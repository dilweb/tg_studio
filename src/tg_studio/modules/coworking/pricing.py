"""Движок расчёта стоимости брони коворкинга (docs/coworking/03-pricing-settings.md).

Алгоритм:
1. Интервал брони [start, end) режется по границам подходящих ценовых правил
   (часы работы студии уже проверены при создании брони): для каждого правила
   строится пересечение его окна времени суток с интервалом брони — это и есть
   границы резки. Получаем сегменты, каждый целиком внутри окна не более чем
   одного набора правил.
2. На сегмент действует правило с максимальным priority (при равенстве —
   более новое, больший id); правило конкретного места (seat_id) выигрывает
   у правила «все места» при том же priority.
3. part_amount = hourly_rate × минуты/60, округляем до 2 знаков (ROUND_HALF_UP);
   base = Σ частей. Если хоть один сегмент не покрыт правилом → 409
   «не задана цена на этот интервал».
4. Скидки (discount_rules, по priority): resident — если мастер-резидент,
   min_hours — если длительность брони ≥ порога. percent — доля от base,
   fixed — фиксированная сумма. discount = Σ, не больше base; final = base − discount.

Снимок (словарь) пишется в bookings.price_snapshot — единственный источник
стоимости брони дальше; смена правил не трогает существующие брони.

Время: start/end — aware; «локальное время студии» = start.astimezone(STUDIO_TZ).
"""

import logging
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException
from sqlalchemy import select

from tg_studio.db.models import DiscountRule, Master, PriceRule, PriceRuleDayKind
from tg_studio.timeutil import STUDIO_TZ

logger = logging.getLogger(__name__)

# Валюта студии (Asia/Almaty) — фиксируем в снимке
CURRENCY = "KZT"

_CENT = Decimal("0.01")


def _round2(value: Decimal | float) -> float:
    return float(Decimal(str(value)).quantize(_CENT, rounding=ROUND_HALF_UP))


def _rule_matches_day(rule: PriceRule, local_date: date) -> bool:
    kind = rule.day_kind
    if kind == PriceRuleDayKind.all.value:
        return True
    if kind == PriceRuleDayKind.weekdays.value:
        return local_date.weekday() < 5
    if kind == PriceRuleDayKind.weekends.value:
        return local_date.weekday() >= 5
    if kind == PriceRuleDayKind.date.value:
        return rule.specific_date is not None and local_date == rule.specific_date
    return False


def _rule_window_on_day(rule: PriceRule, day: date) -> tuple[datetime, datetime] | None:
    """Окно правила [time_from, time_to) на конкретную дату в aware-времени (UTC)."""
    if not _rule_matches_day(rule, day):
        return None
    tz_start = datetime.combine(day, rule.time_from, tzinfo=STUDIO_TZ)
    tz_end = datetime.combine(day, rule.time_to, tzinfo=STUDIO_TZ)
    return tz_start, tz_end


def _pick_rule(segment_start: datetime, segment_end: datetime, rules: list[PriceRule]) -> PriceRule | None:
    """Правило, покрывающее сегмент целиком (середина сегмента внутри окна)."""
    mid = segment_start + (segment_end - segment_start) / 2
    local_mid = mid.astimezone(STUDIO_TZ)
    candidates = []
    for rule in rules:
        window = _rule_window_on_day(rule, local_mid.date())
        if window is None:
            continue
        w_start, w_end = window
        if w_start <= segment_start and segment_end <= w_end:
            candidates.append(rule)
    if not candidates:
        return None
    # Выше priority; при равенстве — правило конкретного места над «все места»,
    # затем более новое (больший id)
    return max(
        candidates,
        key=lambda r: (r.priority, r.seat_id is not None, r.id),
    )


async def _active_price_rules(session, business_id: int, seat_id: int) -> list[PriceRule]:
    stmt = select(PriceRule).where(
        PriceRule.business_id == business_id,
        PriceRule.is_active.is_(True),
        # правило этого места или «все места»
        (PriceRule.seat_id == seat_id) | (PriceRule.seat_id.is_(None)),
    )
    return list((await session.execute(stmt)).scalars().all())


async def _active_discount_rules(session, business_id: int, seat_id: int) -> list[DiscountRule]:
    stmt = select(DiscountRule).where(
        DiscountRule.business_id == business_id,
        DiscountRule.is_active.is_(True),
        (DiscountRule.seat_id == seat_id) | (DiscountRule.seat_id.is_(None)),
    )
    return list((await session.execute(stmt)).scalars().all())


