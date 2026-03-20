"""
FSM-диалог записи на сеанс.

Флоу:
/start → Записаться
  → выбор услуги
  → выбор мастера
  → выбор даты (дни с доступными слотами)
  → выбор времени (свободные слоты)
  → выбор длительности
  → подтверждение + ссылка на оплату Kaspi
"""

import logging
import random
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from tg_studio.bot.keyboards.booking import (
    ConfirmCB,
    DateCB,
    DurationCB,
    MasterCB,
    ServiceCB,
    TimeCB,
    confirm_kb,
    dates_kb,
    duration_kb,
    duration_range_kb,
    masters_kb,
    services_kb,
    times_kb,
)
from tg_studio.bot.states.booking import BookingFlow
from tg_studio.db.models import (
    Booking,
    BookingStatus,
    Client,
    Master,
    MasterService,
    Payment,
    PaymentStatus,
    Service,
    TimeSlot,
    WorkSchedule,
    ServiceType,
)
from tg_studio.db.session import async_session_factory
from tg_studio.services.payment import freedom_pay_service
from tg_studio.services.slots import get_available_slots

logger = logging.getLogger(__name__)
router = Router(name="booking_flow")

TZ = ZoneInfo("Asia/Almaty")
DAYS_AHEAD = 30  # показываем записи на 30 дней вперёд


# ─── Точка входа ───────────────────────────────────────────────────────────────

@router.callback_query(F.data == "book:start")
async def on_book_start(call: CallbackQuery, state: FSMContext):
    await start_booking(call.message, state)
    await call.answer()


async def start_booking(message: Message, state: FSMContext):
    """Вызывается из хендлера /start."""
    async with async_session_factory() as session:
        result = await session.execute(
            select(Service)
            .join(MasterService, MasterService.service_id == Service.id)
            .where(Service.is_active.is_(True))
            .distinct()
        )
        services = result.scalars().all()

    if not services:
        await message.answer("Пока нет доступных услуг.")
        return

    svc_data = [
        {"id": s.id, "name": s.name, "price": float(s.price), "service_type": s.service_type}
        for s in services
    ]
    await state.set_state(BookingFlow.choosing_service)
    await message.answer("Выберите услугу:", reply_markup=services_kb(svc_data))


# ─── Шаг 1: услуга ─────────────────────────────────────────────────────────────

@router.callback_query(ServiceCB.filter())
async def on_service_chosen(call: CallbackQuery, callback_data: ServiceCB, state: FSMContext):
    async with async_session_factory() as session:
        service = await session.get(Service, callback_data.id)
        result = await session.execute(
            select(Master)
            .join(MasterService, MasterService.master_id == Master.id)
            .where(
                MasterService.service_id == callback_data.id,
                Master.is_active.is_(True),
            )
        )
        masters = result.scalars().all()

    if not masters:
        await call.answer("У этой услуги нет доступных мастеров.", show_alert=True)
        return

    await state.update_data(
        service_id=callback_data.id,
        service_name=service.name,
        price=float(service.price),
        service_type=service.service_type,
        prepayment_percent=service.prepayment_percent,
        cancel_deadline_hours=service.cancel_deadline_hours,
    )
    await state.set_state(BookingFlow.choosing_master)

    data = [{"id": m.id, "full_name": m.full_name} for m in masters]
    await call.message.edit_text(
        f"Услуга: <b>{service.name}</b> — {int(service.price)} тг/ч\n\nВыберите мастера:",
        reply_markup=masters_kb(data),
    )
    await call.answer()


# ─── Шаг 2: мастер ─────────────────────────────────────────────────────────────

