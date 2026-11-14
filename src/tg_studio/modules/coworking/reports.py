"""Дашборд и отчёты владельца (07): только агрегаты фактов 02–06.

Период всегда [from, to) во времени студии. Выручка брони распределяется по
плановым интервалам (при фактическом визите — по фактическим), а возвраты
относятся к дате возврата. Поэтому частичный отчёт по части брони остаётся
корректным, а сумма всех дневных строк равна итогу периода.

Cash flow — только оплаченные Payment: пополнение и последующее списание с
внутреннего баланса не считаются двумя поступлениями. Выручка != поступления.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from tg_studio.db.models import (
    ACTIVE_BOOKING_STATUSES,
    Booking,
    BookingEvent,
    BookingEventKind,
    BookingStatus,
    Business,
    LedgerEntry,
    LedgerEntryKind,
    Master,
    Payment,
    PaymentStatus,
    Payout,
    PayoutStatus,
    Sale,
    SaleItem,
    SaleStatus,
    Seat,
    SeatBlock,
    StudioAdmin,
    Supply,
    SupplyMovement,
    SupplyMovementKind,
    Upsell,
)
from tg_studio.modules.coworking.pricing import CURRENCY
from tg_studio.modules.coworking.service import interval_bounds, studio_hours_for_date
from tg_studio.timeutil import STUDIO_TZ, studio_now, to_studio_local

PAID_BOOKING_STATUSES = (
    BookingStatus.confirmed.value,
    BookingStatus.started.value,
    BookingStatus.completed.value,
    BookingStatus.cancelled_by_master.value,
    BookingStatus.cancelled_by_studio.value,
    BookingStatus.no_show.value,
)
SERVICE_BOOKING_STATUSES = (
    BookingStatus.confirmed.value,
    BookingStatus.started.value,
    BookingStatus.completed.value,
)
CENT = Decimal("0.01")
HOUR_SECONDS = Decimal(3600)
SEGMENTS = ("morning", "evening", "weekend")


def dec(value) -> Decimal:
    return Decimal(str(value or 0))


def money(value: Decimal) -> float:
    return float(value.quantize(CENT, rounding=ROUND_HALF_UP))


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def studio_day_range(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=STUDIO_TZ)
    return start, start + timedelta(days=1)


def validate_period(from_: datetime, to: datetime) -> tuple[datetime, datetime]:
    if from_.tzinfo is None or to.tzinfo is None:
        raise HTTPException(status_code=422, detail="Укажите период с часовым поясом")
    from_, to = from_.astimezone(STUDIO_TZ), to.astimezone(STUDIO_TZ)
    if from_ >= to:
        raise HTTPException(status_code=422, detail="Начало периода должно быть раньше конца")
    if to - from_ > timedelta(days=366):
        raise HTTPException(status_code=422, detail="Период отчёта не может превышать 366 дней")
    return from_, to


def _overlap_seconds(start: datetime, end: datetime, from_: datetime, to: datetime) -> Decimal:
    left, right = max(utc(start), utc(from_)), min(utc(end), utc(to))
    return max(Decimal(0), dec((right - left).total_seconds()))


def _merge(intervals: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    result: list[tuple[datetime, datetime]] = []
    for start, end in sorted(intervals):
        start, end = utc(start), utc(end)
        if end <= start:
            continue
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(result[-1][1], end))
        else:
            result.append((start, end))
    return result


def _subtract(
    interval: tuple[datetime, datetime], occupied: list[tuple[datetime, datetime]]
) -> list[tuple[datetime, datetime]]:
    parts = [interval]
    for start, end in occupied:
        next_parts = []
        for left, right in parts:
            if end <= left or start >= right:
                next_parts.append((left, right))
                continue
            if left < start:
                next_parts.append((left, min(start, right)))
            if end < right:
                next_parts.append((max(end, left), right))
        parts = next_parts
    return parts


def _intersect(
    interval: tuple[datetime, datetime], windows: list[tuple[datetime, datetime]]
) -> list[tuple[datetime, datetime]]:
    return [(max(interval[0], w0), min(interval[1], w1)) for w0, w1 in windows
            if min(interval[1], w1) > max(interval[0], w0)]


def _hours(seconds: Decimal) -> float:
    return float((seconds / HOUR_SECONDS).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def _segment_at(moment: datetime) -> str:
    local = to_studio_local(moment)
    if local.weekday() >= 5:
        return "weekend"
    return "morning" if local.hour < 18 else "evening"


def _segment_seconds(start: datetime, end: datetime, from_: datetime, to: datetime) -> dict[str, Decimal]:
    result = dict.fromkeys(SEGMENTS, Decimal(0))
    cursor = max(utc(start), utc(from_))
    finish = min(utc(end), utc(to))
    while cursor < finish:
        next_hour = (cursor + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        boundary = min(finish, next_hour)
        result[_segment_at(cursor)] += dec((boundary - cursor).total_seconds())
        cursor = boundary
    return result


@dataclass
class ReportData:
    bookings: list = field(default_factory=list)
    sales: list = field(default_factory=list)
    sale_items: list = field(default_factory=list)
    ledger: list = field(default_factory=list)
    events: list = field(default_factory=list)
    payments: list = field(default_factory=list)
    movements: list = field(default_factory=list)
    upsells: list = field(default_factory=list)
    payouts: list = field(default_factory=list)
    masters: list = field(default_factory=list)
    seats: list = field(default_factory=list)
    blocks: list = field(default_factory=list)
    admins: list = field(default_factory=list)


async def load_report_data(session, business: Business, from_: datetime, to: datetime) -> ReportData:
    """Нужные факты одним набором запросов; календарные разрезы считаем в Python."""
    bid = business.id
    data = ReportData()
    # Колонки DateTime(timezone=True) на SQLite пишут wall-clock без перевода в UTC:
    # границы в зоне студии (+05:00) ломали бы сравнения. UTC-границы корректны везде.
    from_, to = utc(from_), utc(to)

    async def rows(stmt):
        return (await session.execute(stmt)).scalars().all()

    data.bookings = list(await rows(
        select(Booking).where(
            Booking.business_id == bid, Booking.start < to, Booking.end > from_,
        )
    ))
    data.sales = list(await rows(
        select(Sale).options(selectinload(Sale.items).selectinload(SaleItem.supply)).where(
            Sale.business_id == bid, Sale.created_at >= from_, Sale.created_at < to,
            Sale.status != SaleStatus.cancelled.value,
        )
    ))
    data.sale_items = []
    data.ledger = list(await rows(
        select(LedgerEntry).where(
            LedgerEntry.business_id == bid, LedgerEntry.created_at >= from_, LedgerEntry.created_at < to,
        )
    ))
    data.events = list(await rows(
        select(BookingEvent).where(
            BookingEvent.business_id == bid,
            BookingEvent.kind == BookingEventKind.refunded.value,
            BookingEvent.created_at >= from_, BookingEvent.created_at < to,
        )
    ))
    data.sale_items = list(await rows(
        select(SaleItem).join(Sale, Sale.id == SaleItem.sale_id).where(
            Sale.business_id == bid,
            (Sale.created_at >= from_) | Sale.id.in_([
                event.details["sale_id"] for event in data.events
                if isinstance(event.details, dict) and event.details.get("sale_id") is not None
            ]),
        )
    ))
    data.payments = list(await rows(
        select(Payment).where(
            Payment.business_id == bid, Payment.status == PaymentStatus.paid.value,
            Payment.paid_at.is_not(None), Payment.paid_at >= from_, Payment.paid_at < to,
        ).options(selectinload(Payment.master))
    ))
    data.movements = list(await rows(
        select(SupplyMovement).where(
            SupplyMovement.business_id == bid, SupplyMovement.kind == SupplyMovementKind.refund.value,
            SupplyMovement.sale_id.is_not(None),
            SupplyMovement.created_at >= from_, SupplyMovement.created_at < to,
        )
    ))
    sale_refund_events = [
        event for event in data.events
        if isinstance(event.details, dict) and event.details.get("sale_id") is not None
    ]
    if sale_refund_events:
        # Товарный возврат может относиться к продаже до начала отчёта.
        refund_sales = list(await rows(select(Sale).where(
            Sale.business_id == bid,
            Sale.id.in_([event.details["sale_id"] for event in sale_refund_events]),
        )))
        known = {sale.id for sale in data.sales}
        data.sales.extend(sale for sale in refund_sales if sale.id not in known)
    # Id из разных пространств (платёж/бронь/продажа) не смешиваем: иначе booking_id=3
    # зацепил бы платёж с id=3. Брони и продажи периода берём целиком — их оплата
    # могла быть раньше окна, а Excel нужны её внутренний и банковский id.
    payment_ids = {entry.payment_id for entry in data.ledger if entry.payment_id is not None}
    booking_ids = {booking.id for booking in data.bookings}
    booking_ids.update(entry.booking_id for entry in data.ledger if entry.booking_id is not None)
    booking_ids.update(event.booking_id for event in data.events if event.booking_id is not None)
    sale_ids = {sale.id for sale in data.sales}
    sale_ids.update(entry.sale_id for entry in data.ledger if entry.sale_id is not None)
    for event in data.events:
        if isinstance(event.details, dict):
            if event.details.get("payment_id") is not None:
                payment_ids.add(event.details["payment_id"])
            if event.details.get("sale_id") is not None:
                sale_ids.add(event.details["sale_id"])
    if payment_ids or booking_ids or sale_ids:
        # Такой платёж в cash за период не попадает (money_report фильтрует по paid_at).
        linked_payments = list(await rows(
            select(Payment).options(selectinload(Payment.master)).where(
                Payment.business_id == bid,
                Payment.status == PaymentStatus.paid.value,
                or_(
                    Payment.id.in_(payment_ids),
                    Payment.booking_id.in_(booking_ids),
                    Payment.sale_id.in_(sale_ids),
                ),
            )
        ))
        known_payments = {payment.id for payment in data.payments}
        data.payments.extend(
            payment for payment in linked_payments if payment.id not in known_payments
        )
    data.upsells = list(await rows(
        select(Upsell).where(
            Upsell.business_id == bid, Upsell.created_at >= from_, Upsell.created_at < to,
        )
    ))
    data.payouts = list(await rows(
        select(Payout).where(Payout.business_id == bid)
    ))
    data.masters = list(await rows(
        select(Master).options(selectinload(Master.user)).where(Master.business_id == bid)
    ))
    data.seats = list(await rows(select(Seat).where(Seat.business_id == bid).order_by(Seat.sort_order, Seat.id)))
    data.blocks = list(await rows(
        select(SeatBlock).where(SeatBlock.business_id == bid, SeatBlock.start < to, SeatBlock.end > from_)
    ))
    data.admins = list(await rows(select(StudioAdmin).where(StudioAdmin.business_id == bid)))
    return data


def _booking_interval(booking: Booking) -> tuple[datetime, datetime]:
    return utc(booking.start), utc(booking.end)


def _booking_price(booking: Booking) -> Decimal:
    return dec(booking.paid_amount or (booking.price_snapshot or {}).get("final"))


def refund_facts(data: ReportData) -> list[dict]:
    """Единый список возвратов периода. Возврат по брони/продаже мастеру пишет и
    событие, и ledger-проводку на одну сумму — это один факт (парим по
    брони/продаже и сумме, поэтому второй отдельный возврат не теряется).
    `to_balance`: деньги ушли на баланс мастера, из кассы не выдавались."""
    sale_by_id = {sale.id: sale for sale in data.sales}
    booking_master = {b.id: b.master_id for b in data.bookings}
    ledger = [e for e in data.ledger if e.kind == LedgerEntryKind.refund.value]
    matched: set[int] = set()
    facts = []
    for event in data.events:
        details = event.details if isinstance(event.details, dict) else {}
        sale = sale_by_id.get(details.get("sale_id"))
        amount = dec(details.get("amount"))
        entry = next((
            e for e in ledger if e.id not in matched
            and (e.sale_id == sale.id if sale else e.sale_id is None and e.booking_id == event.booking_id)
            and dec(e.amount) == amount
        ), None)
        if entry is not None:
            matched.add(entry.id)
        facts.append({
            "date": event.created_at, "type": "sale" if sale else "booking",
            "booking_id": event.booking_id, "sale_id": sale.id if sale else None,
            "master_id": sale.master_id if sale else booking_master.get(event.booking_id),
            "amount": amount, "held": dec(details.get("held")),
            "payment_id": details.get("payment_id"), "to_balance": entry is not None,
            "note": details.get("reason") or ("Возврат по продаже" if sale else "Возврат и удержание по брони"),
        })
    for entry in ledger:
        if entry.id in matched:
            continue
        facts.append({
            "date": entry.created_at, "type": "booking" if entry.booking_id is not None else "sale",
            "booking_id": entry.booking_id, "sale_id": entry.sale_id, "master_id": entry.master_id,
            "amount": dec(entry.amount), "held": Decimal(0),
            "payment_id": entry.payment_id, "to_balance": True, "note": entry.note,
        })
    return facts


def revenue_report(data: ReportData, from_: datetime, to: datetime) -> dict:
    booking_gross = sale_gross = Decimal(0)
    booking_refunds = sale_refunds = Decimal(0)
    by_day: dict[date, dict] = defaultdict(lambda: {
        "booking_revenue": Decimal(0), "sales_revenue": Decimal(0),
        "refunds": Decimal(0), "hours": Decimal(0),
    })
    by_seat: dict[int, dict] = defaultdict(lambda: {"revenue": Decimal(0), "hours": Decimal(0)})
    segments: dict[str, Decimal] = dict.fromkeys(SEGMENTS, Decimal(0))

    for booking in data.bookings:
        if booking.status not in PAID_BOOKING_STATUSES:
            continue
        start, end = _booking_interval(booking)
        total_seconds = _overlap_seconds(start, end, start, end)
        if total_seconds <= 0:
            continue
        price = _booking_price(booking)
        seconds = _overlap_seconds(start, end, from_, to)
        if not seconds:
            continue
        amount = price * seconds / total_seconds
        booking_gross += amount
        # Ночная бронь разбивается по локальным календарным датам.
        cursor = utc(from_)
        end_of_period = utc(to)
        while cursor < end_of_period:
            day_finish = utc(studio_day_range(to_studio_local(cursor).date())[1])
            day_seconds = _overlap_seconds(start, end, cursor, min(utc(to), day_finish))
            if day_seconds:
                by_day[to_studio_local(cursor).date()]["booking_revenue"] += price * day_seconds / total_seconds
                by_day[to_studio_local(cursor).date()]["hours"] += day_seconds
            cursor = day_finish
        seat = by_seat[booking.seat_id]
        seat["revenue"] += amount
        seat["hours"] += seconds
        for segment, value in _segment_seconds(start, end, from_, to).items():
            segments[segment] += value

    sale_by_id = {sale.id: sale for sale in data.sales}
    for sale in data.sales:
        if from_ <= utc(sale.created_at) < to:
            sale_gross += dec(sale.total_amount)
            by_day[to_studio_local(sale.created_at).date()]["sales_revenue"] += dec(sale.total_amount)
    for fact in refund_facts(data):
        if fact["type"] == "sale":
            sale_refunds += fact["amount"]
        else:
            booking_refunds += fact["amount"]
        by_day[to_studio_local(fact["date"]).date()]["refunds"] += fact["amount"]

    # Продажи могли быть до начала периода: их позиции уже загружены,
    # а по возвратам мастера источником служит LedgerEntry.
    days = []
    for day in sorted(by_day):
        value = by_day[day]
        gross = value["booking_revenue"] + value["sales_revenue"]
        days.append({
            "date": day, "booking_revenue": money(value["booking_revenue"]),
            "sales_revenue": money(value["sales_revenue"]),
            "gross": money(gross), "refunds": money(value["refunds"]),
            "net": money(gross - value["refunds"]), "hours": _hours(value["hours"]),
        })
    seat_names = {seat.id: seat.name for seat in data.seats}
    seats = [{
        "seat_id": seat_id, "seat_name": seat_names.get(seat_id, f"#{seat_id}"),
        "hours": _hours(value["hours"]), "revenue": money(value["revenue"]),
    } for seat_id, value in sorted(by_seat.items())]
    occupied = sum(segments.values(), Decimal(0))
    return {
        "from_": from_, "to": to, "currency": CURRENCY,
        "booking_revenue": money(booking_gross), "sales_revenue": money(sale_gross),
        "gross": money(booking_gross + sale_gross),
        "booking_refunds": money(booking_refunds), "sale_refunds": money(sale_refunds),
        "refunds": money(booking_refunds + sale_refunds),
        "net": money(booking_gross + sale_gross - booking_refunds - sale_refunds),
        "by_day": days, "by_seat": seats,
        "segments": [
            {"segment": key, "hours": _hours(value), "percent": float(value / occupied * 100) if occupied else 0}
            for key, value in segments.items()
        ],
        "sale_by_id": sale_by_id,
    }


async def usage_report(session, data: ReportData, from_: datetime, to: datetime) -> dict:
    rows = []
    total_available = total_occupied = total_blocked = Decimal(0)
    total_segments = dict.fromkeys(SEGMENTS, Decimal(0))
    business_id = data.seats[0].business_id if data.seats else None
    hours_by_day = {}
    cursor = from_
    while business_id is not None and cursor < to:
        local_day = to_studio_local(cursor).date()
        hours_by_day[local_day] = await studio_hours_for_date(session, business_id, local_day)
        cursor = utc(studio_day_range(local_day)[1])
    for seat in data.seats:
        if not seat.is_active and not any(b.seat_id == seat.id for b in data.bookings):
            continue  # выключенное место без броней не занижает загрузку салона
        intervals: list[tuple[datetime, datetime]] = []
        cursor = from_
        while cursor < to:
            local_day = to_studio_local(cursor).date()
            _, next_day = studio_day_range(local_day)
            for opening, closing in hours_by_day[local_day]:
                start, end = interval_bounds(local_day, opening, closing)
                left, right = max(start, from_), min(end, to)
                if right > left:
                    intervals.append((left, right))
            cursor = next_day
        available_intervals = _merge(intervals)
        occupied = [
            (max(utc(b.start), from_), min(utc(b.end), to))
            for b in data.bookings if b.seat_id == seat.id and b.status in SERVICE_BOOKING_STATUSES
            and utc(b.start) < utc(to) and utc(b.end) > utc(from_)
        ]
        blocked = [
            (max(utc(b.start), from_), min(utc(b.end), to))
            for b in data.blocks if b.seat_id in (None, seat.id)
        ]
        # Загрузка места ≤ 100%: занятость и блокировки считаем только внутри рабочих часов студии
        occupied = [p for iv in _merge(occupied) for p in _intersect(iv, available_intervals)]
        blocked = [p for iv in _merge(blocked) for p in _intersect(iv, available_intervals)]
        occupied, blocked = _merge(occupied), _merge(blocked)
        free = [part for interval in available_intervals for part in _subtract(interval, occupied + blocked)]
        available_seconds = sum((dec((end - start).total_seconds()) for start, end in available_intervals), Decimal(0))
        occupied_seconds = sum((dec((end - start).total_seconds()) for start, end in occupied), Decimal(0))
        blocked_seconds = sum((dec((end - start).total_seconds()) for start, end in blocked), Decimal(0))
        segment_values = dict.fromkeys(SEGMENTS, Decimal(0))
        for start, end in occupied:
            for key, value in _segment_seconds(start, end, from_, to).items():
                segment_values[key] += value
                total_segments[key] += value
        rows.append({
            "seat_id": seat.id, "seat_name": seat.name, "is_active": seat.is_active,
            "available_hours": _hours(available_seconds), "occupied_hours": _hours(occupied_seconds),
            "blocked_hours": _hours(blocked_seconds), "free_hours": _hours(sum((dec((e - s).total_seconds()) for s, e in free), Decimal(0))),
            "percent": float(occupied_seconds / available_seconds * 100) if available_seconds else 0,
            "segments": [{"segment": key, "hours": _hours(value)} for key, value in segment_values.items()],
        })
        total_available += available_seconds
        total_occupied += occupied_seconds
        total_blocked += blocked_seconds
    return {
        "from_": from_, "to": to, "seats": rows,
        "available_hours": _hours(total_available), "occupied_hours": _hours(total_occupied),
        "blocked_hours": _hours(total_blocked),
        "percent": float(total_occupied / total_available * 100) if total_available else 0,
        "segments": [{"segment": key, "hours": _hours(value)} for key, value in total_segments.items()],
    }


def money_report(data: ReportData, from_: datetime, to: datetime, revenue: dict) -> dict:
    payments = []
    receipts = Decimal(0)
    for payment in data.payments:
        if not (from_ <= utc(payment.paid_at) < to):
            continue  # связанный платёж нужен Excel, но это не cash этого периода
        amount = dec(payment.amount)
        receipts += amount
        payments.append({
            "date": payment.paid_at, "type": "sale" if payment.sale_id else "booking" if payment.booking_id else "topup",
            "booking_id": payment.booking_id, "sale_id": payment.sale_id,
            "master_name": payment.master.full_name if payment.master else None,
            "amount": money(amount), "method": payment.provider,
            "external_id": payment.external_invoice_id, "payment_id": payment.id,
        })
    master_names = {m.id: m.full_name for m in data.masters}
    facts = refund_facts(data)
    refunds = [{
        "date": f["date"], "type": f["type"], "booking_id": f["booking_id"], "sale_id": f["sale_id"],
        "master_name": master_names.get(f["master_id"]), "amount": money(f["amount"]),
        "held": money(f["held"]), "external_id": None, "payment_id": f["payment_id"], "note": f["note"],
    } for f in facts]
    cash_refunds = sum((f["amount"] for f in facts if not f["to_balance"]), Decimal(0))
    balances = []
    for master in data.masters:
        balances.append({
            "master_id": master.id, "master_name": master.full_name,
            "balance": None, "is_active": master.is_active,
        })
    return {
        "from_": from_, "to": to, "currency": CURRENCY,
        "receipts": money(receipts), "net_cash": money(receipts - cash_refunds),
        "refunds": money(dec(revenue["refunds"])), "booking_holds": money(sum((dec(r["held"]) for r in refunds), Decimal(0))),
        "topups": money(sum((dec(e.amount) for e in data.ledger if e.kind == LedgerEntryKind.topup.value), Decimal(0))),
        "charges": money(-sum((dec(e.amount) for e in data.ledger if e.kind == LedgerEntryKind.charge.value), Decimal(0))),
        "payments": payments, "refund_rows": refunds, "balances": balances,
    }


async def fill_balances(session, business_id: int, rows: list[dict]) -> Decimal:
    result = (await session.execute(
        select(LedgerEntry.master_id, func.coalesce(func.sum(LedgerEntry.amount), 0))
        .where(LedgerEntry.business_id == business_id).group_by(LedgerEntry.master_id)
    )).all()
    amounts = {master_id: dec(total) for master_id, total in result}
    for row in rows:
        row["balance"] = money(amounts.get(row["master_id"], Decimal(0)))
    return sum(amounts.values(), Decimal(0))


def masters_report(data: ReportData, from_: datetime, to: datetime) -> dict:
    """Мастера за период. Присутствие: брони, начавшиеся в периоде и уже
    начавшиеся к «сейчас» (presence_expected), из них подтверждённые мастером
    кнопкой в напоминании (presence_confirmed). Неподтверждённая ≠ неявка —
    мастер мог не нажать."""
    rows = []
    master_refunds = refund_facts(data)
    presence_until = min(utc(to), datetime.now(UTC))
    for master in data.masters:
        bookings = [b for b in data.bookings if b.master_id == master.id and b.status in PAID_BOOKING_STATUSES]
        seconds = Decimal(0)
        revenue = Decimal(0)
        for booking in bookings:
            start, end = _booking_interval(booking)
            total = _overlap_seconds(start, end, start, end)
            selected = _overlap_seconds(start, end, from_, to)
            if booking.status in SERVICE_BOOKING_STATUSES:
                seconds += selected  # отменённая бронь не создаёт отработанных часов
            if total:
                revenue += _booking_price(booking) * selected / total
        sales = sum((
            dec(s.total_amount) for s in data.sales
            if s.master_id == master.id and from_ <= utc(s.created_at) < to
        ), Decimal(0))
        refunds = sum((f["amount"] for f in master_refunds if f["master_id"] == master.id), Decimal(0))
        created = utc(master.user.created_at) if master.user else None
        expected = [
            b for b in bookings
            if b.status in SERVICE_BOOKING_STATUSES and utc(from_) <= utc(b.start) < presence_until
        ]
        rows.append({
            "master_id": master.id, "master_name": master.full_name, "is_active": master.is_active,
            "is_new": bool(created and from_ <= created < to), "bookings": len(bookings),
            "hours": _hours(seconds), "revenue": money(revenue + sales - refunds),
            "refunds": money(refunds),
            "booking_revenue": money(revenue), "sales_revenue": money(sales), "balance": None,
            "presence_expected": len(expected),
            "presence_confirmed": sum(b.presence_confirmed_at is not None for b in expected),
        })
    return {"from_": from_, "to": to, "currency": CURRENCY, "masters": rows,
            "new": sum(row["is_new"] for row in rows), "active": sum(row["bookings"] > 0 or row["sales_revenue"] > 0 for row in rows),
            "presence_expected": sum(row["presence_expected"] for row in rows),
            "presence_confirmed": sum(row["presence_confirmed"] for row in rows)}


def upsells_report(data: ReportData, from_: datetime, to: datetime) -> dict:
    rows = []
    for admin in data.admins:
        upsells = [u for u in data.upsells if u.admin_id == admin.id]
        payouts = [p for p in data.payouts if p.admin_id == admin.id]
        accrued = sum((dec(p.amount) for p in payouts if p.status == PayoutStatus.accrued.value), Decimal(0))
        paid = sum((dec(p.amount) for p in payouts if p.status == PayoutStatus.paid.value and p.paid_at and from_ <= utc(p.paid_at) < to), Decimal(0))
        rows.append({
            "admin_id": admin.id, "admin_name": admin.full_name, "count": len(upsells),
            "amount": money(sum((dec(u.amount) for u in upsells), Decimal(0))),
            "product_amount": money(sum((dec(u.amount) for u in upsells if u.sale_id is not None), Decimal(0))),
            "manual_amount": money(sum((dec(u.amount) for u in upsells if u.sale_id is None), Decimal(0))),
            # кто покупает: мастера (расходники для работы) и клиенты мастеров (уход)
            "master_amount": money(sum((dec(u.amount) for u in upsells if u.master_id is not None), Decimal(0))),
            "client_amount": money(sum((dec(u.amount) for u in upsells if u.master_id is None), Decimal(0))),
            "payout_accrued": money(accrued), "payout_paid": money(paid),
        })
    return {"from_": from_, "to": to, "currency": CURRENCY, "admins": rows,
            "amount": money(sum((dec(row["amount"]) for row in rows), Decimal(0))),
            "master_amount": money(sum((dec(row["master_amount"]) for row in rows), Decimal(0))),
            "client_amount": money(sum((dec(row["client_amount"]) for row in rows), Decimal(0)))}


def dashboard_period(now: datetime, period: str) -> tuple[datetime, datetime]:
    """Календарные день / неделя (с понедельника) / месяц студии, содержащие `now`."""
    today = now.date()
    if period == "week":
        first = today - timedelta(days=today.weekday())
        return studio_day_range(first)[0], studio_day_range(first + timedelta(days=7))[0]
    if period == "month":
        first = today.replace(day=1)
        following = (first + timedelta(days=32)).replace(day=1)
        return studio_day_range(first)[0], studio_day_range(following)[0]
    return studio_day_range(today)


async def dashboard(session, business: Business, period: str = "day") -> dict:
    now = studio_now()
    from_, tomorrow = dashboard_period(now, period)
    data = await load_report_data(session, business, from_, tomorrow)
    revenue = revenue_report(data, from_, tomorrow)
    money_data = money_report(data, from_, tomorrow, revenue)
    total_balance = await fill_balances(session, business.id, money_data["balances"])
    money_data["total_balance"] = money(total_balance)
    # Загрузка за неделю/месяц считаем до «сейчас»: будущие часы занизили бы процент
    usage = await usage_report(session, data, from_, tomorrow if period == "day" else min(tomorrow, utc(now)))
    next_day = studio_day_range(now.date())[1] + timedelta(days=1)
    upcoming_data = await load_report_data(session, business, now, next_day)
    # идут сейчас — подтверждённые брони, время которых наступило; скоро — ещё не начавшиеся
    onsite = sorted([
        b for b in upcoming_data.bookings
        if b.status in SERVICE_BOOKING_STATUSES and utc(b.start) <= utc(now) < utc(b.end)
    ], key=lambda b: utc(b.start))
    upcoming = sorted([
        b for b in upcoming_data.bookings
        if b.status in ACTIVE_BOOKING_STATUSES and utc(b.start) > utc(now)
    ], key=lambda b: utc(b.start))[:12]
    names = {m.id: m.full_name for m in upcoming_data.masters}
    seat_names = {s.id: s.name for s in upcoming_data.seats}

    horizon = utc(now) + timedelta(hours=4)
    seats_now = []
    all_bookings = {b.id: b for b in data.bookings + upcoming_data.bookings}.values()
    now_utc = utc(now)
    for seat in data.seats:
        if not seat.is_active:
            continue
        busy = [
            b for b in all_bookings
            if b.seat_id == seat.id and b.status in ACTIVE_BOOKING_STATUSES
            and utc(b.start) < horizon and utc(b.end) > now_utc
        ]
        current = next((b for b in busy if utc(b.start) <= now_utc < utc(b.end)), None)
        blocked = next((
            bl for bl in data.blocks
            if bl.seat_id in (None, seat.id) and utc(bl.start) <= now_utc < utc(bl.end)
        ), None)
        seats_now.append({
            "seat_id": seat.id, "seat_name": seat.name,
            "status": "occupied" if current else "blocked" if blocked else "free",
            "booking_id": current.id if current else None,
            "master_name": names.get(current.master_id) if current else None,
            "until": current.end if current else blocked.end if blocked else None,
            "upcoming": len([b for b in busy if b is not current]),
        })
    low_stock = list((await session.execute(
        select(Supply).where(
            Supply.business_id == business.id, Supply.is_active.is_(True),
            Supply.min_quantity.is_not(None), Supply.quantity <= Supply.min_quantity,
        ).order_by(Supply.quantity)
    )).scalars().all())
    from tg_studio.modules.coworking.service import ops_exceptions
    return {
        "generated_at": now, "period": period, "from_": from_, "to": tomorrow, "currency": CURRENCY,
        "revenue": revenue, "money": money_data, "usage": usage,
        "master_balances": money(total_balance),
        "bookings_created": sum(from_ <= utc(b.created_at) < tomorrow for b in data.bookings),
        "cancellations": sum(b.status in (BookingStatus.cancelled_by_master.value, BookingStatus.cancelled_by_studio.value) for b in data.bookings),
        "onsite": [{
            "booking_id": b.id, "master_name": names.get(b.master_id), "seat_name": seat_names.get(b.seat_id),
            "start": b.start, "end": b.end, "status": b.status,
            "presence_confirmed": b.presence_confirmed_at is not None,
        } for b in onsite],
        "upcoming": [{
            "booking_id": b.id, "master_name": names.get(b.master_id), "seat_name": seat_names.get(b.seat_id),
            "start": b.start, "end": b.end, "status": b.status,
        } for b in upcoming],
        "seats_now": seats_now,
        "low_stock": [{"id": s.id, "name": s.name, "quantity": float(s.quantity), "unit": s.unit, "min_quantity": float(s.min_quantity)} for s in low_stock],
        "exceptions": await ops_exceptions(session, business),
        "upsells": upsells_report(data, from_, tomorrow),
    }
