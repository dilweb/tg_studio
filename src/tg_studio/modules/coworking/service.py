"""Сервис коворкинга: места, часы студии, доступность, брони, hold/TTL.

Инварианты (docs/coworking/02-seats-bookings.md):
- активные брони (ожидает_оплаты/подтверждена/начата) одного места не
  пересекаются, блокировки — тоже занятые интервалы;
- проверка + вставка в одной транзакции: advisory lock по seat_id
  (db/locks.lock_seat_slot) ДО проверки — на Postgres; на SQLite (тесты)
  одна транзакция за раз, lock — no-op;
- границы: start<end, мин. длительность и шаг из настроек студии, бронь
  целиком внутри часов работы, буфер между бронями;
- мастер не держит две активные пересекающиеся брони (владелец — может);
- hold истёк → истекла_оплата (expire_booking_holds, beat каждые 5 мин).

Время: фронт шлёт naive ISO — считаем временем студии (Asia/Almaty), как в
tattoo/api.py._ensure_tz. В БД — aware UTC (колонки timestamptz / UTCDateTime).
"""

import logging
from datetime import UTC, date, datetime, time, timedelta

from fastapi import HTTPException
from sqlalchemy import select

from tg_studio.db.locks import lock_seat_slot
from tg_studio.db.models import (
    ACTIVE_BOOKING_STATUSES,
    Booking,
    BookingEvent,
    BookingEventKind,
    BookingStatus,
    Business,
    Master,
    Seat,
    SeatBlock,
    StudioHours,
)
from tg_studio.modules.coworking import pricing
from tg_studio.timeutil import STUDIO_TZ

# money импортируем лениво внутри функций: money сам импортирует хелперы
# отсюда (add_booking_event, coworking_settings) — top-level импорт дал бы цикл

logger = logging.getLogger(__name__)

# Дефолты настроек коворкинга (переопределяются в businesses.coworking_settings)
DEFAULT_MAX_SEATS = 4
DEFAULT_MIN_DURATION_MINUTES = 60
DEFAULT_STEP_MINUTES = 30
DEFAULT_BUFFER_MINUTES = 0
DEFAULT_HOLD_TTL_HOURS = 24
# Правило отмены (03/04): отмена раньше чем за N часов до начала → 100% возврат;
# позже → удержание no_show_hold_percent процентов (остальное возвращается)
DEFAULT_CANCEL_FULL_REFUND_HOURS = 24
DEFAULT_NO_SHOW_HOLD_PERCENT = 100
# Если в studio_hours нет строк — студия работает каждый день 10:00–22:00
FALLBACK_OPEN = time(10, 0)
FALLBACK_CLOSE = time(22, 0)


def ensure_tz(dt: datetime) -> datetime:
    """Наивное время считаем временем студии (фронт шлёт ISO без зоны)."""
    return dt.replace(tzinfo=STUDIO_TZ) if dt.tzinfo is None else dt


def coworking_settings(business: Business) -> dict:
    """Эффективные настройки: дефолты + переопределения из БД."""
    cfg = business.coworking_settings if isinstance(business.coworking_settings, dict) else {}
    return {
        "max_seats": int(cfg.get("max_seats", DEFAULT_MAX_SEATS)),
        "min_duration_minutes": int(cfg.get("min_duration_minutes", DEFAULT_MIN_DURATION_MINUTES)),
        "step_minutes": int(cfg.get("step_minutes", DEFAULT_STEP_MINUTES)),
        "buffer_minutes": int(cfg.get("buffer_minutes", DEFAULT_BUFFER_MINUTES)),
        "hold_ttl_hours": int(cfg.get("hold_ttl_hours", DEFAULT_HOLD_TTL_HOURS)),
        "cancel_full_refund_hours": int(
            cfg.get("cancel_full_refund_hours", DEFAULT_CANCEL_FULL_REFUND_HOURS)
        ),
        "no_show_hold_percent": int(
            cfg.get("no_show_hold_percent", DEFAULT_NO_SHOW_HOLD_PERCENT)
        ),
    }


