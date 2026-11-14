"""API коворкинга: места, часы студии, блокировки, брони, календари.

Роли: мастер — читает места/календари, создаёт и правит только СВОИ брони;
владелец — всё + настройка мест/часов/блокировки. Ручных отметок посещения
нет: прошедшая подтверждённая бронь состоялась, присутствие мастер
подтверждает кнопкой в напоминании Telegram (modules/telegram_api.py). Все изменения броней пишутся в booking_events.

Защита от гонок: создание/перенос брони идёт через сервис в одной транзакции
с advisory lock по seat_id (Postgres) — параллельные запросы не плодят дублей.
"""

import logging
from datetime import UTC, date, datetime, time, timedelta

from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy import func, select

from tg_studio.api.admin_deps import (
    AdminBusinessAndSelfDep,
    MasterBusinessAndSelfDep,
    OwnerBusinessDep,
    RequireOwnerOrAdminDep,
)
from tg_studio.api.auth import CurrentUserDep
from tg_studio.api.deps import SessionDep
from tg_studio.db.locks import lock_business_seats
from tg_studio.timeutil import is_valid_window
from tg_studio.db.models import (
    ACTIVE_BOOKING_STATUSES,
    RELEASED_BOOKING_STATUSES,
    Booking,
    BookingEvent,
    Business,
    DiscountRule,
    LedgerEntry,
    Master,
    Payment,
    PaymentStatus,
    Payout,
    PayoutStatus,
    PriceRule,
    Seat,
    SeatBlock,
    Shift,
    StudioAdmin,
    StudioHours,
    Upsell,
    User,
    UserRole,
)
from tg_studio.modules.coworking import money, pricing
from tg_studio.modules.coworking import service as cw
from tg_studio.modules.coworking.schemas import (
    AvailabilityResponse,
    AvailabilitySlot,
    BalanceOut,
    BlockCreate,
    BlockListResponse,
    BlockOut,
    BookingCancel,
    BookingCreate,
    BookingExtend,
    BookingEventOut,
    BookingHistoryResponse,
    BookingListResponse,
    BookingOut,
    BookingPaymentOut,
    BookingPayRequest,
    BookingPayResponse,
    BookingConfirmOut,
    BookingReschedule,
    CalendarEntry,
    CalendarResponse,
    CoverageOut,
    OpsExceptionOut,
    OpsExceptionsResponse,
    PayoutCreate,
    PayoutListResponse,
    PayoutOut,
    ShiftApprove,
    ShiftCreate,
    ShiftListResponse,
    ShiftOut,
    ShiftStartEnd,
    ShiftUpdate,
    UpsellCreate,
    UpsellListResponse,
    UpsellOut,
    UpsellSummary,
    AdminCreate,
    AdminListResponse,
    AdminOut,
    CoworkingSettingsOut,
    CoworkingSettingsUpdate,
    DiscountRuleCreate,
    DiscountRuleListResponse,
    DiscountRuleOut,
    DiscountRuleUpdate,
    LedgerAdjustRequest,
    LedgerEntryOut,
    LedgerListResponse,
    PriceRuleCreate,
    PriceRuleListResponse,
    PriceRuleOut,
    PriceRuleUpdate,
    PricingQuoteRequest,
    PricingQuoteResponse,
    SeatCreate,
    SeatListResponse,
    SeatOut,
    SeatUpdate,
    StudioHoursEntryOut,
    StudioHoursResponse,
    StudioHoursUpdate,
    TopupRequest,
    MyTopupRequest,
    MyTopupResponse,
    TopupResponse,
)

logger = logging.getLogger(__name__)

# Пути верхнего уровня (legacy booking-модуль удалён — /bookings свободен)
router = APIRouter(tags=["coworking"])


# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------


async def _get_seat(session, business_id: int, seat_id: int, *, include_inactive: bool = False) -> Seat:
    result = await session.execute(
        select(Seat).where(Seat.id == seat_id, Seat.business_id == business_id)
    )
    seat = result.scalar_one_or_none()
    if seat is None or (seat.is_active is False and not include_inactive):
        raise HTTPException(status_code=404, detail="Место не найдено")
    return seat


async def _get_booking(session, business_id: int, booking_id: int) -> Booking:
    result = await session.execute(
        select(Booking).where(Booking.id == booking_id, Booking.business_id == business_id)
    )
    booking = result.scalar_one_or_none()
    if booking is None:
        raise HTTPException(status_code=404, detail="Бронь не найдена")
    return booking


