"""API склада и продаж коворкинга (06).

Роли:
- мастер: каталог read-only + покупка со своего баланса (POST /sales, balance, на себя);
- админ: закупка/обеспечение/ревизия, продажи (касса), возвраты, журнал;
- владелец: всё то же + управление карточками товаров.
"""

from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from tg_studio.api.admin_deps import AdminBusinessAndSelfDep, MasterBusinessAndSelfDep, OwnerBusinessDep
from tg_studio.api.auth import CurrentUserDep
from tg_studio.api.deps import SessionDep
from tg_studio.db.models import (
    Booking,
    Master,
    Payment,
    PaymentStatus,
    Sale,
    SaleStatus,
    StudioAdmin,
    Supply,
    SupplyMovement,
    SupplyMovementKind,
)
from tg_studio.modules.coworking import stock
from tg_studio.modules.coworking.stock_schemas import (
    ProductCreate,
    ProductListResponse,
    ProductOut,
    ProductUpdate,
    SaleCreate,
    SaleItemOut,
    SaleListResponse,
    SaleOut,
    SaleRefundRequest,
    StockAdjust,
    StockConsume,
    StockMovementListResponse,
    StockMovementOut,
    StockPurchase,
)
from tg_studio.modules.coworking.service import ensure_tz

router = APIRouter(tags=["coworking-stock"])


# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------


async def _get_sale(session, business_id: int, sale_id: int) -> Sale:
    sale = (
        await session.execute(
            select(Sale)
            .options(selectinload(Sale.items))
            .where(Sale.id == sale_id, Sale.business_id == business_id)
        )
    ).scalar_one_or_none()
    if sale is None:
        raise HTTPException(status_code=404, detail="Продажа не найдена")
    return sale


async def _sale_out(session, sale: Sale, *, with_payment_url: bool = False) -> SaleOut:
    master = await session.get(Master, sale.master_id) if sale.master_id else None
    admin = await session.get(StudioAdmin, sale.admin_id) if sale.admin_id else None
    items = []
    for i in sale.items:
        supply = await session.get(Supply, i.supply_id)
        items.append(
            SaleItemOut(
                id=i.id,
                supply_id=i.supply_id,
                supply_name=supply.name if supply else None,
                qty=float(i.qty),
                unit_price=float(i.unit_price),
                unit_cost=float(i.unit_cost),
                amount=float(i.amount),
            )
        )
    payment_url = None
    if with_payment_url:
        payment = (
            await session.execute(
                select(Payment).where(Payment.sale_id == sale.id).order_by(Payment.id.desc()).limit(1)
            )
        ).scalar_one_or_none()
        payment_url = payment.payment_url if payment else None
    return SaleOut(
        id=sale.id,
        business_id=sale.business_id,
        master_id=sale.master_id,
        master_name=master.full_name if master else None,
        buyer_name=sale.buyer_name,
        buyer_phone=sale.buyer_phone,
        total_amount=float(sale.total_amount),
        currency=sale.currency,
        payment_method=sale.payment_method,
        status=sale.status,
        admin_id=sale.admin_id,
        admin_name=admin.full_name if admin else None,
        created_by_user_id=sale.created_by_user_id,
        booking_id=sale.booking_id,
        note=sale.note,
        created_at=sale.created_at,
        items=items,
        payment_url=payment_url,
    )


# ---------------------------------------------------------------------------
# Товары (products)
# ---------------------------------------------------------------------------


@router.get("/products", response_model=ProductListResponse)
async def list_products(
    session: SessionDep,
    actor: MasterBusinessAndSelfDep,
    category: str | None = Query(None),
    low_only: bool = Query(False),
    include_inactive: bool = Query(False),
):
    """Каталог склада: мастер — активные позиции с ценами; владелец/админ — всё."""
    business, _ = actor
    stmt = select(Supply).where(Supply.business_id == business.id)
    if not include_inactive:
        stmt = stmt.where(Supply.is_active.is_(True))
    if category is not None:
        stmt = stmt.where(Supply.category == category)
    if low_only:
        stmt = stmt.where(Supply.min_quantity.is_not(None), Supply.quantity <= Supply.min_quantity)
    rows = (await session.execute(stmt.order_by(Supply.category, Supply.name))).scalars().all()
    return ProductListResponse(products=[ProductOut.model_validate(s) for s in rows])


@router.post("/products", response_model=ProductOut, status_code=201)
async def create_product(
    body: ProductCreate,
    session: SessionDep,
    actor: AdminBusinessAndSelfDep,
    user: CurrentUserDep,
):
    """Новая позиция склада (владелец/админ). Начальный остаток — движением init."""
    business, _ = actor
    supply = Supply(
        business_id=business.id,
        name=body.name.strip(),
        category=body.category.strip(),
        unit=body.unit.strip(),
        quantity=body.quantity,
        min_quantity=body.min_quantity,
        purchase_price=body.purchase_price,
        sale_price=body.sale_price,
        note=body.note,
    )
    session.add(supply)
    await session.flush()
    if body.quantity != 0:
        stock._add_movement(
            session,
            business_id=business.id,
            supply_id=supply.id,
            delta=stock._dec(body.quantity),
            kind=SupplyMovementKind.init,
            actor_user_id=user.id,
            note="Начальный остаток",
        )
    await session.commit()
    await session.refresh(supply)
    return ProductOut.model_validate(supply)