async def _studio_hours_map(session, business_id: int) -> dict[int, list[tuple[time, time]]]:
    rows = (
        await session.execute(
            select(StudioHours).where(StudioHours.business_id == business_id)
        )
    ).scalars().all()
    result: dict[int, list[tuple[time, time]]] = {}
    for row in rows:
        result.setdefault(row.weekday, []).append((row.open_time, row.close_time))
    return result


def _assert_within_studio_hours(
    start: datetime, end: datetime, hours_map: dict[int, list[tuple[time, time]]]
) -> None:
    """Бронь целиком внутри часов работы студии (по студийному времени)."""
    local_start = start.astimezone(STUDIO_TZ)
    local_end = end.astimezone(STUDIO_TZ)
    # Интервал внутри одного дня (часы студии не пересекают полночь)
    if local_end.date() != local_start.date():
        raise HTTPException(
            status_code=400,
            detail="Бронь должна быть в пределах одного дня работы студии",
        )
    intervals = hours_map.get(local_start.weekday())
    if not intervals:
        intervals = [(FALLBACK_OPEN, FALLBACK_CLOSE)]
    st, en = local_start.time(), local_end.time()
    if not any(open_t <= st and en <= close_t for open_t, close_t in intervals):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Время {st.strftime('%H:%M')}–{en.strftime('%H:%M')} вне часов работы студии"
            ),
        )


def assert_booking_bounds(
    start: datetime, end: datetime, settings: dict, hours_map: dict[int, list[tuple[time, time]]]
) -> None:
    """Границы брони: start<end, мин. длительность, шаг, часы работы студии."""
    if end <= start:
        raise HTTPException(status_code=400, detail="Конец брони должен быть позже начала")
    duration = end - start
    min_dur = timedelta(minutes=settings["min_duration_minutes"])
    if duration < min_dur:
        raise HTTPException(
            status_code=400,
            detail=f"Минимальная длительность брони — {settings['min_duration_minutes']} мин",
        )
    step = timedelta(minutes=settings["step_minutes"])
    if duration % step != timedelta(0):
        raise HTTPException(
            status_code=400,
            detail=f"Длительность брони — кратно {settings['step_minutes']} мин",
        )
    _assert_within_studio_hours(start, end, hours_map)


async def find_seat_conflicts(
    session,
    business_id: int,
    seat_id: int,
    start: datetime,
    end: datetime,
    *,
    buffer_minutes: int = 0,
    exclude_booking_id: int | None = None,
) -> list[tuple[str, int, datetime, datetime]]:
    """Конфликты места на [start, end): активные брони + блокировки (этого
    места или всей студии), с учётом буфера между бронями.

    Возвращает [(kind, id, conflict_start, conflict_end)] — для 409-деталей.
    """
    buffer = timedelta(minutes=buffer_minutes)
    q_start, q_end = start - buffer, end + buffer

    booking_stmt = select(Booking).where(
        Booking.business_id == business_id,
        Booking.seat_id == seat_id,
        Booking.status.in_(ACTIVE_BOOKING_STATUSES),
        Booking.start < q_end,
        Booking.end > q_start,
    )
    if exclude_booking_id is not None:
        booking_stmt = booking_stmt.where(Booking.id != exclude_booking_id)
    bookings = (await session.execute(booking_stmt)).scalars().all()

    block_stmt = select(SeatBlock).where(
        SeatBlock.business_id == business_id,
        SeatBlock.start < q_end,
        SeatBlock.end > q_start,
        # блокировка либо этого места, либо всей студии (seat_id IS NULL)
        (SeatBlock.seat_id == seat_id) | (SeatBlock.seat_id.is_(None)),
    )
    blocks = (await session.execute(block_stmt)).scalars().all()

    conflicts = [
        ("booking", b.id, b.start, b.end) for b in bookings
    ] + [
        ("block", b.id, b.start, b.end) for b in blocks
    ]
    conflicts.sort(key=lambda c: c[2])
    return conflicts