def _split_into_segments(
    start: datetime, end: datetime, rules: list[PriceRule]
) -> list[tuple[datetime, datetime]]:
    """Режем [start, end) по границам окон правил (все даты внутри диапазона)."""
    boundaries = {start, end}
    day = start.astimezone(STUDIO_TZ).date()
    last_day = end.astimezone(STUDIO_TZ).date()
    while day <= last_day:
        for rule in rules:
            window = _rule_window_on_day(rule, day)
            if window is None:
                continue
            w_start, w_end = window
            # Пересечение окна правила с интервалом брони — потенциальная граница
            cut_start, cut_end = max(w_start, start), min(w_end, end)
            if cut_start < cut_end:
                if cut_start > start:
                    boundaries.add(cut_start)
                if cut_end < end:
                    boundaries.add(cut_end)
        day += timedelta(days=1)
    points = sorted(boundaries)
    return [(points[i], points[i + 1]) for i in range(len(points) - 1)]


def _apply_discounts(
    base: float,
    duration_minutes: int,
    master: Master | None,
    rules: list[DiscountRule],
) -> tuple[float, list[dict]]:
    """Скидки по правилам (по priority, затем id). discount не больше base."""
    applied: list[dict] = []
    total = Decimal("0")
    ordered = sorted(rules, key=lambda r: (r.priority, r.id), reverse=True)
    for rule in ordered:
        if rule.kind == "resident":
            if master is None or not master.is_resident:
                continue
        elif rule.kind == "min_hours":
            if rule.min_hours is None or duration_minutes < rule.min_hours * 60:
                continue
        else:
            continue
        if rule.percent is not None:
            amount = Decimal(str(base)) * Decimal(str(rule.percent))
        elif rule.fixed_amount is not None:
            amount = Decimal(str(rule.fixed_amount))
        else:
            continue
        total += amount
        applied.append(
            {
                "rule_id": rule.id,
                "name": rule.name,
                "kind": rule.kind,
                "amount": _round2(amount),
            }
        )
    discount = min(_round2(total), base)
    return discount, applied


async def quote_booking_price(
    session,
    business_id: int,
    seat_id: int,
    start: datetime,
    end: datetime,
    master: Master | None = None,
) -> dict:
    """Полный расчёт стоимости брони [start, end) на месте seat_id.

    Возвращает снимок (dict) для bookings.price_snapshot. Если интервал не
    покрыт ценовыми правилами целиком — 409 с непокрытыми сегментами.
    start/end — aware (или naive = время студии, см. service.ensure_tz).
    """
    if start.tzinfo is None:
        start = start.replace(tzinfo=STUDIO_TZ)
    if end.tzinfo is None:
        end = end.replace(tzinfo=STUDIO_TZ)

    rules = await _active_price_rules(session, business_id, seat_id)
    segments = _split_into_segments(start, end, rules)

    parts: list[dict] = []
    uncovered: list[dict] = []
    for seg_start, seg_end in segments:
        rule = _pick_rule(seg_start, seg_end, rules)
        minutes = int((seg_end - seg_start).total_seconds() // 60)
        if rule is None:
            local = seg_start.astimezone(STUDIO_TZ)
            uncovered.append(
                {
                    "start": seg_start.isoformat(),
                    "end": seg_end.isoformat(),
                    "local": local.strftime("%d.%m.%Y %H:%M") + "–" + seg_end.astimezone(STUDIO_TZ).strftime("%H:%M"),
                }
            )
            continue
        amount = Decimal(str(rule.hourly_rate)) * Decimal(minutes) / Decimal(60)
        parts.append(
            {
                "start": seg_start.isoformat(),
                "end": seg_end.isoformat(),
                "rule_id": rule.id,
                "rule_name": rule.name,
                "hourly_rate": float(rule.hourly_rate),
                "minutes": minutes,
                "amount": _round2(amount),
            }
        )

    if uncovered:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Не задана цена на часть интервала — добавьте ценовое правило",
                "uncovered": uncovered,
                "text": "; ".join(f"{u['local']}" for u in uncovered),
            },
        )

    base = _round2(sum(Decimal(str(p["amount"])) for p in parts))
    duration_minutes = int((end - start).total_seconds() // 60)
    discount_rules = await _active_discount_rules(session, business_id, seat_id)
    discount, discounts = _apply_discounts(base, duration_minutes, master, discount_rules)
    final = _round2(Decimal(str(base)) - Decimal(str(discount)))

    return {
        "currency": CURRENCY,
        "calculated_at": datetime.now(UTC).isoformat(),
        "seat_id": seat_id,
        "master_id": master.id if master else None,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "parts": parts,
        "base": base,
        "discounts": discounts,
        "discount": discount,
        "final": final,
    }