async def _booking_payment_out(session, booking: Booking) -> BookingPaymentOut | None:
    """Pending/свежий счёт за бронь (для экрана оплаты)."""
    payment = (
        await session.execute(
            select(Payment)
            .where(Payment.booking_id == booking.id)
            .order_by(Payment.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if payment is None:
        return None
    return BookingPaymentOut(
        id=payment.id,
        amount=float(payment.amount),
        status=payment.status,
        provider=payment.provider,
        payment_url=payment.payment_url,
        expires_at=payment.expires_at,
    )


async def _booking_out(session, booking: Booking, *, with_payment: bool = False) -> BookingOut:
    seat = await session.get(Seat, booking.seat_id)
    master = await session.get(Master, booking.master_id)
    return BookingOut(
        id=booking.id,
        seat_id=booking.seat_id,
        seat_name=seat.name if seat else None,
        master_id=booking.master_id,
        master_name=master.full_name if master else None,
        start=booking.start,
        end=booking.end,
        status=booking.status,
        hold_deadline=booking.hold_deadline,
        price_snapshot=booking.price_snapshot,
        paid_amount=float(booking.paid_amount),
        payment=await _booking_payment_out(session, booking) if with_payment else None,
        requires_confirmation=booking.requires_confirmation,
        confirmed_by_user_id=booking.confirmed_by_user_id,
        presence_confirmed_at=booking.presence_confirmed_at,
        created_at=booking.created_at,
        updated_at=booking.updated_at,
    )


MAX_CALENDAR_RANGE = timedelta(days=62)


def _range_params(from_: datetime, to: datetime) -> tuple[datetime, datetime]:
    from_ = cw.ensure_tz(from_)
    to = cw.ensure_tz(to)
    if to <= from_:
        raise HTTPException(status_code=400, detail="to должен быть позже from")
    return from_, to


def _calendar_range(from_: datetime, to: datetime) -> tuple[datetime, datetime]:
    from_, to = _range_params(from_, to)
    if to - from_ > MAX_CALENDAR_RANGE:
        raise HTTPException(status_code=400, detail=f"Диапазон календаря — не больше {MAX_CALENDAR_RANGE.days} дней")
    return from_, to


# ---------------------------------------------------------------------------
# Места — чтение обеим ролям, запись только владельцу
# ---------------------------------------------------------------------------


@router.get("/seats", response_model=SeatListResponse)
async def list_seats(session: SessionDep, actor: MasterBusinessAndSelfDep, include_inactive: bool = Query(False)):
    business, _ = actor
    stmt = select(Seat).where(Seat.business_id == business.id)
    if not include_inactive:
        stmt = stmt.where(Seat.is_active.is_(True))
    stmt = stmt.order_by(Seat.sort_order, Seat.id)
    rows = (await session.execute(stmt)).scalars().all()
    return SeatListResponse(seats=[SeatOut.model_validate(s) for s in rows])


@router.post("/seats", response_model=SeatOut, status_code=201)
async def create_seat(body: SeatCreate, session: SessionDep, business: OwnerBusinessDep):
    """Новое место. Лимит мест (max_seats, дефолт 4) — из настроек коворкинга."""
    settings = cw.coworking_settings(business)
    await lock_business_seats(session, business.id)
    count = (
        await session.execute(
            select(Seat).where(Seat.business_id == business.id, Seat.is_active.is_(True))
        )
    ).scalars().all()
    if len(count) >= settings["max_seats"]:
        raise HTTPException(
            status_code=409,
            detail=f"Достигнут лимит мест ({settings['max_seats']}) — измените в настройках коворкинга",
        )
    seat = Seat(
        business_id=business.id,
        name=body.name.strip(),
        equipment=body.equipment,
        sort_order=max((x.sort_order for x in count), default=-1) + 1,
    )
    session.add(seat)
    await session.commit()
    await session.refresh(seat)
    return SeatOut.model_validate(seat)


async def _assert_seat_deactivation_allowed(session, seat: Seat) -> None:
    """Место с активными бронями выключать нельзя — сначала отмените их."""
    active = (
        await session.execute(
            select(func.count()).select_from(Booking).where(
                Booking.seat_id == seat.id, Booking.status.in_(ACTIVE_BOOKING_STATUSES)
            )
        )
    ).scalar_one()
    if active:
        raise HTTPException(
            status_code=409,
            detail=f"У места есть активные брони ({active} шт.) — сначала отмените их",
        )


@router.patch("/seats/{seat_id}", response_model=SeatOut)
async def update_seat(
    seat_id: int, body: SeatUpdate, session: SessionDep, business: OwnerBusinessDep
):
    seat = await _get_seat(session, business.id, seat_id, include_inactive=True)
    data = body.model_dump(exclude_unset=True)
    if data.get("is_active") is False and seat.is_active:
        await cw.lock_seat_slot(session, seat.id)
        await _assert_seat_deactivation_allowed(session, seat)
    if data.get("is_active") is True and not seat.is_active:
        await lock_business_seats(session, business.id)
        active_count = (
            await session.execute(
                select(func.count()).select_from(Seat).where(
                    Seat.business_id == business.id, Seat.is_active.is_(True)
                )
            )
        ).scalar_one()
        limit = cw.coworking_settings(business)["max_seats"]
        if active_count >= limit:
            raise HTTPException(
                status_code=409,
                detail=f"Достигнут лимит мест ({limit}) — измените в настройках коворкинга",
            )
    for field, value in data.items():
        setattr(seat, field, value)
    await session.commit()
    await session.refresh(seat)
    return SeatOut.model_validate(seat)


@router.delete("/seats/{seat_id}", status_code=204)
async def delete_seat(seat_id: int, session: SessionDep, business: OwnerBusinessDep):
    """Мягкое удаление. Нельзя удалить место с активными бронями."""
    seat = await _get_seat(session, business.id, seat_id, include_inactive=True)
    await cw.lock_seat_slot(session, seat.id)
    await _assert_seat_deactivation_allowed(session, seat)
    seat.is_active = False
    await session.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Настройки коворкинга + часы работы студии — только владелец
# ---------------------------------------------------------------------------


@router.get("/coworking/settings", response_model=CoworkingSettingsOut)
async def get_coworking_settings(session: SessionDep, actor: MasterBusinessAndSelfDep):
    business, _ = actor
    return CoworkingSettingsOut(**cw.coworking_settings(business))


@router.patch("/coworking/settings", response_model=CoworkingSettingsOut)
async def update_coworking_settings(
    body: CoworkingSettingsUpdate, session: SessionDep, business: OwnerBusinessDep
):
    """Частичное обновление; None-поля не трогаем. max_seats не может быть
    меньше числа уже созданных активных мест."""
    current = dict(business.coworking_settings) if isinstance(business.coworking_settings, dict) else {}
    data = body.model_dump(exclude_unset=True)
    if "max_seats" in data and data["max_seats"] is not None:
        active_count = len(
            (
                await session.execute(
                    select(Seat).where(Seat.business_id == business.id, Seat.is_active.is_(True))
                )
            ).scalars().all()
        )
        if data["max_seats"] < active_count:
            raise HTTPException(
                status_code=409,
                detail=f"max_seats не может быть меньше числа активных мест ({active_count})",
            )
    current.update({k: v for k, v in data.items() if v is not None})
    business.coworking_settings = current
    await session.commit()
    return CoworkingSettingsOut(**cw.coworking_settings(business))


@router.get("/studio-hours", response_model=StudioHoursResponse)
async def get_studio_hours(session: SessionDep, actor: MasterBusinessAndSelfDep):
    business, _ = actor
    rows = (
        await session.execute(
            select(StudioHours)
            .where(StudioHours.business_id == business.id)
            .order_by(StudioHours.weekday, StudioHours.open_time)
        )
    ).scalars().all()
    if not rows:
        # Дефолт: каждый день 10:00–22:00 (как в сервисе)
        entries = [
            StudioHoursEntryOut(weekday=wd, open_time=cw.FALLBACK_OPEN, close_time=cw.FALLBACK_CLOSE)
            for wd in range(7)
        ]
    else:
        entries = [StudioHoursEntryOut.model_validate(r) for r in rows]
    return StudioHoursResponse(entries=entries)


@router.put("/studio-hours", response_model=StudioHoursResponse)
async def put_studio_hours(
    body: StudioHoursUpdate, session: SessionDep, business: OwnerBusinessDep
):
    """Полная замена расписания. Интервал внутри одного дня (open < close,
    close 00:00 = до полуночи). День без интервалов — выходной; пустой список —
    сброс к дефолту (10:00–22:00 каждый день)."""
    # close_time 00:00 = «до полуночи»; иначе открытие раньше закрытия
    by_day: dict[int, list[tuple[datetime, datetime]]] = {}
    for e in body.entries:
        if e.close_time != time(0, 0) and not (e.open_time < e.close_time):
            raise HTTPException(
                status_code=400,
                detail=f"День {e.weekday}: открытие должно быть раньше закрытия",
            )
        by_day.setdefault(e.weekday, []).append(cw.interval_bounds(date(2000, 1, 3), e.open_time, e.close_time))
    for weekday, intervals in by_day.items():
        intervals.sort()
        for (_, prev_end), (next_start, _) in zip(intervals, intervals[1:], strict=False):
            if next_start < prev_end:
                raise HTTPException(
                    status_code=400, detail=f"День {weekday}: интервалы работы пересекаются"
                )
    # Удаляем старые и вставляем новые — полная замена
    old = (
        await session.execute(
            select(StudioHours).where(StudioHours.business_id == business.id)
        )
    ).scalars().all()
    for row in old:
        await session.delete(row)
    for e in body.entries:
        session.add(
            StudioHours(
                business_id=business.id,
                weekday=e.weekday,
                open_time=e.open_time,
                close_time=e.close_time,
            )
        )
    await session.commit()
    return await get_studio_hours(session, (business, None))


# ---------------------------------------------------------------------------
# Календари: место и студия
# ---------------------------------------------------------------------------


async def _calendar_entries(
    session,
    business_id: int,
    from_: datetime,
    to: datetime,
    seat_id: int | None,
    viewer_master_id: int | None = None,
) -> list[CalendarEntry]:
    """Брони + блокировки в диапазоне. seat_id=None — вся студия;
    для календаря места добавляем блокировки всей студии (seat_id IS NULL).

    viewer_master_id — смотрит мастер: чужие брони и блокировки отдаём как
    kind="busy" (только место и часы, без мастера, статуса и причины);
    свои брони — полностью."""
    booking_stmt = (
        select(Booking, Seat.name, Master.full_name)
        .join(Seat, Seat.id == Booking.seat_id)
        .outerjoin(Master, Master.id == Booking.master_id)
        .where(
            Booking.business_id == business_id,
            Booking.start < to,
            Booking.end > from_,
            # отменённые/истёкшие брони место не занимают — в календаре им не место
            Booking.status.notin_(RELEASED_BOOKING_STATUSES),
        )
    )
    if seat_id is not None:
        booking_stmt = booking_stmt.where(Booking.seat_id == seat_id)
    booking_rows = (await session.execute(booking_stmt.order_by(Booking.start))).all()

    block_stmt = select(SeatBlock, Seat.name).outerjoin(Seat, Seat.id == SeatBlock.seat_id).where(
        SeatBlock.business_id == business_id,
        SeatBlock.start < to,
        SeatBlock.end > from_,
    )
    if seat_id is not None:
        block_stmt = block_stmt.where(
            (SeatBlock.seat_id == seat_id) | (SeatBlock.seat_id.is_(None))
        )
    block_rows = (await session.execute(block_stmt.order_by(SeatBlock.start))).all()

    entries = [
        CalendarEntry(
            kind="booking",
            id=b.id,
            seat_id=b.seat_id,
            start=b.start,
            end=b.end,
            status=b.status,
            master_id=b.master_id,
            master_name=master_name,
            seat_name=seat_name,
        )
        for b, seat_name, master_name in booking_rows
    ]
    entries += [
        CalendarEntry(
            kind="block",
            id=b.id,
            seat_id=b.seat_id,
            start=b.start,
            end=b.end,
            reason=b.reason,
            seat_name=seat_name,
        )
        for b, seat_name in block_rows
    ]
    if viewer_master_id is not None:
        own = {b.id for b, _, _ in booking_rows if b.master_id == viewer_master_id}
        entries = [
            e
            if e.kind == "booking" and e.id in own
            else CalendarEntry(
                kind="busy", id=e.id, seat_id=e.seat_id, start=e.start, end=e.end, seat_name=e.seat_name
            )
            for e in entries
        ]
    entries.sort(key=lambda e: (e.start, e.kind))
    return entries


@router.get("/seats/{seat_id}/calendar", response_model=CalendarResponse)
async def seat_calendar(
    seat_id: int,
    session: SessionDep,
    actor: MasterBusinessAndSelfDep,
    from_: datetime = Query(..., alias="from"),
    to: datetime = Query(...),
):
    business, self_master = actor
    await _get_seat(session, business.id, seat_id, include_inactive=True)
    from_, to = _calendar_range(from_, to)
    entries = await _calendar_entries(
        session, business.id, from_, to, seat_id, viewer_master_id=self_master.id if self_master else None
    )
    return CalendarResponse(**{"from": from_, "to": to, "entries": entries})


@router.get("/calendar", response_model=CalendarResponse)
async def studio_calendar(
    session: SessionDep,
    actor: MasterBusinessAndSelfDep,
    from_: datetime = Query(..., alias="from"),
    to: datetime = Query(...),
    seat_id: int | None = Query(None),
):
    """Общий календарь студии (все места), опционально с фильтром по месту."""
    business, self_master = actor
    from_, to = _calendar_range(from_, to)
    entries = await _calendar_entries(
        session, business.id, from_, to, seat_id, viewer_master_id=self_master.id if self_master else None
    )
    return CalendarResponse(**{"from": from_, "to": to, "entries": entries})


# ---------------------------------------------------------------------------
# Доступность и брони
# ---------------------------------------------------------------------------


@router.get("/bookings/availability", response_model=AvailabilityResponse)
async def availability(
    session: SessionDep,
    actor: MasterBusinessAndSelfDep,
    start: datetime = Query(...),
    end: datetime = Query(...),
):
    """Какие места свободны на интервале [start, end)."""
    business, self_master = actor
    start, end = cw.ensure_tz(start), cw.ensure_tz(end)
    if end <= start:
        raise HTTPException(status_code=400, detail="end должен быть позже start")
    settings = cw.coworking_settings(business)
    # те же границы и правило мастера, что и при создании брони
    cw.assert_booking_bounds(start, end, settings, await cw._studio_hours_map(session, business.id))
    if self_master is not None:
        try:
            await cw.assert_master_free(session, business.id, self_master.id, start, end)
        except HTTPException:  # у мастера уже есть бронь на это время — свободных мест для него нет
            return AvailabilityResponse(start=start, end=end, free_seats=[])
    seats = (
        await session.execute(
            select(Seat).where(Seat.business_id == business.id, Seat.is_active.is_(True))
        )
    ).scalars().all()
    free = []
    for seat in seats:
        conflicts = await cw.find_seat_conflicts(
            session, business.id, seat.id, start, end, buffer_minutes=settings["buffer_minutes"]
        )
        if not conflicts:
            free.append(AvailabilitySlot(seat_id=seat.id, seat_name=seat.name))
    return AvailabilityResponse(start=start, end=end, free_seats=free)


@router.get("/bookings", response_model=BookingListResponse)
async def list_bookings(
    session: SessionDep,
    actor: MasterBusinessAndSelfDep,
    seat_id: int | None = Query(None),
    status: str | None = Query(None),
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = Query(None),
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Мастер видит только свои брони; владелец — все. Фильтры: место, статус, диапазон.
    Страницы: limit/offset, в ответе total — всего под фильтр."""
    business, self_master = actor
    stmt = select(Booking).where(Booking.business_id == business.id)
    if self_master is not None:
        stmt = stmt.where(Booking.master_id == self_master.id)
    if seat_id is not None:
        stmt = stmt.where(Booking.seat_id == seat_id)
    if status is not None:
        stmt = stmt.where(Booking.status == status)
    if from_ is not None:
        stmt = stmt.where(Booking.end > cw.ensure_tz(from_))
    if to is not None:
        stmt = stmt.where(Booking.start < cw.ensure_tz(to))
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    stmt = stmt.order_by(Booking.start, Booking.id).limit(limit).offset(offset)
    rows = (await session.execute(stmt)).scalars().all()
    return BookingListResponse(
        bookings=[await _booking_out(session, b) for b in rows], total=total, limit=limit, offset=offset
    )


@router.post("/bookings", response_model=BookingOut, status_code=201)
async def create_booking(
    body: BookingCreate, session: SessionDep, actor: MasterBusinessAndSelfDep, user: CurrentUserDep
):
    """Бронь места: 201 со статусом ожидает_оплаты и hold_deadline либо 409
    с конфликтом. Мастер бронирует на себя; владелец/админ — указывают master_id."""
    business, self_master = actor
    seat = await _get_seat(session, business.id, body.seat_id)
    if self_master is not None:
        master = self_master
    else:
        if body.master_id is None:
            raise HTTPException(status_code=400, detail="Нужно указать master_id (за кого бронь)")
        master = (
            await session.execute(
                select(Master).where(
                    Master.id == body.master_id,
                    Master.business_id == business.id,
                    Master.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()
        if master is None:
            raise HTTPException(status_code=404, detail="Мастер не найден в этом бизнесе")
    booking = await cw.create_booking(
        session,
        business,
        seat,
        master,
        body.start,
        body.end,
        user.id,
        note=body.note,
        # Владелец (действует за площадку) — исключение из правила мастера
        allow_master_overlap=self_master is None,
    )
    await session.commit()
    # внешний счёт Kaspi — после commit, вне блокировок места/мастера
    await money.run_provider_ops(session)
    await session.refresh(booking)
    # 04: вместе с бронью вернём и счёт на предоплату (экран оплаты)
    return await _booking_out(session, booking, with_payment=True)


@router.get("/bookings/{booking_id}", response_model=BookingOut)
async def get_booking(booking_id: int, session: SessionDep, actor: MasterBusinessAndSelfDep):
    business, self_master = actor
    booking = await _get_booking(session, business.id, booking_id)
    if self_master is not None and booking.master_id != self_master.id:
        raise HTTPException(status_code=403, detail="Мастер видит только свои брони")
    return await _booking_out(session, booking)


@router.patch("/bookings/{booking_id}", response_model=BookingOut)
async def reschedule_booking(
    booking_id: int,
    body: BookingReschedule,
    session: SessionDep,
    actor: MasterBusinessAndSelfDep,
    user: CurrentUserDep,
):
    """Перенос/продление брони с полной проверкой конфликтов (исключая саму бронь)."""
    business, self_master = actor
    booking = await _get_booking(session, business.id, booking_id)
    if self_master is not None and booking.master_id != self_master.id:
        raise HTTPException(status_code=403, detail="Мастер правит только свои брони")
    # статус (только неоплаченную) проверяет сервис после блокировки брони
    await cw.reschedule_booking(
        session, business, booking, body.start, body.end, user.id, note=body.note
    )
    await session.commit()
    await money.run_provider_ops(session)
    await session.refresh(booking)
    return await _booking_out(session, booking)


@router.post("/bookings/{booking_id}/extend", response_model=BookingOut)
async def extend_booking(
    booking_id: int,
    body: BookingExtend,
    session: SessionDep,
    actor: MasterBusinessAndSelfDep,
    user: CurrentUserDep,
):
    """Продление на целые часы (конец сдвигается). Оплаченную бронь — с доплатой
    разницы цены с баланса мастера."""
    business, self_master = actor
    booking = await _get_booking(session, business.id, booking_id)
    if self_master is not None and booking.master_id != self_master.id:
        raise HTTPException(status_code=403, detail="Мастер продлевает только свои брони")
    await cw.extend_booking(session, business, booking, body.hours, user.id, note=body.note)
    await session.commit()
    await money.run_provider_ops(session)
    await session.refresh(booking)
    return await _booking_out(session, booking)


@router.post("/bookings/{booking_id}/cancel", response_model=BookingOut)
async def cancel_booking(
    booking_id: int,
    body: BookingCancel,
    session: SessionDep,
    actor: MasterBusinessAndSelfDep,
    user: CurrentUserDep,
):
    """Отмена: мастер — своей брони (отменена_мастером), владелец — любой
    (отменена_площадкой). Правило возврата ≥24ч подключается в 04."""
    business, self_master = actor
    booking = await _get_booking(session, business.id, booking_id)
    if self_master is not None and booking.master_id != self_master.id:
        raise HTTPException(status_code=403, detail="Мастер отменяет только свои брони")
    await cw.cancel_booking(
        session, business, booking, user.id, body.reason, by_studio=self_master is None
    )
    await session.commit()
    await money.run_provider_ops(session)
    await session.refresh(booking)
    return await _booking_out(session, booking)


@router.post("/bookings/{booking_id}/confirm", response_model=BookingConfirmOut)
async def confirm_booking(
    booking_id: int,
    session: SessionDep,
    business: OwnerBusinessDep,
    user: CurrentUserDep,
):
    """Владелец подтверждает бронь вне покрытия (флаг требует_подтверждения)."""
    booking = await _get_booking(session, business.id, booking_id)
    await cw.confirm_out_of_coverage_booking(session, business, booking, user.id)
    await session.commit()
    await session.refresh(booking)
    return BookingConfirmOut(
        booking_id=booking.id,
        requires_confirmation=booking.requires_confirmation,
        confirmed_by_user_id=booking.confirmed_by_user_id,
    )


@router.get("/bookings/{booking_id}/history", response_model=BookingHistoryResponse)
async def booking_history(booking_id: int, session: SessionDep, actor: MasterBusinessAndSelfDep):
    """Append-only история брони (все события по порядку)."""
    business, self_master = actor
    booking = await _get_booking(session, business.id, booking_id)
    if self_master is not None and booking.master_id != self_master.id:
        raise HTTPException(status_code=403, detail="Мастер видит историю только своих броней")
    rows = (
        await session.execute(
            select(BookingEvent)
            .where(BookingEvent.booking_id == booking.id)
            .order_by(BookingEvent.id)
        )
    ).scalars().all()
    return BookingHistoryResponse(events=[BookingEventOut.model_validate(e) for e in rows])


# ---------------------------------------------------------------------------
# Блокировки — владелец (админ в 05)
# ---------------------------------------------------------------------------


@router.get("/blocks", response_model=BlockListResponse)
async def list_blocks(
    session: SessionDep,
    actor: AdminBusinessAndSelfDep,  # причины блокировок мастеру не показываем — ему хватает /calendar
    seat_id: int | None = Query(None),
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = Query(None),
):
    business, _ = actor
    stmt = select(SeatBlock).where(SeatBlock.business_id == business.id)
    if seat_id is not None:
        stmt = stmt.where((SeatBlock.seat_id == seat_id) | (SeatBlock.seat_id.is_(None)))
    if from_ is not None:
        stmt = stmt.where(SeatBlock.end > cw.ensure_tz(from_))
    if to is not None:
        stmt = stmt.where(SeatBlock.start < cw.ensure_tz(to))
    stmt = stmt.order_by(SeatBlock.start)
    rows = (await session.execute(stmt)).scalars().all()
    return BlockListResponse(blocks=[BlockOut.model_validate(b) for b in rows])


@router.post("/blocks", response_model=BlockOut, status_code=201)
async def create_block(
    body: BlockCreate, session: SessionDep, business: OwnerBusinessDep, user: CurrentUserDep
):
    """Блокировка места (или всей студии при seat_id=None). Блокировка не
    может пересекать активные брони этого места — 409 с конфликтом."""
    start, end = cw.ensure_tz(body.start), cw.ensure_tz(body.end)
    if end <= start:
        raise HTTPException(status_code=400, detail="end должен быть позже start")
    if body.seat_id is not None:
        await _get_seat(session, business.id, body.seat_id)
        seat_ids = [body.seat_id]
    else:
        seat_ids = [
            s.id
            for s in (
                await session.execute(
                    select(Seat).where(Seat.business_id == business.id, Seat.is_active.is_(True))
                )
            ).scalars().all()
        ]
    # Блокировка конфликтует с активными бронями — проверяем по каждому месту
    # (локи в порядке id: параллельная бронь того же места ждёт, а не проскакивает)
    for sid in sorted(seat_ids):
        await cw.lock_seat_slot(session, sid)
    for sid in seat_ids:
        conflicts = await cw.find_seat_conflicts(session, business.id, sid, start, end)
        booking_conflicts = [c for c in conflicts if c[0] == "booking"]
        if booking_conflicts:
            await cw.assert_seat_free(session, business.id, sid, start, end)
    block = SeatBlock(
        business_id=business.id,
        seat_id=body.seat_id,
        start=start,
        end=end,
        reason=body.reason.strip(),
        created_by_user_id=user.id,
    )
    session.add(block)
    await session.commit()
    await session.refresh(block)
    return BlockOut.model_validate(block)


@router.delete("/blocks/{block_id}", status_code=204)
async def delete_block(block_id: int, session: SessionDep, business: OwnerBusinessDep):
    result = await session.execute(
        select(SeatBlock).where(SeatBlock.id == block_id, SeatBlock.business_id == business.id)
    )
    block = result.scalar_one_or_none()
    if block is None:
        raise HTTPException(status_code=404, detail="Блокировка не найдена")
    await session.delete(block)
    await session.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Ценовые правила — чтение обеим ролям, запись только владельцу (03)
# ---------------------------------------------------------------------------


async def _get_price_rule(session, business_id: int, rule_id: int) -> PriceRule:
    rule = (
        await session.execute(
            select(PriceRule).where(PriceRule.id == rule_id, PriceRule.business_id == business_id)
        )
    ).scalar_one_or_none()
    if rule is None:
        raise HTTPException(status_code=404, detail="Ценовое правило не найдено")
    return rule


async def _get_discount_rule(session, business_id: int, rule_id: int) -> DiscountRule:
    rule = (
        await session.execute(
            select(DiscountRule).where(DiscountRule.id == rule_id, DiscountRule.business_id == business_id)
        )
    ).scalar_one_or_none()
    if rule is None:
        raise HTTPException(status_code=404, detail="Скидка не найдена")
    return rule


async def _check_rule_seat(session, business: Business, seat_id: int | None) -> None:
    if seat_id is not None:
        await _get_seat(session, business.id, seat_id, include_inactive=True)


@router.get("/price-rules", response_model=PriceRuleListResponse)
async def list_price_rules(
    session: SessionDep,
    actor: MasterBusinessAndSelfDep,
    seat_id: int | None = Query(None),
    include_inactive: bool = Query(False),
):
    """Ценовые правила студии (фильтр по месту — его правила + «все места»)."""
    business, _ = actor
    stmt = select(PriceRule).where(PriceRule.business_id == business.id)
    if seat_id is not None:
        stmt = stmt.where((PriceRule.seat_id == seat_id) | (PriceRule.seat_id.is_(None)))
    if not include_inactive:
        stmt = stmt.where(PriceRule.is_active.is_(True))
    stmt = stmt.order_by(PriceRule.priority.desc(), PriceRule.id.desc())
    rows = (await session.execute(stmt)).scalars().all()
    return PriceRuleListResponse(rules=[PriceRuleOut.model_validate(r) for r in rows])


@router.post("/price-rules", response_model=PriceRuleOut, status_code=201)
async def create_price_rule(body: PriceRuleCreate, session: SessionDep, business: OwnerBusinessDep):
    await _check_rule_seat(session, business, body.seat_id)
    rule = PriceRule(
        business_id=business.id,
        seat_id=body.seat_id,
        name=body.name.strip(),
        day_kind=body.day_kind,
        specific_date=body.specific_date,
        time_from=body.time_from,
        time_to=body.time_to,
        hourly_rate=body.hourly_rate,
        priority=body.priority,
        is_active=body.is_active,
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    return PriceRuleOut.model_validate(rule)


@router.patch("/price-rules/{rule_id}", response_model=PriceRuleOut)
async def update_price_rule(
    rule_id: int, body: PriceRuleUpdate, session: SessionDep, business: OwnerBusinessDep
):
    rule = await _get_price_rule(session, business.id, rule_id)
    data = body.model_dump(exclude_unset=True)
    if "seat_id" in data:
        await _check_rule_seat(session, business, data["seat_id"])
    for field, value in data.items():
        setattr(rule, field, value)
    if not is_valid_window(rule.time_from, rule.time_to):
        raise HTTPException(status_code=400, detail="time_from должен быть раньше time_to (или time_to=00:00 — до полуночи)")
    if rule.day_kind == "дата" and rule.specific_date is None:
        raise HTTPException(status_code=400, detail="Для day_kind='дата' укажите specific_date")
    await session.commit()
    await session.refresh(rule)
    return PriceRuleOut.model_validate(rule)


@router.delete("/price-rules/{rule_id}", status_code=204)
async def delete_price_rule(rule_id: int, session: SessionDep, business: OwnerBusinessDep):
    """Удаление жёсткое: снимки броней хранят применённые правила текстом,
    ссылка на правило в снимке — только informational (rule_id/rule_name)."""
    rule = await _get_price_rule(session, business.id, rule_id)
    await session.delete(rule)
    await session.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Скидки — чтение обеим ролям, запись только владельцу (03)
# ---------------------------------------------------------------------------


@router.get("/discount-rules", response_model=DiscountRuleListResponse)
async def list_discount_rules(
    session: SessionDep,
    actor: MasterBusinessAndSelfDep,
    include_inactive: bool = Query(False),
):
    business, _ = actor
    stmt = select(DiscountRule).where(DiscountRule.business_id == business.id)
    if not include_inactive:
        stmt = stmt.where(DiscountRule.is_active.is_(True))
    stmt = stmt.order_by(DiscountRule.priority.desc(), DiscountRule.id.desc())
    rows = (await session.execute(stmt)).scalars().all()
    return DiscountRuleListResponse(rules=[DiscountRuleOut.model_validate(r) for r in rows])


@router.post("/discount-rules", response_model=DiscountRuleOut, status_code=201)
async def create_discount_rule(body: DiscountRuleCreate, session: SessionDep, business: OwnerBusinessDep):
    await _check_rule_seat(session, business, body.seat_id)
    rule = DiscountRule(
        business_id=business.id,
        seat_id=body.seat_id,
        name=body.name.strip(),
        kind=body.kind,
        min_hours=body.min_hours,
        percent=body.percent,
        fixed_amount=body.fixed_amount,
        priority=body.priority,
        is_active=body.is_active,
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    return DiscountRuleOut.model_validate(rule)


@router.patch("/discount-rules/{rule_id}", response_model=DiscountRuleOut)
async def update_discount_rule(
    rule_id: int, body: DiscountRuleUpdate, session: SessionDep, business: OwnerBusinessDep
):
    rule = await _get_discount_rule(session, business.id, rule_id)
    data = body.model_dump(exclude_unset=True)
    if "seat_id" in data:
        await _check_rule_seat(session, business, data["seat_id"])
    for field, value in data.items():
        setattr(rule, field, value)
    if rule.min_hours is None:
        raise HTTPException(status_code=400, detail="Для kind='min_hours' укажите min_hours")
    if (rule.percent is None) == (rule.fixed_amount is None):
        raise HTTPException(status_code=400, detail="Задайте ровно одно: percent или fixed_amount")
    await session.commit()
    await session.refresh(rule)
    return DiscountRuleOut.model_validate(rule)


@router.delete("/discount-rules/{rule_id}", status_code=204)
async def delete_discount_rule(rule_id: int, session: SessionDep, business: OwnerBusinessDep):
    rule = await _get_discount_rule(session, business.id, rule_id)
    await session.delete(rule)
    await session.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Калькулятор цены (quote) — обе роли, бронь не создаёт (03)
# ---------------------------------------------------------------------------


@router.post("/pricing/quote", response_model=PricingQuoteResponse)
async def pricing_quote(
    body: PricingQuoteRequest, session: SessionDep, actor: MasterBusinessAndSelfDep
):
    """Сколько будет стоить место на интервале по актуальным правилам.

    Мастер видит расчёт для себя; владелец может указать master_id, чтобы
    применились резидентские скидки этого мастера. Без цены на интервал — 409.
    """
    business, self_master = actor
    seat = await _get_seat(session, business.id, body.seat_id)
    master = self_master
    if self_master is None and body.master_id is not None:
        master = (
            await session.execute(
                select(Master).where(
                    Master.id == body.master_id,
                    Master.business_id == business.id,
                    Master.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()
        if master is None:
            raise HTTPException(status_code=404, detail="Мастер не найден в этом бизнесе")
    start, end = cw.ensure_tz(body.start), cw.ensure_tz(body.end)
    if end <= start:
        raise HTTPException(status_code=400, detail="end должен быть позже start")
    snapshot = await pricing.quote_booking_price(
        session, business.id, seat.id, start, end, master=master
    )
    return PricingQuoteResponse(**snapshot)


# ---------------------------------------------------------------------------
# Деньги коворкинга (04): оплата брони, баланс мастера, журнал проводок
# ---------------------------------------------------------------------------


async def _get_own_booking(
    session, business_id: int, booking_id: int, self_master: Master | None
) -> Booking:
    booking = await _get_booking(session, business_id, booking_id)
    if self_master is not None and booking.master_id != self_master.id:
        raise HTTPException(status_code=403, detail="Мастер оплачивает только свои брони")
    return booking


@router.post("/bookings/{booking_id}/pay", response_model=BookingPayResponse)
async def pay_booking(
    booking_id: int,
    body: BookingPayRequest,
    session: SessionDep,
    actor: MasterBusinessAndSelfDep,
    user: CurrentUserDep,
):
    """Оплата брони (100% предоплата, 04).

    method=balance — списать с баланса мастера (сразу → подтверждена).
    method=card — вернуть счёт apipay (ссылка на оплату); бронь подтвердится
    вебхуком после оплаты. Если apipay не подключён — 409 (счёт ручной,
    подтвердит владелец/админ).
    """
    business, self_master = actor
    booking = await _get_own_booking(session, business.id, booking_id, self_master)
    if body.method not in ("card", "balance"):
        raise HTTPException(status_code=400, detail="method: card | balance")
    price = money.booking_price(booking)

    if body.method == "balance":
        await money.pay_booking_from_balance(session, business, booking, user.id)
        await session.commit()
        await money.run_provider_ops(session)
        await session.refresh(booking)
        balance = await money.master_balance(session, booking.master_id)
        return BookingPayResponse(
            booking_id=booking.id,
            status=booking.status,
            paid_amount=float(booking.paid_amount),
            price=float(price),
            payment_url=None,
            balance=float(balance),
            currency=money.CURRENCY,
        )

    # card: нужен apipay-счёт на бронь
    if booking.status != "ожидает_оплаты":
        raise HTTPException(
            status_code=409, detail=f"Оплатить картой можно только ожидающую оплаты бронь (сейчас: {booking.status})"
        )
    payment = (
        await session.execute(
            select(Payment).where(
                Payment.booking_id == booking.id,
                Payment.status == PaymentStatus.pending.value,
            )
        )
    ).scalar_one_or_none()
    if payment is None:
        raise HTTPException(status_code=409, detail="Счёт на оплату не найден — бронь уже оплачена или истекла")
    if payment.provider != "apipay" or not payment.payment_url:
        raise HTTPException(
            status_code=409,
            detail="Онлайн-оплата (Kaspi) не подключена для этого счёта — оплатите с баланса или наличными у администратора",
        )
    await session.commit()
    return BookingPayResponse(
        booking_id=booking.id,
        status=booking.status,
        paid_amount=float(booking.paid_amount),
        price=float(price),
        payment_url=payment.payment_url,
        balance=None,
        currency=money.CURRENCY,
    )


@router.get("/me/balance", response_model=BalanceOut)
async def my_balance(session: SessionDep, actor: MasterBusinessAndSelfDep):
    """Остаток счёта мастера = Σ проводок (вычисляется). Владелец — 403 (у него нет счёта мастера)."""
    business, self_master = actor
    if self_master is None:
        raise HTTPException(status_code=403, detail="Баланс счёта есть только у мастера")
    balance = await money.master_balance(session, self_master.id)
    return BalanceOut(master_id=self_master.id, balance=float(balance), currency=money.CURRENCY)


@router.post("/me/topups", response_model=MyTopupResponse, status_code=201)
async def create_my_topup(
    body: MyTopupRequest, session: SessionDep, actor: MasterBusinessAndSelfDep, user: CurrentUserDep
):
    """Счёт на пополнение своего баланса (Kaspi): ссылка на оплату; баланс вырастет вебхуком."""
    from decimal import Decimal

    business, self_master = actor
    if self_master is None:
        raise HTTPException(status_code=403, detail="Баланс есть только у мастера")
    payment = await money.create_topup_invoice(
        session, business, self_master, Decimal(str(body.amount)), user.id
    )
    await session.commit()
    return MyTopupResponse(
        payment_id=payment.id,
        amount=float(payment.amount),
        payment_url=payment.payment_url,
        expires_at=payment.expires_at,
        currency=money.CURRENCY,
    )


@router.get("/me/ledger", response_model=LedgerListResponse)
async def my_ledger(
    session: SessionDep,
    actor: MasterBusinessAndSelfDep,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Выписка по счёту мастера (своя)."""
    business, self_master = actor
    if self_master is None:
        raise HTTPException(status_code=403, detail="Выписка есть только у мастера")
    total = (
        await session.execute(
            select(func.count()).select_from(LedgerEntry).where(LedgerEntry.master_id == self_master.id)
        )
    ).scalar_one()
    rows = (
        await session.execute(
            select(LedgerEntry, Master.full_name)
            .outerjoin(Master, Master.id == LedgerEntry.master_id)
            .where(LedgerEntry.master_id == self_master.id)
            .order_by(LedgerEntry.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return LedgerListResponse(
        entries=[
            LedgerEntryOut(
                id=e.id, master_id=e.master_id, master_name=mn, kind=e.kind,
                amount=float(e.amount), currency=e.currency, payment_id=e.payment_id,
                booking_id=e.booking_id, actor_user_id=e.actor_user_id, note=e.note,
                created_at=e.created_at,
            )
            for e, mn in rows
        ],
        total=total,
    )


@router.get("/ledger", response_model=LedgerListResponse)
async def list_ledger(
    session: SessionDep,
    actor: AdminBusinessAndSelfDep,
    master_id: int | None = Query(None),
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """Журнал проводок бизнеса (владелец и администратор, 05). Фильтры: мастер, диапазон."""
    business, _ = actor
    stmt = (
        select(LedgerEntry, Master.full_name)
        .outerjoin(Master, Master.id == LedgerEntry.master_id)
        .where(LedgerEntry.business_id == business.id)
    )
    if master_id is not None:
        stmt = stmt.where(LedgerEntry.master_id == master_id)
    if from_ is not None:
        stmt = stmt.where(LedgerEntry.created_at >= cw.ensure_tz(from_))
    if to is not None:
        stmt = stmt.where(LedgerEntry.created_at < cw.ensure_tz(to))
    total = (
        await session.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))
    ).scalar_one()
    rows = (
        await session.execute(stmt.order_by(LedgerEntry.id.desc()).limit(limit).offset(offset))
    ).all()
    return LedgerListResponse(
        entries=[
            LedgerEntryOut(
                id=e.id, master_id=e.master_id, master_name=mn, kind=e.kind,
                amount=float(e.amount), currency=e.currency, payment_id=e.payment_id,
                booking_id=e.booking_id, actor_user_id=e.actor_user_id, note=e.note,
                created_at=e.created_at,
            )
            for e, mn in rows
        ],
        total=total,
    )


@router.get("/masters/{master_id}/balance", response_model=BalanceOut)
async def master_balance(
    master_id: int, session: SessionDep, actor: AdminBusinessAndSelfDep
):
    """Остаток счёта конкретного мастера (владелец/админ — обзорно, 05)."""
    business, _ = actor
    master = (
        await session.execute(
            select(Master).where(Master.id == master_id, Master.business_id == business.id)
        )
    ).scalar_one_or_none()
    if master is None:
        raise HTTPException(status_code=404, detail="Мастер не найден в этом бизнесе")
    balance = await money.master_balance(session, master.id)
    return BalanceOut(master_id=master.id, balance=float(balance), currency=money.CURRENCY)


@router.post("/masters/{master_id}/topup", response_model=TopupResponse, status_code=201)
async def topup_master(
    master_id: int,
    body: TopupRequest,
    session: SessionDep,
    actor: AdminBusinessAndSelfDep,
    user: CurrentUserDep,
):
    """Ручное пополнение баланса мастера (наличные/перевод; владелец или админ, 05)."""
    business, _ = actor
    from decimal import Decimal

    master = (
        await session.execute(
            select(Master).where(Master.id == master_id, Master.business_id == business.id)
        )
    ).scalar_one_or_none()
    if master is None:
        raise HTTPException(status_code=404, detail="Мастер не найден в этом бизнесе")
    payment = await money.manual_topup(
        session, business, master, Decimal(str(body.amount)), user.id, body.note
    )
    await session.commit()
    balance = await money.master_balance(session, master.id)
    return TopupResponse(payment_id=payment.id, balance=float(balance), currency=money.CURRENCY)


@router.post("/masters/{master_id}/ledger/adjust", response_model=LedgerEntryOut, status_code=201)
async def adjust_master_balance(
    master_id: int,
    body: LedgerAdjustRequest,
    session: SessionDep,
    business: OwnerBusinessDep,
    user: CurrentUserDep,
):
    """Ручная корректировка баланса мастера: основание обязательно (только владелец)."""
    from decimal import Decimal

    master = (
        await session.execute(
            select(Master).where(Master.id == master_id, Master.business_id == business.id)
        )
    ).scalar_one_or_none()
    if master is None:
        raise HTTPException(status_code=404, detail="Мастер не найден в этом бизнесе")
    entry = await money.adjust_balance(
        session, business, master, Decimal(str(body.amount)), user.id, body.reason
    )
    await session.commit()
    await session.refresh(entry)
    master_name = master.full_name
    return LedgerEntryOut(
        id=entry.id, master_id=entry.master_id, master_name=master_name, kind=entry.kind,
        amount=float(entry.amount), currency=entry.currency, payment_id=entry.payment_id,
        booking_id=entry.booking_id, actor_user_id=entry.actor_user_id, note=entry.note,
        created_at=entry.created_at,
    )


# ---------------------------------------------------------------------------
# Администраторы студии — владелец назначает/снимает (05)
# ---------------------------------------------------------------------------


async def _get_admin(session, business_id: int, admin_id: int) -> StudioAdmin:
    admin = (
        await session.execute(
            select(StudioAdmin).where(
                StudioAdmin.id == admin_id, StudioAdmin.business_id == business_id
            )
        )
    ).scalar_one_or_none()
    if admin is None:
        raise HTTPException(status_code=404, detail="Администратор не найден")
    return admin


@router.get("/admins", response_model=AdminListResponse)
async def list_admins(session: SessionDep, business: OwnerBusinessDep):
    rows = (
        await session.execute(
            select(StudioAdmin)
            .where(StudioAdmin.business_id == business.id)
            .order_by(StudioAdmin.id)
        )
    ).scalars().all()
    return AdminListResponse(admins=[AdminOut.model_validate(a) for a in rows])


@router.post("/admins", response_model=AdminOut, status_code=201)
async def create_admin(
    body: AdminCreate, session: SessionDep, business: OwnerBusinessDep, user: CurrentUserDep
):
    """Назначить администратора по telegram_id. User создаётся/находится
    автоматически, роль становится admin (PG enum — миграция c3d4e5f6a7b8)."""
    existing = (
        await session.execute(
            select(StudioAdmin).where(
                StudioAdmin.business_id == business.id,
                StudioAdmin.telegram_id == body.telegram_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None and existing.is_active:
        raise HTTPException(status_code=409, detail="Этот администратор уже добавлен")

    # User: найти по telegram_id или создать; роль — admin
    uresult = await session.execute(select(User).where(User.telegram_id == body.telegram_id))
    u = uresult.scalar_one_or_none()
    if u is None:
        u = User(
            telegram_id=body.telegram_id,
            first_name=body.full_name.strip()[:128],
            role=UserRole.admin,
        )
        session.add(u)
        await session.flush()
    else:
        u.role = UserRole.admin
        u.is_active = True

    if existing is not None:
        existing.is_active = True
        existing.full_name = body.full_name.strip()
        existing.user_id = u.id
        admin = existing
    else:
        admin = StudioAdmin(
            business_id=business.id,
            user_id=u.id,
            telegram_id=body.telegram_id,
            full_name=body.full_name.strip(),
        )
        session.add(admin)
    await session.commit()
    await session.refresh(admin)
    return AdminOut.model_validate(admin)


@router.delete("/admins/{admin_id}", status_code=204)
async def delete_admin(admin_id: int, session: SessionDep, business: OwnerBusinessDep):
    """Снять администратора (мягко): роль user остаётся, но вход по initData
    перестаёт резолвиться в admin."""
    admin = await _get_admin(session, business.id, admin_id)
    admin.is_active = False
    await session.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Смены администраторов (05)
# ---------------------------------------------------------------------------


async def _shift_out(session, shift: Shift) -> ShiftOut:
    admin = await session.get(StudioAdmin, shift.admin_id)
    return ShiftOut(
        id=shift.id,
        business_id=shift.business_id,
        admin_id=shift.admin_id,
        admin_name=admin.full_name if admin else None,
        planned_start=shift.planned_start,
        planned_end=shift.planned_end,
        started_at=shift.started_at,
        ended_at=shift.ended_at,
        approved_hours=float(shift.approved_hours) if shift.approved_hours is not None else None,
        approved_by_user_id=shift.approved_by_user_id,
        approved_at=shift.approved_at,
        note=shift.note,
        created_by_user_id=shift.created_by_user_id,
        created_at=shift.created_at,
        updated_at=shift.updated_at,
    )


@router.get("/shifts", response_model=ShiftListResponse)
async def list_shifts(
    session: SessionDep,
    actor: AdminBusinessAndSelfDep,
    admin_id: int | None = Query(None),
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = Query(None),
):
    """Смены: админ видит свои, владелец — все. Фильтры: админ, диапазон плана."""
    business, self_admin = actor
    stmt = select(Shift).where(Shift.business_id == business.id)
    if self_admin is not None:
        stmt = stmt.where(Shift.admin_id == self_admin.id)
    elif admin_id is not None:
        stmt = stmt.where(Shift.admin_id == admin_id)
    if from_ is not None:
        stmt = stmt.where(Shift.planned_end > cw.ensure_tz(from_))
    if to is not None:
        stmt = stmt.where(Shift.planned_start < cw.ensure_tz(to))
    rows = (await session.execute(stmt.order_by(Shift.planned_start))).scalars().all()
    return ShiftListResponse(shifts=[await _shift_out(session, s) for s in rows])


@router.post("/shifts", response_model=ShiftOut, status_code=201)
async def create_shift(
    body: ShiftCreate,
    session: SessionDep,
    actor: AdminBusinessAndSelfDep,
    user: CurrentUserDep,
):
    """План смены. Админ — только на себя; владелец — на любого админа."""
    business, self_admin = actor
    if self_admin is not None:
        if body.admin_id != self_admin.id:
            raise HTTPException(status_code=403, detail="Админ создаёт смену только на себя")
        admin = self_admin
    else:
        admin = await _get_admin(session, business.id, body.admin_id)
        if not admin.is_active:
            raise HTTPException(status_code=404, detail="Администратор неактивен")
    start, end = cw.ensure_tz(body.planned_start), cw.ensure_tz(body.planned_end)
    if end <= start:
        raise HTTPException(status_code=400, detail="Конец смены должен быть позже начала")
    shift = Shift(
        business_id=business.id,
        admin_id=admin.id,
        planned_start=start,
        planned_end=end,
        note=body.note,
        created_by_user_id=user.id,
    )
    session.add(shift)
    await session.commit()
    await session.refresh(shift)
    return await _shift_out(session, shift)


@router.patch("/shifts/{shift_id}", response_model=ShiftOut)
async def update_shift(
    shift_id: int,
    body: ShiftUpdate,
    session: SessionDep,
    actor: AdminBusinessAndSelfDep,
    user: CurrentUserDep,
):
    business, self_admin = actor
    shift = (
        await session.execute(
            select(Shift).where(Shift.id == shift_id, Shift.business_id == business.id)
        )
    ).scalar_one_or_none()
    if shift is None:
        raise HTTPException(status_code=404, detail="Смена не найдена")
    if self_admin is not None and shift.admin_id != self_admin.id:
        raise HTTPException(status_code=403, detail="Админ правит только свои смены")
    if body.planned_start is not None:
        shift.planned_start = cw.ensure_tz(body.planned_start)
    if body.planned_end is not None:
        shift.planned_end = cw.ensure_tz(body.planned_end)
    if shift.planned_end <= shift.planned_start:
        raise HTTPException(status_code=400, detail="Конец смены должен быть позже начала")
    if body.note is not None:
        shift.note = body.note
    await session.commit()
    await session.refresh(shift)
    return await _shift_out(session, shift)


@router.post("/shifts/{shift_id}/start", response_model=ShiftOut)
async def start_shift(
    shift_id: int,
    session: SessionDep,
    actor: AdminBusinessAndSelfDep,
    user: CurrentUserDep,
):
    """Фактическое начало смены (админ — своя смена, владелец — любая)."""
    business, self_admin = actor
    shift = (
        await session.execute(
            select(Shift).where(Shift.id == shift_id, Shift.business_id == business.id)
        )
    ).scalar_one_or_none()
    if shift is None:
        raise HTTPException(status_code=404, detail="Смена не найдена")
    if self_admin is not None and shift.admin_id != self_admin.id:
        raise HTTPException(status_code=403, detail="Админ начинает только свою смену")
    if shift.started_at is not None:
        raise HTTPException(status_code=409, detail="Смена уже начата")
    shift.started_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(shift)
    return await _shift_out(session, shift)


@router.post("/shifts/{shift_id}/end", response_model=ShiftOut)
async def end_shift(
    shift_id: int,
    session: SessionDep,
    actor: AdminBusinessAndSelfDep,
    user: CurrentUserDep,
):
    """Фактический конец смены."""
    business, self_admin = actor
    shift = (
        await session.execute(
            select(Shift).where(Shift.id == shift_id, Shift.business_id == business.id)
        )
    ).scalar_one_or_none()
    if shift is None:
        raise HTTPException(status_code=404, detail="Смена не найдена")
    if self_admin is not None and shift.admin_id != self_admin.id:
        raise HTTPException(status_code=403, detail="Админ завершает только свою смену")
    if shift.started_at is None:
        raise HTTPException(status_code=409, detail="Сначала начните смену")
    if shift.ended_at is not None:
        raise HTTPException(status_code=409, detail="Смена уже завершена")
    shift.ended_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(shift)
    return await _shift_out(session, shift)


@router.patch("/shifts/{shift_id}/approve", response_model=ShiftOut)
async def approve_shift(
    shift_id: int,
    body: ShiftApprove,
    session: SessionDep,
    business: OwnerBusinessDep,
    user: CurrentUserDep,
):
    """Владелец утверждает оплачиваемые часы смены."""
    shift = (
        await session.execute(
            select(Shift).where(Shift.id == shift_id, Shift.business_id == business.id)
        )
    ).scalar_one_or_none()
    if shift is None:
        raise HTTPException(status_code=404, detail="Смена не найдена")
    shift.approved_hours = float(body.approved_hours)
    shift.approved_by_user_id = user.id
    shift.approved_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(shift)
    return await _shift_out(session, shift)


# ---------------------------------------------------------------------------
# Покрытие студии сменами (05)
# ---------------------------------------------------------------------------


@router.get("/coverage", response_model=CoverageOut)
async def coverage(
    session: SessionDep,
    actor: AdminBusinessAndSelfDep,
    from_: datetime = Query(..., alias="from"),
    to: datetime = Query(...),
):
    """Интервалы покрытия студии (слитые) и часы покрытия на [from, to)."""
    business, _ = actor
    from_, to = _range_params(from_, to)
    intervals = await cw.admin_coverage_intervals(session, business.id, from_, to)
    hours = await cw.coverage_hours(session, business.id, from_, to)
    return CoverageOut(
        **{
            "from": from_,
            "to": to,
            "intervals": [{"start": s, "end": e} for s, e in intervals],
            "hours": hours,
        }
    )


# ---------------------------------------------------------------------------
# Апсейлы (05) — фиксация продаж, оформленных админом
# ---------------------------------------------------------------------------


async def _upsell_out(session, upsell: Upsell) -> UpsellOut:
    admin = await session.get(StudioAdmin, upsell.admin_id)
    return UpsellOut(
        id=upsell.id,
        business_id=upsell.business_id,
        admin_id=upsell.admin_id,
        admin_name=admin.full_name if admin else None,
        sale_id=upsell.sale_id,
        master_id=upsell.master_id,
        buyer_type="master" if upsell.master_id is not None else "client",
        buyer_name=upsell.buyer_name,
        buyer_phone=upsell.buyer_phone,
        amount=float(upsell.amount),
        currency=upsell.currency,
        booking_id=upsell.booking_id,
        note=upsell.note,
        created_by_user_id=upsell.created_by_user_id,
        created_at=upsell.created_at,
    )


@router.get("/upsells", response_model=UpsellListResponse)
async def list_upsells(
    session: SessionDep,
    actor: AdminBusinessAndSelfDep,
    admin_id: int | None = Query(None),
    buyer_type: str | None = Query(None, pattern="^(master|client)$"),
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = Query(None),
    limit: int = Query(200, ge=1, le=1000),
):
    """Апсейлы студии. Админ видит свои; владелец — все (или фильтр по админу)."""
    business, self_admin = actor
    stmt = select(Upsell).where(Upsell.business_id == business.id)
    if self_admin is not None:
        stmt = stmt.where(Upsell.admin_id == self_admin.id)
    elif admin_id is not None:
        stmt = stmt.where(Upsell.admin_id == admin_id)
    if buyer_type == "master":
        stmt = stmt.where(Upsell.master_id.is_not(None))
    elif buyer_type == "client":
        stmt = stmt.where(Upsell.master_id.is_(None))
    if from_ is not None:
        stmt = stmt.where(Upsell.created_at >= cw.ensure_tz(from_))
    if to is not None:
        stmt = stmt.where(Upsell.created_at < cw.ensure_tz(to))
    rows = (await session.execute(stmt.order_by(Upsell.id.desc()).limit(limit))).scalars().all()
    return UpsellListResponse(
        upsells=[await _upsell_out(session, u) for u in rows], total=len(rows)
    )


@router.post("/upsells", response_model=UpsellOut, status_code=201)
async def create_upsell(
    body: UpsellCreate,
    session: SessionDep,
    actor: AdminBusinessAndSelfDep,
    user: CurrentUserDep,
):
    """Зафиксировать апсейл. Админ — только на себя; владелец — на любого админа."""
    from tg_studio.modules.coworking.pricing import CURRENCY

    business, self_admin = actor
    if self_admin is not None:
        # Админ оформляет апсейл на себя (автор — он)
        admin = self_admin
    else:
        # Владелец фиксирует апсейл на указанного админа
        if body.admin_id is None:
            raise HTTPException(status_code=400, detail="Укажите admin_id (за кем фиксируем апсейл)")
        admin = await _get_admin(session, business.id, body.admin_id)
        if not admin.is_active:
            raise HTTPException(status_code=404, detail="Администратор неактивен")
    if body.master_id is not None:
        m = (
            await session.execute(
                select(Master).where(
                    Master.id == body.master_id,
                    Master.business_id == business.id,
                    Master.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()
        if m is None:
            raise HTTPException(status_code=404, detail="Мастер не найден в этом бизнесе")
    if body.booking_id is not None:
        await _get_booking(session, business.id, body.booking_id)
    upsell = Upsell(
        business_id=business.id,
        admin_id=admin.id,
        master_id=body.master_id,
        buyer_name=body.buyer_name,
        buyer_phone=body.buyer_phone,
        amount=body.amount,
        currency=CURRENCY,
        booking_id=body.booking_id,
        note=body.note,
        created_by_user_id=user.id,
    )
    session.add(upsell)
    await session.commit()
    await session.refresh(upsell)
    return await _upsell_out(session, upsell)


@router.get("/upsells/summary", response_model=list[UpsellSummary])
async def upsell_summary(
    session: SessionDep,
    business: OwnerBusinessDep,
    from_: datetime = Query(..., alias="from"),
    to: datetime = Query(...),
):
    """Сводка по апсейлам каждого админа за период (для доначисления по правилу %)."""
    from_, to = _range_params(from_, to)
    rows = (
        await session.execute(
            select(
                Upsell.admin_id,
                StudioAdmin.full_name,
                func.count(Upsell.id),
                func.coalesce(func.sum(Upsell.amount), 0),
            )
            .join(StudioAdmin, StudioAdmin.id == Upsell.admin_id)
            .where(
                Upsell.business_id == business.id,
                Upsell.created_at >= from_,
                Upsell.created_at < to,
            )
            .group_by(Upsell.admin_id, StudioAdmin.full_name)
            .order_by(func.sum(Upsell.amount).desc())
        )
    ).all()
    from tg_studio.modules.coworking.pricing import CURRENCY

    return [
        UpsellSummary(
            admin_id=aid, admin_name=name, **{"from": from_, "to": to},
            count=cnt, total_amount=float(total), currency=CURRENCY,
        )
        for aid, name, cnt, total in rows
    ]


# ---------------------------------------------------------------------------
# Выплаты (доначисления) админам — владелец (05)
# ---------------------------------------------------------------------------


async def _payout_out(session, payout: Payout) -> PayoutOut:
    admin = await session.get(StudioAdmin, payout.admin_id)
    return PayoutOut(
        id=payout.id,
        business_id=payout.business_id,
        admin_id=payout.admin_id,
        admin_name=admin.full_name if admin else None,
        amount=float(payout.amount),
        currency=payout.currency,
        status=payout.status,
        percent=float(payout.percent) if payout.percent is not None else None,
        period_from=payout.period_from,
        period_to=payout.period_to,
        reason=payout.reason,
        created_by_user_id=payout.created_by_user_id,
        paid_by_user_id=payout.paid_by_user_id,
        paid_at=payout.paid_at,
        created_at=payout.created_at,
    )


@router.get("/payouts", response_model=PayoutListResponse)
async def list_payouts(
    session: SessionDep,
    actor: AdminBusinessAndSelfDep,
    admin_id: int | None = Query(None),
    status: str | None = Query(None),
):
    """Доначисления. Админ видит свои; владелец — все. Фильтр по статусу."""
    business, self_admin = actor
    stmt = select(Payout).where(Payout.business_id == business.id)
    if self_admin is not None:
        stmt = stmt.where(Payout.admin_id == self_admin.id)
    elif admin_id is not None:
        stmt = stmt.where(Payout.admin_id == admin_id)
    if status is not None:
        stmt = stmt.where(Payout.status == status)
    rows = (await session.execute(stmt.order_by(Payout.id.desc()))).scalars().all()
    return PayoutListResponse(payouts=[await _payout_out(session, p) for p in rows], total=len(rows))


@router.post("/payouts", response_model=PayoutOut, status_code=201)
async def create_payout(
    body: PayoutCreate,
    session: SessionDep,
    business: OwnerBusinessDep,
    user: CurrentUserDep,
):
    """Начислить доначисление админу: фиксированная сумма ИЛИ % от его апсейлов
    за период (percent + from + to — сумма считается по upsells)."""
    from decimal import Decimal

    from tg_studio.modules.coworking.pricing import CURRENCY

    admin = await _get_admin(session, business.id, body.admin_id)
    if not admin.is_active:
        raise HTTPException(status_code=404, detail="Администратор неактивен")

    amount: Decimal
    period_from = cw.ensure_tz(body.from_) if body.from_ is not None else None
    period_to = cw.ensure_tz(body.to) if body.to is not None else None
    if body.amount is not None:
        amount = Decimal(str(body.amount))
    else:
        # % от апсейлов админа за период
        total = (
            await session.execute(
                select(func.coalesce(func.sum(Upsell.amount), 0)).where(
                    Upsell.business_id == business.id,
                    Upsell.admin_id == admin.id,
                    Upsell.created_at >= period_from,
                    Upsell.created_at < period_to,
                )
            )
        ).scalar_one()
        amount = (Decimal(str(total)) * Decimal(str(body.percent))).quantize(Decimal("0.01"))
        if amount <= 0:
            raise HTTPException(
                status_code=409,
                detail="За период нет апсейлов — доначисление по % равно нулю",
            )
    payout = Payout(
        business_id=business.id,
        admin_id=admin.id,
        amount=float(amount),
        currency=CURRENCY,
        status=PayoutStatus.accrued.value,
        percent=body.percent,
        period_from=period_from,
        period_to=period_to,
        reason=body.reason.strip(),
        created_by_user_id=user.id,
    )
    session.add(payout)
    await session.commit()
    await session.refresh(payout)
    return await _payout_out(session, payout)


@router.post("/payouts/{payout_id}/mark-paid", response_model=PayoutOut)
async def mark_payout_paid(
    payout_id: int,
    session: SessionDep,
    business: OwnerBusinessDep,
    user: CurrentUserDep,
):
    """Отметить доначисление выплаченным."""
    payout = (
        await session.execute(
            select(Payout).where(Payout.id == payout_id, Payout.business_id == business.id)
        )
    ).scalar_one_or_none()
    if payout is None:
        raise HTTPException(status_code=404, detail="Доначисление не найдено")
    if payout.status == PayoutStatus.paid.value:
        raise HTTPException(status_code=409, detail="Доначисление уже выплачено")
    payout.status = PayoutStatus.paid.value
    payout.paid_by_user_id = user.id
    payout.paid_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(payout)
    return await _payout_out(session, payout)


# ---------------------------------------------------------------------------
# Операционные исключения — админ/владелец (05)
# ---------------------------------------------------------------------------


@router.get("/ops/exceptions", response_model=OpsExceptionsResponse)
async def ops_exceptions(
    session: SessionDep,
    actor: AdminBusinessAndSelfDep,
):
    """Неявки, истекшие hold, неподтверждённые брони вне покрытия — список для админа/владельца."""
    business, _ = actor
    items = await cw.ops_exceptions(session, business)
    return OpsExceptionsResponse(
        exceptions=[OpsExceptionOut(**i) for i in items]
    )