async def assert_seat_free(
    session,
    business_id: int,
    seat_id: int,
    start: datetime,
    end: datetime,
    *,
    buffer_minutes: int = 0,
    exclude_booking_id: int | None = None,
) -> None:
    """409 с описанием конфликта, если место занято на интервале."""
    conflicts = await find_seat_conflicts(
        session,
        business_id,
        seat_id,
        start,
        end,
        buffer_minutes=buffer_minutes,
        exclude_booking_id=exclude_booking_id,
    )
    if not conflicts:
        return
    parts = []
    for kind, cid, c_start, c_end in conflicts:
        label = f"бронь #{cid}" if kind == "booking" else f"блокировка #{cid}"
        parts.append(f"{label} {c_start.astimezone(STUDIO_TZ):%d.%m %H:%M}–{c_end.astimezone(STUDIO_TZ):%H:%M}")
    raise HTTPException(
        status_code=409,
        detail={
            "message": "Место занято на этом интервале",
            "conflicts": [
                {"kind": kind, "id": cid, "start": c_start.isoformat(), "end": c_end.isoformat()}
                for kind, cid, c_start, c_end in conflicts
            ],
            "text": "; ".join(parts),
        },
    )


async def assert_master_free(
    session,
    business_id: int,
    master_id: int,
    start: datetime,
    end: datetime,
    *,
    exclude_booking_id: int | None = None,
) -> None:
    """Мастер не держит две активные пересекающиеся брони (любые места)."""
    stmt = select(Booking).where(
        Booking.business_id == business_id,
        Booking.master_id == master_id,
        Booking.status.in_(ACTIVE_BOOKING_STATUSES),
        Booking.start < end,
        Booking.end > start,
    )
    if exclude_booking_id is not None:
        stmt = stmt.where(Booking.id != exclude_booking_id)
    existing = (await session.execute(stmt)).scalars().all()
    if existing:
        b = existing[0]
        raise HTTPException(
            status_code=409,
            detail=(
                f"У мастера уже есть бронь на пересекающееся время: "
                f"#{b.id} {b.start.astimezone(STUDIO_TZ):%d.%m %H:%M}–{b.end.astimezone(STUDIO_TZ):%H:%M}"
            ),
        )


def add_booking_event(
    session,
    business_id: int,
    booking_id: int | None,
    kind: BookingEventKind,
    actor_user_id: int | None,
    details: dict | None = None,
) -> None:
    session.add(
        BookingEvent(
            business_id=business_id,
            booking_id=booking_id,
            kind=kind.value,
            actor_user_id=actor_user_id,
            details=details,
        )
    )


