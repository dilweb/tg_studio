"""Напоминания мастерам в Telegram (beat каждые 5 минут).

- «оплатите»: бронь ожидает_оплаты, до дедлайна оплаты осталось не больше
  payment_reminder_minutes — «оплатите до ЧЧ:ММ, иначе место освободится»;
- «скоро бронь»: подтверждённая бронь начнётся не позже чем через
  booking_reminder_minutes — с inline-кнопкой «Подтвердить» (присутствие,
  обрабатывает modules/telegram_api.py → service.confirm_presence).

Каждое напоминание — не больше одного раза: бронь «забирается» условным
UPDATE … WHERE *_sent_at IS NULL (два параллельных свипа не задвоят) и
коммитится ДО отправки. Сбой Telegram (бот заблокирован, нет чата) — лог,
повтора нет: лучше пропустить напоминание, чем заспамить. Мастер без
telegram id — лог, бронь отмечается обработанной.
"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from tg_studio.db.models import Booking, BookingStatus, Business, Master, Payment, PaymentStatus, Seat, User
from tg_studio.modules import notify
from tg_studio.modules.coworking.service import coworking_settings
from tg_studio.timeutil import STUDIO_TZ

logger = logging.getLogger(__name__)

PRESENCE_CALLBACK_PREFIX = "presence"


def presence_callback_data(booking_id: int) -> str:
    return f"{PRESENCE_CALLBACK_PREFIX}:{booking_id}"


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)  # SQLite отдаёт naive UTC


async def master_telegram_id(session, master_id: int) -> int | None:
    """Куда слать мастеру: masters.telegram_id, иначе telegram_id его пользователя."""
    master = await session.get(Master, master_id)
    if master is None:
        return None
    if master.telegram_id:
        return master.telegram_id
    user = await session.get(User, master.user_id) if master.user_id else None
    return user.telegram_id if user else None


async def _claim(session, booking_id: int, column, status: str) -> bool:
    """Забрать бронь под отправку: True — эта транзакция первая и статус прежний."""
    result = await session.execute(
        update(Booking)
        .where(Booking.id == booking_id, column.is_(None), Booking.status == status)
        .values({column.key: datetime.now(UTC)})
    )
    await session.commit()
    return result.rowcount == 1


async def _due(session, business: Business, now: datetime) -> tuple[list[Booking], list[Booking]]:
    cfg = coworking_settings(business)
    pay_horizon = now + timedelta(minutes=cfg["payment_reminder_minutes"])
    start_horizon = now + timedelta(minutes=cfg["booking_reminder_minutes"])
    unpaid = (
        await session.execute(
            select(Booking).where(
                Booking.business_id == business.id,
                Booking.status == BookingStatus.pending_payment.value,
                Booking.payment_reminder_sent_at.is_(None),
                Booking.hold_deadline > now,
                Booking.hold_deadline <= pay_horizon,
            )
        )
    ).scalars().all()
    upcoming = (
        await session.execute(
            select(Booking).where(
                Booking.business_id == business.id,
                Booking.status == BookingStatus.confirmed.value,
                Booking.booking_reminder_sent_at.is_(None),
                Booking.start > now,
                Booking.start <= start_horizon,
            )
        )
    ).scalars().all()
    return list(unpaid), list(upcoming)


async def _payment_text(session, booking: Booking, seat_name: str) -> str:
    start = _utc(booking.start).astimezone(STUDIO_TZ)
    end = _utc(booking.end).astimezone(STUDIO_TZ)
    deadline = _utc(booking.hold_deadline).astimezone(STUDIO_TZ)
    text = (
        f"Бронь места «{seat_name}» на {start:%d.%m %H:%M}–{end:%H:%M} не оплачена. "
        f"Оплатите до {deadline:%d.%m %H:%M}, иначе место освободится."
    )
    url = (
        await session.execute(
            select(Payment.payment_url).where(
                Payment.booking_id == booking.id,
                Payment.status == PaymentStatus.pending.value,
                Payment.payment_url.is_not(None),
            )
        )
    ).scalars().first()
    return f"{text}\nОплатить: {url}" if url else text


def _booking_text(booking: Booking, seat_name: str, now: datetime) -> str:
    start = _utc(booking.start).astimezone(STUDIO_TZ)
    end = _utc(booking.end).astimezone(STUDIO_TZ)
    minutes = max(1, int((_utc(booking.start) - now).total_seconds() // 60))
    return (
        f"Через {minutes} мин бронь места «{seat_name}»: {start:%d.%m %H:%M}–{end:%H:%M}. "
        "Подтвердите, что будете на месте."
    )


async def send_due_reminders(session) -> dict[str, int]:
    """Разослать причитающиеся напоминания. Коммитит сам. Возвращает счётчики отправленных."""
    now = datetime.now(UTC)
    jobs: list[tuple[str, Booking, str]] = []
    for business in (await session.execute(select(Business))).scalars().all():
        unpaid, upcoming = await _due(session, business, now)
        for booking in unpaid:
            seat = await session.get(Seat, booking.seat_id)
            jobs.append(("payment", booking, seat.name if seat else str(booking.seat_id)))
        for booking in upcoming:
            seat = await session.get(Seat, booking.seat_id)
            jobs.append(("booking", booking, seat.name if seat else str(booking.seat_id)))
    await session.commit()

    sent = {"payment": 0, "booking": 0}
    for kind, booking, seat_name in jobs:
        column = Booking.payment_reminder_sent_at if kind == "payment" else Booking.booking_reminder_sent_at
        status = BookingStatus.pending_payment.value if kind == "payment" else BookingStatus.confirmed.value
        try:
            if not await _claim(session, booking.id, column, status):
                continue
            chat_id = await master_telegram_id(session, booking.master_id)
            if chat_id is None:
                logger.info("Reminder %s for booking %s skipped: master has no telegram id", kind, booking.id)
                continue
            if kind == "payment":
                await notify.send_text_to_telegram(chat_id, await _payment_text(session, booking, seat_name))
            else:
                await notify.send_text_with_button(
                    chat_id, _booking_text(booking, seat_name, now), "Подтвердить",
                    presence_callback_data(booking.id),
                )
            sent[kind] += 1
        except Exception:
            logger.exception("Reminder %s for booking %s failed", kind, booking.id)
            await session.rollback()
    await session.commit()
    return sent