@router.callback_query(MasterCB.filter())
async def on_master_chosen(call: CallbackQuery, callback_data: MasterCB, state: FSMContext):
    data = await state.get_data()
    st = data.get("service_type")
    if st == ServiceType.project or st == "project":
        async with async_session_factory() as session:
            master = await session.get(Master, callback_data.id)
        total = float(data["price"])
        prepayment = round(total * data["prepayment_percent"] / 100, 2)
        await state.update_data(
            master_id=callback_data.id,
            master_name=master.full_name,
            duration_hours=None,
            total_amount=total,
            prepayment_amount=prepayment,
            starts_at=None,
        )
        await state.set_state(BookingFlow.confirming)
        await call.message.edit_text(
            f"Подтвердите заказ:\n\n"
            f"Мастер: <b>{master.full_name}</b>\n"
            f"Услуга: <b>{data['service_name']}</b> — {int(total)} тг\n\n"
            f"Предоплата: <b>{int(prepayment)} тг</b>",
            reply_markup=confirm_kb(),
        )
    else:
        async with async_session_factory() as session:
            master = await session.get(Master, callback_data.id)
            today = date.today()
            to_date = today + timedelta(days=DAYS_AHEAD)
            slots = await get_available_slots(session, callback_data.id, today, to_date)

        available_dates = sorted({s["starts_at"][:10] for s in slots})
        if not available_dates:
            await call.answer("Нет доступных дат на ближайшие 30 дней.", show_alert=True)
            return

        await state.update_data(
            master_id=callback_data.id,
            master_name=master.full_name,
        )
        await state.set_state(BookingFlow.choosing_date)

        await call.message.edit_text(
            f"Мастер: <b>{master.full_name}</b>\n"
            f"Услуга: <b>{data['service_name']}</b> — {int(data['price'])} тг/ч\n\n"
            f"На какой день запись?",
            reply_markup=dates_kb(available_dates),
        )
    await call.answer()


# ─── Шаг 3: дата ───────────────────────────────────────────────────────────────

@router.callback_query(BookingFlow.choosing_date, DateCB.filter())
async def on_date_chosen(call: CallbackQuery, callback_data: DateCB, state: FSMContext):
    data = await state.get_data()
    chosen_date = date.fromisoformat(callback_data.value)

    async with async_session_factory() as session:
        slots = await get_available_slots(
            session, data["master_id"], chosen_date, chosen_date
        )

    if not slots:
        await call.answer("На эту дату нет доступных слотов.", show_alert=True)
        return

    await state.update_data(chosen_date=callback_data.value)
    await state.set_state(BookingFlow.choosing_time)

    date_label = chosen_date.strftime("%d.%m.%Y")
    await call.message.edit_text(
        f"Дата: <b>{date_label}</b>\n\nВыберите время начала:",
        reply_markup=times_kb(slots),
    )
    await call.answer()


# ─── Шаг 4: время ──────────────────────────────────────────────────────────────

def _consecutive_slot_ranges(slots: list[dict], chosen_time: str) -> list[tuple[str, str, int]]:
    """
    Слоты на один день, отсортированы по starts_at.
    Найти слот с началом chosen_time (HH:MM) и вернуть диапазоны:
    от него до первого занятого — (start, end, hours) для 1, 2, 3, ... слотов.
    """
    time_part = lambda s: s["starts_at"][11:16]  # "HH:MM"
    idx = None
    for i, s in enumerate(slots):
        if time_part(s) == chosen_time:
            idx = i
            break
    if idx is None:
        return []
    ranges = []
    for k in range(1, len(slots) - idx + 1):
        start_s = slots[idx]["starts_at"]
        end_s = slots[idx + k - 1]["ends_at"]
        start_dt = datetime.fromisoformat(start_s)
        end_dt = datetime.fromisoformat(end_s)
        hours = max(1, int(round((end_dt - start_dt).total_seconds() / 3600)))
        ranges.append((time_part(slots[idx]), end_s[11:16], hours))
    return ranges


@router.callback_query(BookingFlow.choosing_time, TimeCB.filter())
async def on_time_chosen(call: CallbackQuery, callback_data: TimeCB, state: FSMContext):
    data = await state.get_data()
    chosen_date = data["chosen_date"]
    chosen_time = callback_data.value

    async with async_session_factory() as session:
        slots = await get_available_slots(
            session, data["master_id"], date.fromisoformat(chosen_date), date.fromisoformat(chosen_date)
        )

    ranges = _consecutive_slot_ranges(slots, chosen_time)
    if not ranges:
        await call.answer("Это время больше недоступно. Выберите другое.", show_alert=True)
        return

    await state.update_data(chosen_time=chosen_time)
    await state.set_state(BookingFlow.choosing_duration)

    date_label = date.fromisoformat(chosen_date).strftime("%d.%m.%Y")
    await call.message.edit_text(
        f"Мастер: <b>{data['master_name']}</b>\n"
        f"Услуга: <b>{data['service_name']}</b>\n"
        f"Дата: <b>{date_label}</b>\n"
        f"Время начала: <b>{chosen_time}</b>\n\n"
        f"Выберите конец (свободные слоты до первого занятого):",
        reply_markup=duration_range_kb(ranges),
    )
    await call.answer()


# ─── Шаг 5: длительность ───────────────────────────────────────────────────────