async def create_booking(
    session,
    business: Business,
    seat: Seat,
    master: Master,
    start: datetime,
    end: datetime,
    created_by_user_id: int,
    note: str | None = None,
    *,
    allow_master_overlap: bool = False,
) -> Booking:
    """Создание брони в одной транзакции: lock по месту → проверки → вставка.

    Возвращает созданную бронь (статус ожидает_оплаты, hold_deadline =
    now + hold_ttl_hours). Вызывающий коммитит транзакцию.

    allow_master_overlap — исключение из инварианта «одна активная бронь
    мастера на пересекающемся времени»: для действий площадки (владелец/админ
    создают бронь за мастера, напр. при ручном распределении).
    """
    start = ensure_tz(start)
    end = ensure_tz(end)
    settings = coworking_settings(business)
    hours_map = await _studio_hours_map(session, business.id)

    await lock_seat_slot(session, seat.id)
    assert_booking_bounds(start, end, settings, hours_map)
    await assert_seat_free(
        session,
        business.id,
        seat.id,
        start,
        end,
        buffer_minutes=settings["buffer_minutes"],
    )
    if not allow_master_overlap:
        await assert_master_free(session, business.id, master.id, start, end)

    # Цена: снимок расчёта по актуальным правилам (03). Без цены на интервал — 409.
    snapshot = await pricing.quote_booking_price(
        session, business.id, seat.id, start, end, master=master
    )

    # 05: бронь вне покрытия смен админов — флаг «требует подтверждения владельцем»
    outside = await booking_outside_coverage(session, business, start, end)

    now = datetime.now(UTC)
    booking = Booking(
        business_id=business.id,
        seat_id=seat.id,
        master_id=master.id,
        start=start,
        end=end,
        status=BookingStatus.pending_payment.value,
        hold_deadline=now + timedelta(hours=settings["hold_ttl_hours"]),
        price_snapshot=snapshot,
        paid_amount=0,
        requires_confirmation=outside,
        created_by_user_id=created_by_user_id,
    )
    session.add(booking)
    await session.flush()
    # 04: сразу счёт на 100% стоимости (предоплата) — бронь оплатят по нему
    from tg_studio.modules.coworking import money

    await money.create_booking_invoice(session, business, booking, created_by_user_id)
    add_booking_event(
        session,
        business.id,
        booking.id,
        BookingEventKind.created,
        created_by_user_id,
        {
            "seat_id": seat.id,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "hold_deadline": booking.hold_deadline.isoformat(),
            "note": note,
            "price_final": snapshot["final"],
            "price_base": snapshot["base"],
            "price_discount": snapshot["discount"],
            "currency": snapshot["currency"],
            "requires_confirmation": outside,
        },
    )
    return booking


async def reschedule_booking(
    session,
    business: Business,
    booking: Booking,
    new_start: datetime,
    new_end: datetime,
    actor_user_id: int,
    note: str | None = None,
) -> Booking:
    """Перенос/продление брони: та же проверка места (исключая саму бронь)."""
    new_start = ensure_tz(new_start)
    new_end = ensure_tz(new_end)
    settings = coworking_settings(business)
    hours_map = await _studio_hours_map(session, business.id)

    await lock_seat_slot(session, booking.seat_id)
    assert_booking_bounds(new_start, new_end, settings, hours_map)
    await assert_seat_free(
        session,
        business.id,
        booking.seat_id,
        new_start,
        new_end,
        buffer_minutes=settings["buffer_minutes"],
        exclude_booking_id=booking.id,
    )
    await assert_master_free(
        session, business.id, booking.master_id, new_start, new_end,
        exclude_booking_id=booking.id,
    )

    old = {"start": booking.start.isoformat(), "end": booking.end.isoformat()}
    old_snapshot = booking.price_snapshot
    booking.start = new_start
    booking.end = new_end
    # Перенос/продление → новый расчёт цены по актуальным правилам (03);
    # старый снимок остаётся в событии «перенесена»
    master = await session.get(Master, booking.master_id)
    new_snapshot = await pricing.quote_booking_price(
        session, business.id, booking.seat_id, new_start, new_end, master=master
    )
    booking.price_snapshot = new_snapshot
    add_booking_event(
        session,
        business.id,
        booking.id,
        BookingEventKind.rescheduled,
        actor_user_id,
        {
            "old": old,
            "new": {"start": new_start.isoformat(), "end": new_end.isoformat()},
            "note": note,
            "old_price_snapshot": old_snapshot,
            "new_price_snapshot": new_snapshot,
        },
    )
    add_booking_event(
        session,
        business.id,
        booking.id,
        BookingEventKind.priced,
        actor_user_id,
        {"price_final": new_snapshot["final"], "currency": new_snapshot["currency"]},
    )
    return booking


