"""Склад и продажи коворкинга (06): движения, закупка, обеспечение, продажи, возвраты.

Инварианты:
- Остаток = Σ движений; quantity обновляется в той же транзакции, что и движение.
- Минус запрещён: списания сериализуются advisory lock'ом по позиции (Postgres).
- Цена продажи и себестоимость в позициях sale фиксируются на момент продажи.
- Продажа + оплата + апсейл — одна транзакция; при нехватке остатка/денег — 409
  без частичных изменений.
- Возвраты — только сторнирующими движениями + refund-проводкой (мастеру).
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException
from sqlalchemy import select

from tg_studio.db.locks import for_update, lock_master_slot, lock_sale, lock_supply
from tg_studio.db.models import (
    Booking,
    BookingEventKind,
    Business,
    LedgerEntry,
    LedgerEntryKind,
    Master,
    Payment,
    PaymentKind,
    PaymentProvider,
    PaymentStatus,
    Sale,
    SaleItem,
    SaleStatus,
    StudioAdmin,
    Supply,
    SupplyMovement,
    SupplyMovementKind,
    Upsell,
)
from tg_studio.modules.payments.providers import normalize_phone
from tg_studio.modules.coworking import money
from tg_studio.modules.coworking.pricing import CURRENCY
from tg_studio.modules.coworking.service import add_booking_event
from tg_studio.modules.coworking.stock_schemas import SaleCreate, SaleRefundRequest

logger = logging.getLogger(__name__)

_CENT = Decimal("0.01")


@dataclass
class LowStockSnapshot:
    """Снимок для post-commit уведомления: переживает rollback/reload sale."""

    id: int
    name: str
    quantity: float
    unit: str
    min_quantity: float | None


def _dec(value) -> Decimal:
    return Decimal(str(value))


def _round2(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def _fmt_qty(value) -> str:
    return f"{float(value):.2f}".rstrip("0").rstrip(".")


async def get_supply(session, business_id: int, supply_id: int, *, include_inactive: bool = False) -> Supply:
    supply = (
        await session.execute(
            select(Supply).where(Supply.id == supply_id, Supply.business_id == business_id)
        )
    ).scalar_one_or_none()
    if supply is None or (not include_inactive and not supply.is_active):
        raise HTTPException(status_code=404, detail="Товар не найден")
    return supply


async def locked_supply(
    session, business_id: int, supply_id: int, *, include_inactive: bool = False
) -> Supply:
    """Позиция под advisory lock; остаток перечитан ПОСЛЕ ожидания блокировки.

    Второй конкурентный запрос должен увидеть commit первого, а не свой старый
    snapshot. На Postgres запрос идёт с populate_existing + FOR UPDATE, на
    SQLite блокировка no-op, но свежие значения всё равно читаются.
    """
    await lock_supply(session, supply_id)
    stmt = (
        select(Supply)
        .where(Supply.id == supply_id, Supply.business_id == business_id)
        .execution_options(populate_existing=True)
    )
    supply = (
        await session.execute(for_update(stmt, session))
    ).scalar_one_or_none()
    if supply is None or (not include_inactive and not supply.is_active):
        raise HTTPException(status_code=404, detail="Товар не найден")
    return supply


def _crossed_low(supply: Supply, new_quantity: Decimal) -> bool:
    if supply.min_quantity is None:
        return False
    return bool(
        new_quantity <= _dec(supply.min_quantity) and _dec(supply.quantity) > _dec(supply.min_quantity)
    )


async def notify_low_stock(session, supply: Supply) -> None:
    """Telegram-уведомление владельцу о низком остатке (best-effort)."""
    from tg_studio.modules.notify import resolve_owner_telegram_id, send_text_to_telegram

    try:
        owner_tg_id = await resolve_owner_telegram_id(session)
    except Exception:
        logger.exception("Failed to resolve owner for low-stock notification")
        return
    if not owner_tg_id:
        return
    text = (
        f"⚠️ Товар заканчивается: «{supply.name}» — "
        f"осталось {_fmt_qty(supply.quantity)} {supply.unit}"
        + (f" (минимум {_fmt_qty(supply.min_quantity)})" if supply.min_quantity is not None else "")
    )
    try:
        await send_text_to_telegram(owner_tg_id, text)
    except Exception:
        logger.warning("Не удалось отправить уведомление о складе «%s»", supply.name, exc_info=True)


def _add_movement(
    session,
    *,
    business_id: int,
    supply_id: int,
    delta: Decimal,
    kind: SupplyMovementKind,
    actor_user_id: int | None = None,
    sale_id: int | None = None,
    booking_id: int | None = None,
    master_id: int | None = None,
    note: str | None = None,
) -> SupplyMovement:
    movement = SupplyMovement(
        business_id=business_id,
        supply_id=supply_id,
        delta=float(delta),
        kind=kind.value,
        actor_user_id=actor_user_id,
        sale_id=sale_id,
        booking_id=booking_id,
        master_id=master_id,
        note=note,
    )
    session.add(movement)
    return movement


async def purchase(
    session,
    business: Business,
    supply: Supply,
    qty: Decimal,
    unit_cost: Decimal | None,
    actor_user_id: int,
    note: str | None,
) -> Supply:
    """Закупка: остаток растёт, себестоимость — средневзвешенная."""
    await lock_supply(session, supply.id)
    await session.refresh(supply)
    qty = _round2(qty)
    if qty <= 0:
        raise HTTPException(status_code=400, detail="Количество должно быть положительным")
    old_qty = _dec(supply.quantity)
    new_qty = old_qty + qty
    if unit_cost is not None:
        # цены закупки у позиции ещё не было — старый остаток оцениваем по новой
        old_cost = _dec(supply.purchase_price) if supply.purchase_price is not None else _dec(unit_cost)
        total_cost = old_qty * old_cost + qty * _dec(unit_cost)
        supply.purchase_price = float(_round2(total_cost / new_qty)) if new_qty > 0 else 0
    supply.quantity = float(new_qty)
    _add_movement(
        session,
        business_id=business.id,
        supply_id=supply.id,
        delta=qty,
        kind=SupplyMovementKind.purchase,
        actor_user_id=actor_user_id,
        note=note,
    )
    return supply


async def consume(
    session,
    business: Business,
    supply: Supply,
    qty: Decimal,
    reason: str,
    actor_user_id: int,
    booking_id: int | None,
) -> tuple[Supply, bool]:
    """Обеспечение площадки: списание без оплаты. Возвращает (supply, crossed_low)."""
    await lock_supply(session, supply.id)
    await session.refresh(supply)
    qty = _round2(qty)
    if qty <= 0:
        raise HTTPException(status_code=400, detail="Количество должно быть положительным")
    new_qty = _dec(supply.quantity) - qty
    if new_qty < 0:
        raise HTTPException(
            status_code=409,
            detail=f"На складе только {_fmt_qty(supply.quantity)} {supply.unit}",
        )
    crossed = _crossed_low(supply, new_qty)
    supply.quantity = float(new_qty)
    _add_movement(
        session,
        business_id=business.id,
        supply_id=supply.id,
        delta=-qty,
        kind=SupplyMovementKind.consume,
        actor_user_id=actor_user_id,
        booking_id=booking_id,
        note=reason,
    )
    return supply, crossed


async def adjust(
    session,
    business: Business,
    supply: Supply,
    new_quantity: Decimal,
    reason: str,
    actor_user_id: int,
) -> tuple[Supply, bool]:
    """Ревизия: выставить фактический остаток."""
    await lock_supply(session, supply.id)
    await session.refresh(supply)
    new_quantity = _round2(new_quantity)
    delta = new_quantity - _dec(supply.quantity)
    if delta == 0:
        raise HTTPException(status_code=409, detail=f"Остаток уже {_fmt_qty(supply.quantity)} {supply.unit}")
    crossed = _crossed_low(supply, new_quantity)
    supply.quantity = float(new_quantity)
    _add_movement(
        session,
        business_id=business.id,
        supply_id=supply.id,
        delta=delta,
        kind=SupplyMovementKind.adjust,
        actor_user_id=actor_user_id,
        note=reason,
    )
    return supply, crossed


async def create_sale(
    session,
    business: Business,
    body: SaleCreate,
    actor_user_id: int,
    self_admin: StudioAdmin | None,
) -> Sale:
    """Продажа: корзина → остатки → sale + движения + оплата + апсейл (одна транзакция).

    При нехватке остатка или средств — 409 без частичных изменений (rollback
    выполняет вызывающий локально вокруг create_sale/commit).
    """
    if body.payment_method == "balance" and body.master_id is None:
        raise HTTPException(status_code=422, detail="Для оплаты с баланса выберите мастера")
    if body.master_id is None and not (body.buyer_name or "").strip():
        raise HTTPException(status_code=422, detail="Укажите покупателя")
    buyer_name = buyer_phone = None
    if body.master_id is None:
        buyer_name = body.buyer_name.strip()
        if (body.buyer_phone or "").strip():
            buyer_phone = normalize_phone(body.buyer_phone)
            if buyer_phone is None:
                raise HTTPException(status_code=422, detail="Телефон должен быть вида 8XXXXXXXXXX")
    if body.booking_id is not None:
        booking = (
            await session.execute(
                select(Booking).where(Booking.id == body.booking_id, Booking.business_id == business.id)
            )
        ).scalar_one_or_none()
        if booking is None:
            raise HTTPException(status_code=404, detail="Бронь не найдена")
    master = None
    if body.master_id is not None:
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

    # Списание остатков по каждой позиции. Lock порядок фиксирован по id:
    # одинаковые товары в корзине агрегируются, дедлоков между корзинами нет.
    quantities: dict[int, Decimal] = {}
    for item in body.items:
        qty = _round2(_dec(item.qty))
        if qty <= 0:
            raise HTTPException(status_code=422, detail="Количество должно быть положительным")
        quantities[item.supply_id] = quantities.get(item.supply_id, Decimal("0")) + qty

    items: list[SaleItem] = []
    low_stock: list[Supply] = []
    total = Decimal("0")
    for supply_id, qty in sorted(quantities.items()):
        supply = await locked_supply(session, business.id, supply_id)
        if not supply.sale_price:
            raise HTTPException(status_code=409, detail=f"Товар «{supply.name}» не продаётся (нет цены)")
        new_qty = _dec(supply.quantity) - qty
        if new_qty < 0:
            raise HTTPException(
                status_code=409,
                detail=f"Недостаточно «{supply.name}»: на складе {_fmt_qty(supply.quantity)} {supply.unit}, нужно {_fmt_qty(qty)}",
            )
        if _crossed_low(supply, new_qty):
            low_stock.append(supply)
        unit_price = _dec(supply.sale_price)
        unit_cost = _dec(supply.purchase_price or 0)
        amount = _round2(qty * unit_price)
        total += amount
        supply.quantity = float(new_qty)
        items.append(
            SaleItem(
                supply_id=supply.id,
                qty=float(qty),
                unit_price=float(unit_price),
                unit_cost=float(unit_cost),
                amount=float(amount),
            )
        )

    total = _round2(total)
    sale = Sale(
        business_id=business.id,
        master_id=master.id if master else None,
        buyer_name=buyer_name,
        buyer_phone=buyer_phone,
        total_amount=float(total),
        currency=CURRENCY,
        payment_method=body.payment_method,
        status=SaleStatus.paid.value,
        admin_id=self_admin.id if self_admin else None,
        created_by_user_id=actor_user_id,
        booking_id=body.booking_id,
        note=body.note,
    )
    sale.items = items
    session.add(sale)
    await session.flush()

    # Движения склада (продажа)
    for supply_id, qty in sorted(quantities.items()):
        _add_movement(
            session,
            business_id=business.id,
            supply_id=supply_id,
            delta=-qty,
            kind=SupplyMovementKind.sale,
            actor_user_id=actor_user_id,
            sale_id=sale.id,
            booking_id=body.booking_id,
            master_id=master.id if master else None,
            note=body.note,
        )

    # Оплата
    if body.payment_method == "balance":
        await lock_master_slot(session, master.id)
        balance = await money.master_balance(session, master.id)
        if balance < total:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "Недостаточно средств на балансе",
                    "balance": float(balance),
                    "required": float(total),
                    "currency": CURRENCY,
                },
            )
        session.add(
            LedgerEntry(
                business_id=business.id,
                master_id=master.id,
                kind=LedgerEntryKind.charge.value,
                amount=float(-total),
                currency=CURRENCY,
                sale_id=sale.id,
                actor_user_id=actor_user_id,
                note=f"Оплата продажи #{sale.id} с баланса",
            )
        )
    else:
        # Наличные/карта: ручной платёж, оплачен сразу (админ принял деньги)
        payment = Payment(
            business_id=business.id,
            amount=float(total),
            kind=PaymentKind.final.value,
            status=PaymentStatus.paid.value,
            provider=PaymentProvider.manual.value,
            created_by_user_id=actor_user_id,
            confirmed_by_user_id=actor_user_id,
            master_id=master.id if master else None,
            sale_id=sale.id,
            paid_at=datetime.now(UTC),
            note=f"Продажа #{sale.id} ({'наличные' if body.payment_method == 'cash' else 'карта'})",
        )
        session.add(payment)
        await session.flush()
        # Продажа мастеру за наличные/карту: учёт как у брони (04) — topup на
        # принятую сумму + charge за товар, баланс не меняется, а возврат
        # (refund на баланс) вернёт деньги туда же, откуда они учтены.
        if master is not None:
            how = "наличные" if body.payment_method == "cash" else "карта"
            for kind, amount, note, payment_id, sale_id in (
                (LedgerEntryKind.topup, total, f"Оплата продажи #{sale.id} ({how})", payment.id, None),
                (LedgerEntryKind.charge, -total, f"Оплата продажи #{sale.id} ({how})", None, sale.id),
            ):
                session.add(
                    LedgerEntry(
                        business_id=business.id,
                        master_id=master.id,
                        kind=kind.value,
                        amount=float(amount),
                        currency=CURRENCY,
                        payment_id=payment_id,
                        sale_id=sale_id,
                        actor_user_id=actor_user_id,
                        note=note,
                    )
                )

    # Апсейл (05): автор — админ
    if self_admin is not None:
        session.add(
            Upsell(
                business_id=business.id,
                admin_id=self_admin.id,
                sale_id=sale.id,
                master_id=master.id if master else None,
                buyer_name=sale.buyer_name,
                buyer_phone=sale.buyer_phone,
                amount=float(total),
                currency=CURRENCY,
                booking_id=body.booking_id,
                note=body.note or "Продажа со склада",
                created_by_user_id=actor_user_id,
            )
        )

    sale._low_stock = low_stock  # для post-commit уведомлений (не колонка)
    return sale


async def refund_sale(
    session,
    business: Business,
    sale: Sale,
    body: SaleRefundRequest,
    actor_user_id: int,
) -> tuple[Sale, Decimal]:
    """Возврат: сторнирующие движения + refund-проводка мастеру (если оплата была).

    Полный возврат (items=None) или частичный по позициям. Возвращает (sale, сумма).
    """
    if sale.status != SaleStatus.paid.value:
        raise HTTPException(status_code=409, detail=f"Возврат возможен только по оплаченной продаже (сейчас: {sale.status})")

    # Один возврат за раз на чек. Перечитываем позиции после ожидания:
    # предыдущий конкурентный частичный возврат уже мог стать полным.
    await lock_sale(session, sale.id)
    await session.refresh(sale, attribute_names=["status"])
    if sale.status != SaleStatus.paid.value:
        raise HTTPException(status_code=409, detail="Продажа уже полностью возвращена")
    from sqlalchemy.orm import selectinload

    sale = (
        await session.execute(
            select(Sale)
            .options(selectinload(Sale.items))
            .where(Sale.id == sale.id, Sale.business_id == business.id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()

    previous = (
        await session.execute(
            select(SupplyMovement.supply_id, SupplyMovement.delta).where(
                SupplyMovement.sale_id == sale.id,
                SupplyMovement.kind == SupplyMovementKind.refund.value,
            )
        )
    ).all()
    returned: dict[int, Decimal] = {}
    for supply_id, delta in previous:
        returned[supply_id] = returned.get(supply_id, Decimal("0")) + _dec(delta)

    quantities: dict[int, Decimal] = {}
    if body.items is None:
        # полный возврат = всё, что ещё не возвращено
        quantities = {
            i.supply_id: _dec(i.qty) - returned.get(i.supply_id, Decimal("0"))
            for i in sale.items
            if _dec(i.qty) > returned.get(i.supply_id, Decimal("0"))
        }
    else:
        for item in body.items:
            qty = _round2(_dec(item.qty))
            if qty <= 0:
                raise HTTPException(status_code=400, detail="Количество возврата должно быть положительным")
            quantities[item.supply_id] = quantities.get(item.supply_id, Decimal("0")) + qty

    refund_total = Decimal("0")
    low_stock: list[Supply] = []
    for supply_id, qty in sorted(quantities.items()):
        orig = next((i for i in sale.items if i.supply_id == supply_id), None)
        if orig is None:
            raise HTTPException(status_code=400, detail=f"Позиция supply_id={supply_id} не была в продаже")
        available = _dec(orig.qty) - returned.get(supply_id, Decimal("0"))
        if qty > available:
            raise HTTPException(
                status_code=409,
                detail=f"По позиции supply_id={supply_id} можно вернуть не больше {_fmt_qty(max(available, Decimal('0')))}",
            )
        supply = await locked_supply(session, business.id, supply_id, include_inactive=True)
        supply.quantity = float(_dec(supply.quantity) + qty)
        amount = _round2(qty * _dec(orig.unit_price))
        refund_total += amount
        _add_movement(
            session,
            business_id=business.id,
            supply_id=supply.id,
            delta=qty,
            kind=SupplyMovementKind.refund,
            actor_user_id=actor_user_id,
            sale_id=sale.id,
            booking_id=sale.booking_id,
            master_id=sale.master_id,
            note=f"Возврат по продаже #{sale.id}: {body.reason}",
        )

    refund_total = _round2(refund_total)
    if refund_total <= 0:
        raise HTTPException(status_code=400, detail="Сумма возврата равна нулю")

    # Деньги: мастеру — refund на баланс (и для balance, и для cash/card:
    # деньги возвращаются через счёт мастера); разовому контакту — деньги
    # возвращает админ наличными, проводки нет.
    if sale.master_id is not None:
        session.add(
            LedgerEntry(
                business_id=business.id,
                master_id=sale.master_id,
                kind=LedgerEntryKind.refund.value,
                amount=float(refund_total),
                currency=CURRENCY,
                sale_id=sale.id,
                actor_user_id=actor_user_id,
                note=f"Возврат по продаже #{sale.id}: {body.reason}",
            )
        )

    # Статус: полный возврат → возвращена; частичный остаётся оплаченной
    # (сумма возврата видна в движениях/ledger).
    full = all(
        returned.get(i.supply_id, Decimal("0")) + quantities.get(i.supply_id, Decimal("0"))
        >= _dec(i.qty)
        for i in sale.items
    )
    if full:
        sale.status = SaleStatus.refunded.value

    # Апсейл (05) уменьшается на возвращённую сумму: админу не засчитываем вернувшуюся продажу
    upsell = (
        await session.execute(select(Upsell).where(Upsell.sale_id == sale.id))
    ).scalar_one_or_none()
    if upsell is not None:
        upsell.amount = float(max(_dec(upsell.amount) - refund_total, Decimal("0")))

    # Факт возврата нужен отчётности 07 в том числе для гостевой наличной
    # продажи, где нет ledger. Событие связано с исходной бронью (если она
    # есть); в JSON храним sale_id и замороженные суммы по позициям.
    add_booking_event(
        session,
        business.id,
        sale.booking_id,
        BookingEventKind.refunded,
        actor_user_id,
        {
            "sale_id": sale.id,
            "amount": float(refund_total),
            "qty_by_supply": {str(supply_id): float(qty) for supply_id, qty in quantities.items()},
            "reason": body.reason,
            "currency": CURRENCY,
        },
    )
    sale._low_stock = low_stock
    return sale, refund_total