@router.callback_query(BookingFlow.choosing_duration, DurationCB.filter())
async def on_duration_chosen(call: CallbackQuery, callback_data: DurationCB, state: FSMContext):
    data = await state.get_data()
    hours = callback_data.hours

    chosen_date = data["chosen_date"]
    chosen_time = data["chosen_time"]
    starts_at_str = f"{chosen_date}T{chosen_time}:00"
    starts_at = datetime.fromisoformat(starts_at_str).replace(tzinfo=TZ)
    ends_at = starts_at + timedelta(hours=hours)

    total = float(data["price"]) * hours
    prepayment = round(total * data["prepayment_percent"] / 100, 2)

    await state.update_data(
        duration_hours=hours,
        total_amount=total,
        prepayment_amount=prepayment,
        starts_at=starts_at.isoformat(),
    )
    await state.set_state(BookingFlow.confirming)

    cancel_deadline = starts_at - timedelta(hours=data["cancel_deadline_hours"])
    now = datetime.now(TZ)
    if cancel_deadline <= now:
        cancel_note = "⚠️ Бесплатная отмена недоступна (поздняя запись)"
    else:
        cancel_note = f"Бесплатная отмена до: {cancel_deadline.strftime('%d.%m %H:%M')}"

    await call.message.edit_text(
        f"Подтвердите запись:\n\n"
        f"Мастер: <b>{data['master_name']}</b>\n"
        f"Услуга: <b>{data['service_name']}</b>\n"
        f"Дата: <b>{starts_at.strftime('%d.%m.%Y')}</b>\n"
        f"Время: <b>{starts_at.strftime('%H:%M')} – {ends_at.strftime('%H:%M')}</b>\n"
        f"Длительность: <b>{hours} ч</b>\n\n"
        f"Итого: <b>{int(total)} тг</b>\n"
        f"Предоплата сейчас: <b>{int(prepayment)} тг</b>\n"
        f"{cancel_note}",
        reply_markup=confirm_kb(),
    )
    await call.answer()


# ─── Шаг 6: подтверждение ──────────────────────────────────────────────────────