async def cancel_booking(
    session,
    business: Business,
    booking: Booking,
    actor_user_id: int,
    reason: str | None,
    *,
    by_studio: bool = False,
) -> Booking:
    """Отмена активной брони → терминальный статус + событие. Слот свободен.

    04: если бронь была оплачена (подтверждена/начата) — возврат по правилу
    отмены (≥ cancel_full_refund_hours до начала → 100%, позже → удержание
    no_show_hold_percent %; площадка — всегда 100%). Проводка refund в ledger.
    """
    if booking.status not in ACTIVE_BOOKING_STATUSES:
        raise HTTPException(status_code=409, detail=f"Бронь уже в терминальном статусе: {booking.status}")
    from tg_studio.modules.coworking import money

    booking.status = (
        BookingStatus.cancelled_by_studio.value if by_studio else BookingStatus.cancelled_by_master.value
    )
    if booking.paid_amount and booking.paid_amount > 0:
        await money.apply_cancellation_refund(
            session, business, booking, actor_user_id, by_studio=by_studio, reason=reason
        )
    add_booking_event(
        session,
        business.id,
        booking.id,
        BookingEventKind.cancelled,
        actor_user_id,
        {"reason": reason, "by_studio": by_studio, "status": booking.status},
    )
    return booking


async def start_booking(session, business: Business, booking: Booking, actor_user_id: int) -> Booking:
    """Отметка «начата» (владелец/админ в 05; статус + фактическое время)."""
    if booking.status != BookingStatus.confirmed.value:
        raise HTTPException(
            status_code=409,
            detail=f"Начать можно только подтверждённую бронь (сейчас: {booking.status})",
        )
    booking.status = BookingStatus.started.value
    booking.started_at = datetime.now(UTC)
    add_booking_event(session, business.id, booking.id, BookingEventKind.started, actor_user_id)
    return booking


async def complete_booking(session, business: Business, booking: Booking, actor_user_id: int) -> Booking:
    """Отметка «завершена» (владелец/админ в 05)."""
    if booking.status != BookingStatus.started.value:
        raise HTTPException(
            status_code=409,
            detail=f"Завершить можно только начатую бронь (сейчас: {booking.status})",
        )
    booking.status = BookingStatus.completed.value
    booking.completed_at = datetime.now(UTC)
    add_booking_event(session, business.id, booking.id, BookingEventKind.completed, actor_user_id)
    return booking


async def mark_no_show(session, business: Business, booking: Booking, actor_user_id: int) -> Booking:
    """Неявка (владелец/админ в 05): активная бронь → неявка.

    04: удержание no_show_hold_percent % от оплаченной суммы (refund остатка).
    """
    if booking.status not in ACTIVE_BOOKING_STATUSES:
        raise HTTPException(status_code=409, detail=f"Бронь уже в терминальном статусе: {booking.status}")
    from tg_studio.modules.coworking import money

    booking.status = BookingStatus.no_show.value
    if booking.paid_amount and booking.paid_amount > 0:
        # Неявка = удержание по правилу (как поздняя отмена), площадка не виновата
        await money.apply_cancellation_refund(
            session, business, booking, actor_user_id, by_studio=False, reason="неявка"
        )
    add_booking_event(session, business.id, booking.id, BookingEventKind.no_show, actor_user_id)
    return booking


async def expire_booking_holds(session) -> int:
    """Beat-задача: ожидает_оплаты с истёкшим hold_deadline → истекла_оплата.

    Место освобождается (статус терминальный, не участвует в пересечениях).
    Возвращает число истёкших броней. Вызывающий коммитит.
    """
    from tg_studio.modules.coworking import money

    now = datetime.now(UTC)
    stale = (
        await session.execute(
            select(Booking).where(
                Booking.status == BookingStatus.pending_payment.value,
                Booking.hold_deadline.isnot(None),
                Booking.hold_deadline < now,
            )
        )
    ).scalars().all()
    for booking in stale:
        booking.status = BookingStatus.payment_expired.value
        # 04: pending-счёт за бронь → expired (место свободно, деньги не списаны)
        await money.expire_booking_payment(session, booking)
        add_booking_event(
            session,
            booking.business_id,
            booking.id,
            BookingEventKind.payment_expired,
            None,
            {"hold_deadline": booking.hold_deadline.isoformat()},
        )
    return len(stale)