@router.patch("/products/{product_id}", response_model=ProductOut)
async def update_product(
    product_id: int,
    body: ProductUpdate,
    session: SessionDep,
    actor: AdminBusinessAndSelfDep,
):
    """Правка карточки товара. Остаток меняется только движениями."""
    business, _ = actor
    supply = await stock.get_supply(session, business.id, product_id, include_inactive=True)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(supply, field, value)
    await session.commit()
    await session.refresh(supply)
    return ProductOut.model_validate(supply)


@router.delete("/products/{product_id}", status_code=204)
async def delete_product(
    product_id: int,
    session: SessionDep,
    actor: AdminBusinessAndSelfDep,
):
    """Мягкое удаление: история движений не теряет позицию."""
    business, _ = actor
    supply = await stock.get_supply(session, business.id, product_id)
    supply.is_active = False
    await session.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Движения: закупка, обеспечение, ревизия, журнал
# ---------------------------------------------------------------------------


@router.post("/stock/purchase", response_model=ProductOut)
async def stock_purchase(
    body: StockPurchase,
    session: SessionDep,
    actor: AdminBusinessAndSelfDep,
    user: CurrentUserDep,
):
    """Закупка: остаток растёт, средневзвешенная себестоимость."""
    business, _ = actor
    supply = await stock.get_supply(session, business.id, body.supply_id, include_inactive=True)
    if not supply.is_active:
        raise HTTPException(status_code=409, detail="Позиция в архиве")
    await stock.purchase(
        session, business, supply, stock._dec(body.qty),
        stock._dec(body.unit_cost) if body.unit_cost is not None else None,
        user.id, body.note,
    )
    await session.commit()
    await session.refresh(supply)
    return ProductOut.model_validate(supply)


@router.post("/stock/consume", response_model=ProductOut)
async def stock_consume(
    body: StockConsume,
    session: SessionDep,
    actor: AdminBusinessAndSelfDep,
    user: CurrentUserDep,
):
    """Обеспечение площадки: списание без оплаты, причина обязательна."""
    business, _ = actor
    supply = await stock.get_supply(session, business.id, body.supply_id, include_inactive=True)
    if not supply.is_active:
        raise HTTPException(status_code=409, detail="Позиция в архиве")
    if body.booking_id is not None:
        booking = (
            await session.execute(
                select(Booking).where(Booking.id == body.booking_id, Booking.business_id == business.id)
            )
        ).scalar_one_or_none()
        if booking is None:
            raise HTTPException(status_code=404, detail="Бронь не найдена")
    _, crossed = await stock.consume(
        session, business, supply, stock._dec(body.qty), body.reason, user.id, body.booking_id
    )
    await session.commit()
    await session.refresh(supply)
    if crossed:
        await stock.notify_low_stock(session, supply)
    return ProductOut.model_validate(supply)


@router.post("/stock/adjust", response_model=ProductOut)
async def stock_adjust(
    body: StockAdjust,
    session: SessionDep,
    actor: AdminBusinessAndSelfDep,
    user: CurrentUserDep,
):
    """Ревизия: выставить фактический остаток (основание обязательно)."""
    business, _ = actor
    supply = await stock.get_supply(session, business.id, body.supply_id, include_inactive=True)
    if not supply.is_active:
        raise HTTPException(status_code=409, detail="Позиция в архиве")
    _, crossed = await stock.adjust(
        session, business, supply, stock._dec(body.new_quantity), body.reason, user.id
    )
    await session.commit()
    await session.refresh(supply)
    if crossed:
        await stock.notify_low_stock(session, supply)
    return ProductOut.model_validate(supply)


@router.get("/stock/movements", response_model=StockMovementListResponse)
async def stock_movements(
    session: SessionDep,
    actor: AdminBusinessAndSelfDep,
    supply_id: int | None = Query(None),
    kind: str | None = Query(None),
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    limit: int = Query(200, ge=1, le=1000),
):
    """Журнал движений склада (владелец/админ)."""
    from datetime import datetime

    business, _ = actor
    stmt = (
        select(SupplyMovement, Supply.name, Master.full_name)
        .join(Supply, SupplyMovement.supply_id == Supply.id)
        .outerjoin(Master, SupplyMovement.master_id == Master.id)
        .where(SupplyMovement.business_id == business.id)
    )
    if supply_id is not None:
        stmt = stmt.where(SupplyMovement.supply_id == supply_id)
    if kind is not None:
        stmt = stmt.where(SupplyMovement.kind == kind)
    if from_:
        stmt = stmt.where(SupplyMovement.created_at >= ensure_tz(datetime.fromisoformat(from_)))
    if to:
        stmt = stmt.where(SupplyMovement.created_at < ensure_tz(datetime.fromisoformat(to)))
    rows = (await session.execute(stmt.order_by(SupplyMovement.id.desc()).limit(limit))).all()
    movements = [
        StockMovementOut(
            id=m.id,
            supply_id=m.supply_id,
            supply_name=supply_name,
            delta=float(m.delta),
            kind=m.kind,
            master_id=m.master_id,
            master_name=master_name,
            sale_id=m.sale_id,
            booking_id=m.booking_id,
            actor_user_id=m.actor_user_id,
            note=m.note,
            created_at=m.created_at,
        )
        for m, supply_name, master_name in rows
    ]
    return StockMovementListResponse(movements=movements, total=len(movements))