@router.callback_query(BookingFlow.confirming, ConfirmCB.filter(F.action == "no"))
async def on_cancel(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("Запись отменена. Напишите /start чтобы начать заново.")
    await call.answer()


@router.callback_query(BookingFlow.confirming, ConfirmCB.filter(F.action == "yes"))
async def on_confirm(call: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    tg_user = call.from_user
    st = data.get("service_type")
    is_project = st == ServiceType.project or st == "project"

    await call.message.edit_text("Создаём запись, подождите...")

    try:
        async with async_session_factory() as session:
            if is_project:
                slot_id = None
                cancel_deadline_at = None
            else:
                starts_at = datetime.fromisoformat(data["starts_at"])
                ends_at = starts_at + timedelta(hours=data["duration_hours"])
                now = datetime.now(TZ)
                cancel_deadline_at = max(
                    starts_at - timedelta(hours=data["cancel_deadline_hours"]),
                    now,
                )
                # БД хранит TIMESTAMP WITHOUT TIME ZONE — передаём naive UTC
                cancel_deadline_at = cancel_deadline_at.astimezone(timezone.utc).replace(tzinfo=None)

                # Повторная проверка пересечений (мог кто-то занять пока выбирали)
                overlap = await session.execute(
                    select(TimeSlot)
                    .join(Booking, Booking.slot_id == TimeSlot.id)
                    .where(
                        TimeSlot.master_id == data["master_id"],
                        Booking.status.in_([BookingStatus.pending, BookingStatus.confirmed]),
                        TimeSlot.starts_at < ends_at,
                        TimeSlot.ends_at > starts_at,
                    )
                )
                if overlap.scalar_one_or_none():
                    await state.clear()
                    await call.message.edit_text(
                        "К сожалению, это время только что заняли.\n"
                        "Напишите /start чтобы выбрать другое время."
                    )
                    await call.answer()
                    return

                # Создать TimeSlot
                slot = TimeSlot(
                    master_id=data["master_id"],
                    starts_at=starts_at,
                    ends_at=ends_at,
                    is_available=False,
                )
                session.add(slot)
                await session.flush()
                slot_id = slot.id

            # Получить или создать клиента
            client_result = await session.execute(
                select(Client).where(Client.telegram_id == tg_user.id)
            )
            client = client_result.scalar_one_or_none()
            if client is None:
                full_name = f"{tg_user.first_name or ''} {tg_user.last_name or ''}".strip()
                client = Client(
                    telegram_id=tg_user.id,
                    username=tg_user.username,
                    full_name=full_name or "Без имени",
                )
                session.add(client)
                await session.flush()

            # Получить master для business_id
            master_result = await session.execute(
                select(Master).where(Master.id == data["master_id"])
            )
            master = master_result.scalar_one_or_none()
            if master is None:
                await state.clear()
                await call.message.edit_text("Мастер не найден. Попробуйте /start")
                await call.answer()
                return

            # Создать Booking
            booking = Booking(
                client_id=client.id,
                master_id=data["master_id"],
                slot_id=slot_id,
                service_id=data["service_id"],
                status=BookingStatus.pending,
                duration_hours=data.get("duration_hours"),
                total_amount=data["total_amount"],
                cancel_deadline_at=cancel_deadline_at,
            )
            session.add(booking)
            await session.flush()

            # Создать заказ Freedom Pay (заглушка: 90% — оплата подтверждена)
            # desc = f"Предоплата: {data['service_name']}"
            # if data.get("duration_hours"):
            #     desc += f" x{data['duration_hours']}ч"
            # fp_order = await freedom_pay_service.create_order(
            #     session,
            #     business_id=master.business_id,
            #     booking_id=booking.id,
            #     amount=data["prepayment_amount"],
            #     description=desc,
            #     user_phone=client.phone,
            # )
            # payment = Payment(
            #     booking_id=booking.id,
            #     gateway_order_id=fp_order["order_id"],
            #     gateway="freedompay",
            #     amount=data["prepayment_amount"],
            #     status=PaymentStatus.pending,
            # )
            # session.add(payment)
            # await session.commit()
            # booking_id = booking.id

            payment_confirmed = random.random() < 0.9
            payment = Payment(
                booking_id=booking.id,
                gateway_order_id=f"stub-{booking.id}",
                gateway="freedompay",
                amount=data["prepayment_amount"],
                status=PaymentStatus.paid if payment_confirmed else PaymentStatus.pending,
            )
            session.add(payment)
            if payment_confirmed:
                # Project: после оплаты — в работу; appointment: подтверждённая запись
                booking.status = BookingStatus.in_progress if is_project else BookingStatus.confirmed
            await session.commit()

            booking_id = booking.id

        # Запланировать автоотмену через 30 минут (только если оплата не подтверждена)
        if not payment_confirmed:
            from tg_studio.tasks.expire_payment import PAYMENT_TIMEOUT_SECONDS, expire_pending_payment
            expire_pending_payment.apply_async(args=[booking_id], countdown=PAYMENT_TIMEOUT_SECONDS)

        await state.clear()
        # Сообщение: со скольки до скольки и общая длительность (для почасовой записи)
        if not is_project and "starts_at" in data and data.get("duration_hours"):
            starts_at = datetime.fromisoformat(data["starts_at"])
            start_str = starts_at.strftime("%H:%M")
            end_str = (starts_at + timedelta(hours=data["duration_hours"])).strftime("%H:%M")
            duration_str = f"{data['duration_hours']} ч"
            time_range_line = f"С {start_str} до {end_str}, общая длительность {duration_str}.\n\n"
        else:
            time_range_line = ""

        if payment_confirmed:
            await call.message.edit_text(
                f"Запись создана!\n\n{time_range_line}Оплата получена. Ждём вас!\n\n"
                f"Вы можете продолжить общение с мастером в этом чате — просто напишите сообщение."
            )
        else:
            await call.message.edit_text(
                f"Запись создана!\n\n{time_range_line}"
                f"Для подтверждения оплатите предоплату:\n"
                f"<b>{int(data['prepayment_amount'])} тг</b>\n\n"
                f"Ссылка для оплаты (заглушка): не выдаётся.\n\n"
                f"После оплаты вы получите подтверждение в этом чате.\n"
                f"Ссылка действительна 30 минут."
            )

    except Exception:
        logger.exception("Ошибка при создании брони для tg_id=%s", tg_user.id)
        await state.clear()
        await call.message.edit_text(
            "Произошла ошибка при создании записи. Попробуйте ещё раз: /start"
        )

    await call.answer()

@router.callback_query()  # ловит все callback
async def debug_cb(call: CallbackQuery, state: FSMContext):
    from tg_studio.bot.handlers.booking_flow import logger
    logger.info("Callback: data=%r, state=%s", call.data, await state.get_state())
    await call.answer()