def free_seats_for_interval(
    all_seats: list[Seat],
    conflicts_by_seat: dict[int, list],
    start: datetime,
    end: datetime,
) -> list[Seat]:
    """Чистая функция: какие места свободны на [start, end) по уже найденным
    конфликтам (для availability-эндпоинта)."""
    return [s for s in all_seats if not conflicts_by_seat.get(s.id)]


async def studio_hours_for_date(session, business_id: int, day: date) -> list[tuple[time, time]]:
    """Интервалы работы студии на конкретную дату (дефолт 10:00–22:00)."""
    hours_map = await _studio_hours_map(session, business_id)
    return hours_map.get(day.weekday(), [(FALLBACK_OPEN, FALLBACK_CLOSE)])


# ---------------------------------------------------------------------------
# Администраторы, смены, покрытие, посещения, апсейлы (05)
# ---------------------------------------------------------------------------


async def admin_coverage_intervals(
    session, business_id: int, from_: datetime, to: datetime
) -> list[tuple[datetime, datetime]]:
    """Интервалы покрытия студии сменами админов на [from, to) — фактические,
    пересекающиеся смены сливаются (4 мастера × 5ч = 5ч покрытия, не 20).

    Учитываются started_at/ended_at, если смена начата/завершена; иначе план.
    """
    from tg_studio.db.models import Shift

    from_ = ensure_tz(from_)
    to = ensure_tz(to)
    rows = (
        await session.execute(
            select(Shift).where(
                Shift.business_id == business_id,
                Shift.planned_start < to,
                Shift.planned_end > from_,
            )
        )
    ).scalars().all()
    intervals: list[tuple[datetime, datetime]] = []
    for sh in rows:
        # Для планирования покрытия важно окно запроса: факт (started_at/ended_at)
        # учитываем только если он сам попадает в это окно. Иначе плановый интервал —
        # например, начатая сегодня смена на завтра не должна расширять покрытие
        # сегодняшним вечером (регрессия: coverage считал 14ч вместо плановых 4ч).
        start = sh.planned_start
        if sh.started_at is not None and from_ <= ensure_tz(sh.started_at) < to:
            start = sh.started_at
        end = sh.planned_end
        if sh.ended_at is not None and from_ < ensure_tz(sh.ended_at) <= to:
            end = sh.ended_at
        start, end = ensure_tz(start), ensure_tz(end)
        # факт может выйти за план — обрезаем по запрошенному диапазону
        intervals.append((max(start, from_), min(end, to)))
    intervals = [(s, e) for s, e in intervals if e > s]
    intervals.sort()
    merged: list[tuple[datetime, datetime]] = []
    for s, e in intervals:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


async def coverage_hours(session, business_id: int, from_: datetime, to: datetime) -> float:
    """Часы покрытия студии на интервале (сумма длин слитых интервалов смен)."""
    total = 0.0
    for s, e in await admin_coverage_intervals(session, business_id, from_, to):
        total += (e - s).total_seconds() / 3600
    return round(total, 4)


async def booking_outside_coverage(
    session, business: Business, start: datetime, end: datetime
) -> bool:
    """Выходит ли бронь [start, end) за фактическое покрытие смен админов.

    Проверяем, что каждый кусок брони покрыт каким-то интервалом покрытия.
    """
    start, end = ensure_tz(start), ensure_tz(end)
    coverage = await admin_coverage_intervals(session, business.id, start, end)
    cursor = start
    for s, e in coverage:
        if s > cursor:
            return True  # дырка до следующего покрытия
        cursor = max(cursor, e)
        if cursor >= end:
            return False
    return cursor < end


