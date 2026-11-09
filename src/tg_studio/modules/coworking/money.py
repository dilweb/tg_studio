"""Деньги коворкинга: счета (payments), ledger мастера, предоплата брони (04).

Правило: бронь подтверждается только 100% оплатой стоимости из снимка цены,
долгов нет (docs/coworking/04-money-prepay.md).

Хранимые факты: Payment (счёт + статус) и LedgerEntry (проводки счёта
мастера). Остаток баланса мастера НИГДЕ не хранится — считается как
Σ LedgerEntry.amount по мастеру; журнал не может разойтись с балансом.

Сценарий брони:
- create_booking (service.py) → бронь ожидает_оплаты + счёт Payment(pending,
  booking_id) на price_snapshot.final; у apipay — внешний счёт Kaspi;
- POST /bookings/{id}/pay {method: card|balance}:
  - card — apipay-счёт уже создан при создании брони, метод лишь убеждается,
    что счёт есть; оплата приходит вебхуком (дедуп по external id + сверка
    суммы — в payments/api.py);
  - balance — charge с баланса (в пределах остатка, lock мастера);
- вебхук/manual-отметка paid → topup + бронь подтверждена (paid_amount=final);
- hold_deadline прошёл → истекла_оплата + счёт expired (beat booking_holds);
- отмена подтверждённой брони: раньше чем за cancel_full_refund_hours до
  начала → refund 100%; позже → удержание no_show_hold_percent % (т.е.
  refund = final × (100−hold)/100); отмена площадкой → всегда 100%;
- неявка → удержание no_show_hold_percent %.

Все check+insert — в одной транзакции; charge сериализуется advisory lock'ом
по мастеру (lock_master_slot в db/locks.py) — параллельные charge не уходят
в минус.
"""

import logging
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import load_only

from tg_studio.db.locks import for_update, lock_master_slot
from tg_studio.db.models import (
    Booking,
    BookingEventKind,
    BookingStatus,
    Business,
    LedgerEntry,
    LedgerEntryKind,
    Master,
    Payment,
    PaymentKind,
    PaymentProvider,
    PaymentStatus,
)
from tg_studio.modules.coworking.pricing import CURRENCY
from tg_studio.modules.coworking.service import add_booking_event, coworking_settings
from tg_studio.modules.payments.providers import (
    InvoiceContext,
    PaymentProviderError,
    apipay_configured,
    get_provider,
)
from tg_studio.modules.payments.service import normalize_phone as _normalize_phone

logger = logging.getLogger(__name__)

_CENT = Decimal("0.01")


def _dec(value) -> Decimal:
    return Decimal(str(value))