# ---------------------------------------------------------------------------
# Продажи
# ---------------------------------------------------------------------------


@router.get("/sales", response_model=SaleListResponse)
async def list_sales(
    session: SessionDep,
    actor: MasterBusinessAndSelfDep,
    master_id: int | None = Query(None),
    admin_id: int | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(200, ge=1, le=1000),
):
    """Продажи: мастер — только свои покупки; владелец/админ — все."""
    business, self_master = actor
    stmt = select(Sale).where(Sale.business_id == business.id)
    if self_master is not None:
        stmt = stmt.where(Sale.master_id == self_master.id)
    elif master_id is not None:
        stmt = stmt.where(Sale.master_id == master_id)
    if admin_id is not None:
        stmt = stmt.where(Sale.admin_id == admin_id)
    if status is not None:
        stmt = stmt.where(Sale.status == status)
    rows = (
        await session.execute(
            stmt.options(selectinload(Sale.items)).order_by(Sale.id.desc()).limit(limit)
        )
    ).scalars().unique().all()
    return SaleListResponse(sales=[await _sale_out(session, s) for s in rows], total=len(rows))


@router.post("/sales", response_model=SaleOut, status_code=201)
async def create_sale(
    body: SaleCreate,
    session: SessionDep,
    actor: MasterBusinessAndSelfDep,
    user: CurrentUserDep,
):
    """Продажа (касса). Мастер — только себе и только с баланса; админ/владелец —
    любому покупателю, любым способом. Атомарно: остатки + sale + оплата + апсейл.
    """
    business, self_master = actor
    self_admin = None
    if user.role.value == "admin":
        self_admin = (
            await session.execute(
                select(StudioAdmin).where(
                    StudioAdmin.user_id == user.id, StudioAdmin.is_active.is_(True)
                )
            )
        ).scalar_one_or_none()
    if self_master is not None:
        # Мастер покупает себе и только с баланса
        if body.master_id is not None and body.master_id != self_master.id:
            raise HTTPException(status_code=403, detail="Мастер покупает только себе")
        if body.payment_method != "balance":
            raise HTTPException(status_code=403, detail="Мастер оплачивает покупки только с баланса")
        body.master_id = self_master.id
    try:
        sale = await stock.create_sale(session, business, body, user.id, self_admin)
        low_stock = [
            {
                "id": s.id,
                "name": s.name,
                "quantity": float(s.quantity),
                "unit": s.unit,
                "min_quantity": float(s.min_quantity) if s.min_quantity is not None else None,
            }
            for s in sale._low_stock
        ]
        await session.commit()
    except Exception:
        # Атомарность продажи: нехватка остатка/средств — 409 без частичных
        # изменений (остатки уже могли быть изменены до проверки баланса).
        await session.rollback()
        raise
    sale = await _get_sale(session, business.id, sale.id)
    for snapshot in low_stock:
        await stock.notify_low_stock(session, stock.LowStockSnapshot(**snapshot))
    return await _sale_out(session, sale, with_payment_url=True)


@router.get("/sales/{sale_id}", response_model=SaleOut)
async def get_sale(
    sale_id: int,
    session: SessionDep,
    actor: MasterBusinessAndSelfDep,
):
    business, self_master = actor
    sale = await _get_sale(session, business.id, sale_id)
    if self_master is not None and sale.master_id != self_master.id:
        raise HTTPException(status_code=403, detail="Мастер видит только свои покупки")
    return await _sale_out(session, sale, with_payment_url=True)


@router.post("/sales/{sale_id}/refund", response_model=SaleOut)
async def refund_sale(
    sale_id: int,
    body: SaleRefundRequest,
    session: SessionDep,
    actor: AdminBusinessAndSelfDep,
    user: CurrentUserDep,
):
    """Возврат по продаже (админ/владелец): товар на склад + деньги мастеру."""
    business, _ = actor
    sale = await _get_sale(session, business.id, sale_id)
    try:
        sale, _ = await stock.refund_sale(session, business, sale, body, user.id)
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    sale = await _get_sale(session, business.id, sale.id)
    return await _sale_out(session, sale)
