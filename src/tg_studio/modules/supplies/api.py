"""API склада расходников (миниапп: владелец и мастера).

- мастер: видит остатки и может только списать (POST /{id}/use);
- владелец: полный CRUD, пополнение, ревизия, журнал движений.

Остаток хранится денормализованно в supplies.quantity и обновляется
транзакционно вместе с движением — журнал только история.
При переходе остатка через порог (min_quantity) владельцу уходит
уведомление в Telegram — best-effort, не ломает списание.
"""

import logging
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy import select

from tg_studio.api.admin_deps import MasterBusinessAndSelfDep, OwnerBusinessDep
from tg_studio.api.deps import SessionDep
from tg_studio.db.models import (
    Master,
    Supply,
    SupplyMovement,
    SupplyMovementKind,
    TattooSession,
    TattooWork,
)
from tg_studio.modules.chat.service import resolve_owner_telegram_id, send_text_to_telegram
from tg_studio.modules.supplies.schemas import (
    SupplyActionRequest,
    SupplyAdjustRequest,
    SupplyCreate,
    SupplyListResponse,
    SupplyMovementListResponse,
    SupplyMovementOut,
    SupplyOut,
    SupplyUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/supplies", tags=["supplies"])


# ---------------------------------------------------------------------------
# Уведомление владельцу о низком остатке
# ---------------------------------------------------------------------------


def _fmt_qty(value: float | Decimal) -> str:
    """7.0 → «7», 7.5 → «7.5»."""
    return f"{float(value):.2f}".rstrip("0").rstrip(".")


def _dec(value: float | Decimal) -> Decimal:
    return Decimal(str(value))


def _crossed_low(supply: Supply, new_quantity: Decimal) -> bool:
    """Переход через порог этим действием: было выше — стало не выше.

    Уведомляем именно на переходе, иначе каждое списание при уже низком
    остатке дёргало бы владельца заново.
    """
    if supply.min_quantity is None:
        return False
    return bool(
        new_quantity <= _dec(supply.min_quantity) and supply.quantity > _dec(supply.min_quantity)
    )


async def _notify_low_stock(session, supply: Supply) -> None:
    """Telegram-сообщение владельцу, best-effort: ботом могли не пользоваться,
    токен мог протухнуть — списание важнее уведомления."""
    try:
        owner_tg_id = await resolve_owner_telegram_id(session)
    except Exception:
        logger.exception("Failed to resolve owner for low-stock notification")
        return
    if not owner_tg_id:
        return
    text = (
        f"⚠️ Расходник заканчивается: «{supply.name}» — "
        f"осталось {_fmt_qty(supply.quantity)} {supply.unit}"
        + (f" (минимум {_fmt_qty(supply.min_quantity)})" if supply.min_quantity is not None else "")
    )
    try:
        await send_text_to_telegram(owner_tg_id, text)
    except Exception:
        logger.warning("Не удалось отправить уведомление о складе «%s»", supply.name, exc_info=True)


# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------


async def _get_supply(
    session, business, supply_id: int, *, include_inactive: bool = False
) -> Supply:
    result = await session.execute(
        select(Supply).where(Supply.id == supply_id, Supply.business_id == business.id)
    )
    supply = result.scalar_one_or_none()
    if supply is None or (supply.is_active is False and not include_inactive):
        raise HTTPException(status_code=404, detail="Позиция не найдена")
    return supply


async def _reread_supply(session, business, supply_id: int) -> Supply:
    """Свежая строка после commit (SQLite не возвращает server-onupdate)."""
    return await _get_supply(session, business, supply_id, include_inactive=True)


async def _resolve_action_master(
    session, business, self_master: Master | None, master_id: int | None
) -> int | None:
    """Чьё списание: мастер всегда свой, владелец может указать мастера."""
    if self_master is not None:
        return self_master.id
    if master_id is None:
        return None
    master = (
        await session.execute(
            select(Master).where(
                Master.id == master_id,
                Master.business_id == business.id,
                Master.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if not master:
        raise HTTPException(status_code=404, detail="Мастер не найден в этом бизнесе")
    return master.id


async def _check_links(session, business, body: SupplyActionRequest) -> None:
    """work_id/session_id должны существовать и принадлежать бизнесу."""
    if body.work_id is not None:
        work = (
            await session.execute(
                select(TattooWork).where(
                    TattooWork.id == body.work_id, TattooWork.business_id == business.id
                )
            )
        ).scalar_one_or_none()
        if work is None:
            raise HTTPException(status_code=404, detail="Работа не найдена")
    if body.session_id is not None:
        sess = (
            await session.execute(
                select(TattooSession)
                .join(TattooWork, TattooSession.work_id == TattooWork.id)
                .where(TattooSession.id == body.session_id, TattooWork.business_id == business.id)
            )
        ).scalar_one_or_none()
        if sess is None:
            raise HTTPException(status_code=404, detail="Сеанс не найден")


# ---------------------------------------------------------------------------
# Чтение — обе роли
# ---------------------------------------------------------------------------


@router.get("", response_model=SupplyListResponse)
async def list_supplies(
    session: SessionDep,
    actor: MasterBusinessAndSelfDep,
    category: str | None = Query(None),
    low_only: bool = Query(False),
    include_inactive: bool = Query(False),
):
    """Остатки склада. Мастер видит активные позиции, владелец может
    запросить и архивные (include_inactive=true)."""
    business, _ = actor
    stmt = select(Supply).where(Supply.business_id == business.id)
    if not include_inactive:
        stmt = stmt.where(Supply.is_active.is_(True))
    if category is not None:
        stmt = stmt.where(Supply.category == category)
    if low_only:
        stmt = stmt.where(
            Supply.min_quantity.is_not(None), Supply.quantity <= Supply.min_quantity
        )
    stmt = stmt.order_by(Supply.category, Supply.name)
    rows = (await session.execute(stmt)).scalars().all()
    return SupplyListResponse(supplies=[SupplyOut.model_validate(s) for s in rows])


# ---------------------------------------------------------------------------
# Запись — только владелец
# ---------------------------------------------------------------------------


@router.post("", response_model=SupplyOut, status_code=201)
async def create_supply(
    body: SupplyCreate,
    session: SessionDep,
    business: OwnerBusinessDep,
):
    """Новая позиция; quantity — начальный остаток (движение kind=init)."""
    supply = Supply(
        business_id=business.id,
        name=body.name.strip(),
        category=body.category.value,
        unit=body.unit.strip(),
        quantity=_dec(body.quantity),
        min_quantity=_dec(body.min_quantity) if body.min_quantity is not None else None,
        note=body.note,
    )
    session.add(supply)
    await session.flush()
    if body.quantity != 0:
        session.add(
            SupplyMovement(
                business_id=business.id,
                supply_id=supply.id,
                delta=_dec(body.quantity),
                kind=SupplyMovementKind.init.value,
                note="Начальный остаток",
            )
        )
    await session.commit()
    return SupplyOut.model_validate(await _reread_supply(session, business, supply.id))


@router.patch("/{supply_id}", response_model=SupplyOut)
async def update_supply(
    supply_id: int,
    body: SupplyUpdate,
    session: SessionDep,
    business: OwnerBusinessDep,
):
    """Правка карточки. quantity правится только движениями (restock/use/adjust),
    чтобы журнал не расходился с остатком."""
    supply = await _get_supply(session, business, supply_id, include_inactive=True)
    data = body.model_dump(exclude_unset=True)
    if "category" in data and data["category"] is not None:
        data["category"] = data["category"].value
    for field, value in data.items():
        setattr(supply, field, value)
    await session.commit()
    return SupplyOut.model_validate(await _reread_supply(session, business, supply_id))


@router.delete("/{supply_id}", status_code=204)
async def delete_supply(supply_id: int, session: SessionDep, business: OwnerBusinessDep):
    """Мягкое удаление (is_active=false): история движений не теряет позицию."""
    supply = await _get_supply(session, business, supply_id)
    supply.is_active = False
    await session.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Движения
# ---------------------------------------------------------------------------


@router.post("/{supply_id}/use", response_model=SupplyOut)
async def use_supply(
    supply_id: int,
    body: SupplyActionRequest,
    session: SessionDep,
    actor: MasterBusinessAndSelfDep,
):
    """Списание — единственное действие мастера. В минус нельзя."""
    business, self_master = actor
    supply = await _get_supply(session, business, supply_id, include_inactive=True)
    if not supply.is_active:
        raise HTTPException(status_code=409, detail="Позиция в архиве")
    await _check_links(session, business, body)

    amount = _dec(body.amount)
    new_quantity = _dec(supply.quantity) - amount
    if new_quantity < 0:
        raise HTTPException(
            status_code=400,
            detail=f"На складе только {_fmt_qty(supply.quantity)} {supply.unit}",
        )
    crossed = _crossed_low(supply, new_quantity)

    supply.quantity = new_quantity
    session.add(
        SupplyMovement(
            business_id=business.id,
            supply_id=supply.id,
            delta=-amount,
            kind=SupplyMovementKind.use.value,
            master_id=await _resolve_action_master(session, business, self_master, body.master_id),
            work_id=body.work_id,
            session_id=body.session_id,
            note=body.note,
        )
    )
    await session.commit()

    supply = await _reread_supply(session, business, supply_id)
    if crossed:
        await _notify_low_stock(session, supply)
    return SupplyOut.model_validate(supply)


@router.post("/{supply_id}/restock", response_model=SupplyOut)
async def restock_supply(
    supply_id: int,
    body: SupplyActionRequest,
    session: SessionDep,
    business: OwnerBusinessDep,
):
    """Пополнение (закупка). Порог не проверяем: закупка снижает дефицит."""
    supply = await _get_supply(session, business, supply_id, include_inactive=True)
    if not supply.is_active:
        raise HTTPException(status_code=409, detail="Позиция в архиве")
    await _check_links(session, business, body)

    supply.quantity = _dec(supply.quantity) + _dec(body.amount)
    session.add(
        SupplyMovement(
            business_id=business.id,
            supply_id=supply.id,
            delta=_dec(body.amount),
            kind=SupplyMovementKind.purchase.value,
            note=body.note,
        )
    )
    await session.commit()
    return SupplyOut.model_validate(await _reread_supply(session, business, supply_id))


@router.post("/{supply_id}/adjust", response_model=SupplyOut)
async def adjust_supply(
    supply_id: int,
    body: SupplyAdjustRequest,
    session: SessionDep,
    business: OwnerBusinessDep,
):
    """Ревизия: фактический остаток не сходится — выставить вручную."""
    supply = await _get_supply(session, business, supply_id, include_inactive=True)
    if not supply.is_active:
        raise HTTPException(status_code=409, detail="Позиция в архиве")
    new_quantity = _dec(body.new_quantity)
    delta = new_quantity - _dec(supply.quantity)
    if delta == 0:
        raise HTTPException(
            status_code=422, detail=f"Остаток уже {_fmt_qty(supply.quantity)} {supply.unit}"
        )
    crossed = _crossed_low(supply, new_quantity)

    supply.quantity = new_quantity
    session.add(
        SupplyMovement(
            business_id=business.id,
            supply_id=supply.id,
            delta=delta,
            kind=SupplyMovementKind.adjust.value,
            note=body.note,
        )
    )
    await session.commit()

    supply = await _reread_supply(session, business, supply_id)
    if crossed:
        await _notify_low_stock(session, supply)
    return SupplyOut.model_validate(supply)


@router.get("/movements", response_model=SupplyMovementListResponse)
async def list_movements(
    session: SessionDep,
    business: OwnerBusinessDep,
    supply_id: int | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    """Журнал движений (владелец): кто, когда, сколько и зачем."""
    stmt = (
        select(SupplyMovement, Supply.name, Master.full_name)
        .join(Supply, SupplyMovement.supply_id == Supply.id)
        .outerjoin(Master, SupplyMovement.master_id == Master.id)
        .where(SupplyMovement.business_id == business.id)
        .order_by(SupplyMovement.id.desc())
        .limit(limit)
    )
    if supply_id is not None:
        stmt = stmt.where(SupplyMovement.supply_id == supply_id)
    rows = (await session.execute(stmt)).all()
    movements = [
        SupplyMovementOut(
            id=m.id,
            supply_id=m.supply_id,
            supply_name=supply_name,
            delta=float(m.delta),
            kind=m.kind,
            master_id=m.master_id,
            master_name=master_name,
            work_id=m.work_id,
            session_id=m.session_id,
            note=m.note,
            created_at=m.created_at,
        )
        for m, supply_name, master_name in rows
    ]
    return SupplyMovementListResponse(movements=movements)