def _round2(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def booking_price(booking: Booking) -> Decimal:
    """Стоимость брони из снимка цены (единственный источник)."""
    snapshot = booking.price_snapshot or {}
    return _dec(snapshot.get("final", 0))


async def master_balance(session, master_id: int) -> Decimal:
    """Остаток счёта мастера = Σ проводок (вычисляется, не хранится)."""
    result = await session.execute(
        select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(
            LedgerEntry.master_id == master_id
        )
    )
    return _round2(_dec(result.scalar_one()))


async def _add_entry(
    session,
    *,
    business_id: int,
    master_id: int,
    kind: LedgerEntryKind,
    amount: Decimal,
    payment_id: int | None = None,
    booking_id: int | None = None,
    actor_user_id: int | None = None,
    note: str | None = None,
) -> LedgerEntry:
    entry = LedgerEntry(
        business_id=business_id,
        master_id=master_id,
        kind=kind.value,
        amount=float(_round2(amount)),
        currency=CURRENCY,
        payment_id=payment_id,
        booking_id=booking_id,
        actor_user_id=actor_user_id,
        note=note,
    )
    session.add(entry)
    await session.flush()
    return entry


async def create_booking_invoice(
    session, business: Business, booking: Booking, user_id: int
) -> Payment | None:
    """Счёт на 100% стоимости брони при создании (POST /bookings).

    Провайдер: apipay, если подключён, иначе manual pending (мастер/владелец
    подтвердит оплату кнопкой). Идемпотентен: повторный вызов возвращает уже
    созданный pending-счёт на эту бронь. None — цена 0 (бесплатная бронь).

    Счёт коммитится вызывающим до обращения к провайдеру; сбой apipay →
    счёт остаётся manual, бронь не срывается.
    """
    existing = (
        await session.execute(
            select(Payment).where(
                Payment.booking_id == booking.id,
                Payment.status == PaymentStatus.pending.value,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    amount = _round2(booking_price(booking))
    if amount <= 0:
        return None

    master = (
        await session.execute(
            select(Master).options(load_only(Master.id, Master.phone, Master.full_name)).where(
                Master.id == booking.master_id
            )
        )
    ).scalar_one_or_none()
    phone = _normalize_phone(master.phone) if master else None
    provider_name = (
        PaymentProvider.apipay.value
        if apipay_configured() and phone
        else PaymentProvider.manual.value
    )
    payment = Payment(
        business_id=business.id,
        booking_id=booking.id,
        amount=float(amount),
        kind=PaymentKind.prepay.value,
        status=PaymentStatus.pending.value,
        provider=provider_name,
        created_by_user_id=user_id,
        master_id=booking.master_id,
        expires_at=booking.hold_deadline,
        note=f"100% предоплата брони места #{booking.id}",
    )
    session.add(payment)
    await session.flush()

    if provider_name == PaymentProvider.apipay.value:
        try:
            created = await get_provider(provider_name).create_invoice(
                payment,
                InvoiceContext(
                    client_phone=phone,
                    client_name=master.full_name if master else "",
                    description=f"Бронь места #{booking.id}"[:60],
                ),
            )
        except PaymentProviderError as exc:
            # ApiPay недоступен — счёт остаётся manual, бронь не срывается
            logger.warning("Apipay booking invoice failed for booking %s: %s — fallback manual", booking.id, exc)
            payment.provider = PaymentProvider.manual.value
            payment.external_invoice_id = None
        else:
            payment.external_invoice_id = created.external_id
            payment.payment_url = created.payment_url
            payment.expires_at = created.expires_at or booking.hold_deadline
    return payment


async def apply_payment_paid(
    session, payment: Payment, *, paid_at: datetime | None = None, actor_user_id: int | None = None
) -> bool:
    """Отметка счёта оплаченным (вебхук apipay / ручное подтверждение).

    Идемпотентна: дедуп по проводке — если topup с этим payment_id уже есть,
    повторный вебхук/отметка — no-op (сам статус мог уже стать paid в
    apply_provider_status до вызова — поэтому дедуп по ledger, а не по статусу).
    Пишет проводку topup и, если счёт привязан к брони, подтверждает бронь
    (paid_amount = final → подтверждена). Возвращает True, если что-то изменилось.
    """
    existing_entry = (
        await session.execute(
            select(LedgerEntry).where(
                LedgerEntry.payment_id == payment.id, LedgerEntry.kind == LedgerEntryKind.topup.value
            )
        )
    ).scalar_one_or_none()
    if existing_entry is not None:
        return False
    if payment.status not in (PaymentStatus.pending.value, PaymentStatus.paid.value):
        if actor_user_id is not None:
            raise HTTPException(
                status_code=409, detail=f"Счёт в статусе {payment.status} — оплату не применить"
            )
    payment.status = PaymentStatus.paid.value
    if payment.paid_at is None:
        payment.paid_at = paid_at or datetime.now(UTC)
    if actor_user_id is not None:
        payment.confirmed_by_user_id = actor_user_id

    await _add_entry(
        session,
        business_id=payment.business_id,
        master_id=payment.master_id,
        kind=LedgerEntryKind.topup,
        amount=_dec(payment.amount),
        payment_id=payment.id,
        booking_id=payment.booking_id,
        actor_user_id=actor_user_id,
        note=payment.note,
    )

    if payment.booking_id is not None:
        booking = await session.get(Booking, payment.booking_id)
        if booking is not None and booking.status == BookingStatus.pending_payment.value:
            price = booking_price(booking)
            booking.paid_amount = float(_round2(_dec(payment.amount) + _dec(booking.paid_amount)))
            if _dec(booking.paid_amount) >= price:
                booking.status = BookingStatus.confirmed.value
                booking.hold_deadline = None
                add_booking_event(
                    session,
                    booking.business_id,
                    booking.id,
                    BookingEventKind.paid,
                    actor_user_id,
                    {
                        "method": "card",
                        "payment_id": payment.id,
                        "amount": float(_dec(payment.amount)),
                        "paid_amount": booking.paid_amount,
                        "price": float(price),
                        "currency": CURRENCY,
                    },
                )
    return True


async def pay_booking_from_balance(
    session, business: Business, booking: Booking, user_id: int
) -> Booking:
    """Оплата брони «с баланса»: charge в пределах остатка (lock мастера).

    Бронь должна быть ожидает_оплаты; цена — из снимка. Недостаточно средств
    → 409. Успех → charge + paid_amount=final + подтверждена + событие.
    """
    if booking.status != BookingStatus.pending_payment.value:
        raise HTTPException(
            status_code=409, detail=f"Оплатить с баланса можно только ожидающую оплаты бронь (сейчас: {booking.status})"
        )
    price = booking_price(booking)
    if price <= 0:
        raise HTTPException(status_code=409, detail="У брони нулевая цена — оплата не нужна")

    await lock_master_slot(session, booking.master_id)
    balance = await master_balance(session, booking.master_id)
    if balance < price:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Недостаточно средств на балансе",
                "balance": float(balance),
                "required": float(price),
                "currency": CURRENCY,
            },
        )
    booking.paid_amount = float(_round2(price))
    booking.status = BookingStatus.confirmed.value
    booking.hold_deadline = None
    await _add_entry(
        session,
        business_id=business.id,
        master_id=booking.master_id,
        kind=LedgerEntryKind.charge,
        amount=-price,
        booking_id=booking.id,
        actor_user_id=user_id,
        note=f"Оплата брони места #{booking.id} с баланса",
    )
    add_booking_event(
        session,
        business.id,
        booking.id,
        BookingEventKind.paid,
        user_id,
        {
            "method": "balance",
            "amount": float(price),
            "paid_amount": booking.paid_amount,
            "price": float(price),
            "currency": CURRENCY,
        },
    )
    return booking


def cancel_refund_amount(booking: Booking, settings: dict, *, by_studio: bool) -> Decimal:
    """Сколько вернуть при отмене подтверждённой брони (правило 24ч).

    by_studio → всегда 100%. Иначе: до начала осталось ≥ cancel_full_refund_hours
    → 100%; позже → удержание no_show_hold_percent % от цены, возврат остатка.
    """
    price = booking_price(booking)
    if by_studio or price <= 0:
        return price
    now = datetime.now(UTC)
    start = booking.start
    if start.tzinfo is None:
        # SQLite в тестах возвращает naive — колонка timestamptz, время UTC
        start = start.replace(tzinfo=UTC)
    hours_left = (start - now).total_seconds() / 3600
    full_hours = int(settings["cancel_full_refund_hours"])
    # полный возврат — если до начала осталось не меньше порога; порог 0 = никогда
    if full_hours > 0 and hours_left >= full_hours:
        return price
    hold_percent = _dec(settings["no_show_hold_percent"]) / Decimal(100)
    hold = _round2(price * hold_percent)
    return _round2(price - hold)


async def apply_cancellation_refund(
    session,
    business: Business,
    booking: Booking,
    actor_user_id: int,
    *,
    by_studio: bool,
    reason: str | None,
) -> Decimal:
    """Возврат по отмене подтверждённой брони (или неявки): refund-проводка.

    Возвращает сумму возврата (0 — удержали всё). Проводка пишется всегда,
    когда была предоплата (paid_amount > 0) — даже при нулевом возврате нет:
    удержание — это не проводка, деньги просто не возвращаем.
    """
    refund = cancel_refund_amount(booking, coworking_settings(business), by_studio=by_studio)
    paid = _dec(booking.paid_amount)
    if paid <= 0:
        return Decimal("0")
    refund = min(refund, paid)
    if refund > 0:
        await _add_entry(
            session,
            business_id=business.id,
            master_id=booking.master_id,
            kind=LedgerEntryKind.refund,
            amount=refund,
            booking_id=booking.id,
            actor_user_id=actor_user_id,
            note=f"Возврат по отмене брони #{booking.id} ({'площадкой' if by_studio else 'мастером'}: {reason or '—'})",
        )
        add_booking_event(
            session,
            business.id,
            booking.id,
            BookingEventKind.refunded,
            actor_user_id,
            {
                "amount": float(refund),
                "held": float(_round2(paid - refund)),
                "paid_amount": float(paid),
                "by_studio": by_studio,
                "reason": reason,
                "currency": CURRENCY,
            },
        )
    return refund


async def expire_booking_payment(session, booking: Booking) -> Payment | None:
    """Истёкший hold: бронь → истекла_оплата, её pending-счёт → expired."""
    payment = (
        await session.execute(
            for_update(
                select(Payment).where(
                    Payment.booking_id == booking.id,
                    Payment.status == PaymentStatus.pending.value,
                ),
                session,
            )
        )
    ).scalar_one_or_none()
    if payment is not None:
        payment.status = PaymentStatus.expired.value
    return payment


async def manual_topup(
    session,
    business: Business,
    master: Master,
    amount: Decimal,
    user_id: int,
    note: str | None,
) -> Payment:
    """Ручное пополнение баланса мастера (админ принял наличные — 05; здесь
    владелец). Создаёт оплаченный manual-платёж + проводку topup."""
    amount = _round2(amount)
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Сумма пополнения должна быть положительной")
    payment = Payment(
        business_id=business.id,
        amount=float(amount),
        kind=PaymentKind.prepay.value,
        status=PaymentStatus.paid.value,
        provider=PaymentProvider.manual.value,
        created_by_user_id=user_id,
        confirmed_by_user_id=user_id,
        master_id=master.id,
        paid_at=datetime.now(UTC),
        note=note or "Ручное пополнение баланса",
    )
    session.add(payment)
    await session.flush()
    await _add_entry(
        session,
        business_id=business.id,
        master_id=master.id,
        kind=LedgerEntryKind.topup,
        amount=amount,
        payment_id=payment.id,
        actor_user_id=user_id,
        note=payment.note,
    )
    return payment


async def adjust_balance(
    session,
    business: Business,
    master: Master,
    amount: Decimal,
    user_id: int,
    reason: str,
) -> LedgerEntry:
    """Ручная корректировка баланса (владелец): author + основание обязательны."""
    if not reason or not reason.strip():
        raise HTTPException(status_code=400, detail="Укажите основание корректировки")
    amount = _round2(amount)
    if amount == 0:
        raise HTTPException(status_code=400, detail="Сумма корректировки не должна быть нулевой")
    return await _add_entry(
        session,
        business_id=business.id,
        master_id=master.id,
        kind=LedgerEntryKind.adjust,
        amount=amount,
        actor_user_id=user_id,
        note=reason.strip(),
    )