async def mark_visit_start(
    session, business: Business, booking: Booking, actor_user_id: int
) -> Booking:
    """Отметка прихода по брони (админ/владелец, 05): фактическое время визита.

    Бронь должна быть подтверждена/начата. Повторная отметка — 409 (история в
    событиях, время не перезаписываем задним числом).
    """
    if booking.status not in (BookingStatus.confirmed.value, BookingStatus.started.value):
        raise HTTPException(
            status_code=409,
            detail=f"Отметить приход можно по подтверждённой/начатой брони (сейчас: {booking.status})",
        )
    if booking.visit_started_at is not None:
        raise HTTPException(status_code=409, detail="Приход уже отмечен")
    booking.visit_started_at = datetime.now(UTC)
    add_booking_event(
        session, business.id, booking.id, BookingEventKind.visit_started, actor_user_id,
        {"visit_started_at": booking.visit_started_at.isoformat()},
    )
    return booking


async def mark_visit_end(
    session, business: Business, booking: Booking, actor_user_id: int
) -> Booking:
    """Отметка ухода по брони (админ/владелец, 05)."""
    if booking.status not in (BookingStatus.confirmed.value, BookingStatus.started.value):
        raise HTTPException(
            status_code=409,
            detail=f"Отметить уход можно по подтверждённой/начатой брони (сейчас: {booking.status})",
        )
    if booking.visit_started_at is None:
        raise HTTPException(status_code=409, detail="Сначала отметьте приход")
    if booking.visit_ended_at is not None:
        raise HTTPException(status_code=409, detail="Уход уже отмечен")
    booking.visit_ended_at = datetime.now(UTC)
    add_booking_event(
        session, business.id, booking.id, BookingEventKind.visit_ended, actor_user_id,
        {"visit_ended_at": booking.visit_ended_at.isoformat()},
    )
    return booking


async def confirm_out_of_coverage_booking(
    session, business: Business, booking: Booking, actor_user_id: int
) -> Booking:
    """Владелец подтверждает бронь вне покрытия (флаг требует_подтверждения, 05)."""
    if not booking.requires_confirmation:
        raise HTTPException(status_code=409, detail="Бронь не требует подтверждения")
    if booking.confirmed_by_user_id is not None:
        raise HTTPException(status_code=409, detail="Бронь уже подтверждена")
    booking.confirmed_by_user_id = actor_user_id
    add_booking_event(
        session, business.id, booking.id, BookingEventKind.confirmed, actor_user_id
    )
    return booking


async def ops_exceptions(session, business: Business) -> list[dict]:
    """Операционные исключения для админа/владельца (05): неявки, истекшие hold,
    брони вне покрытия без подтверждения — за последние 7 дней и сегодня вперёд."""
    now = datetime.now(UTC)
    window_from = now - timedelta(days=7)
    rows = (
        await session.execute(
            select(Booking, Seat.name, Master.full_name)
            .join(Seat, Seat.id == Booking.seat_id)
            .outerjoin(Master, Master.id == Booking.master_id)
            .where(
                Booking.business_id == business.id,
                Booking.end > window_from,
                Booking.status.in_(
                    (
                        BookingStatus.no_show.value,
                        BookingStatus.payment_expired.value,
                    )
                )
                | (
                    Booking.requires_confirmation.is_(True)
                    & Booking.confirmed_by_user_id.is_(None)
                    & Booking.status.in_(ACTIVE_BOOKING_STATUSES)
                ),
            )
            .order_by(Booking.start)
        )
    ).all()
    result = []
    for b, seat_name, master_name in rows:
        if b.status == BookingStatus.no_show.value:
            kind = "no_show"
        elif b.status == BookingStatus.payment_expired.value:
            kind = "payment_expired"
        else:
            kind = "out_of_coverage_unconfirmed"
        result.append(
            {
                "kind": kind,
                "booking_id": b.id,
                "seat_name": seat_name,
                "master_name": master_name,
                "start": b.start,
                "end": b.end,
                "status": b.status,
            }
        )
    return result
