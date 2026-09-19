import secrets

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from tg_studio.api.admin_deps import OwnerBusinessDep
from tg_studio.api.deps import SessionDep
from tg_studio.config import settings
from tg_studio.db.models import Business, Master
from tg_studio.modules.business.schemas import (
    AdminBusinessResponse,
    BusinessResponse,
    MasterCreate,
    MasterResponse,
    MasterUpdate,
)

router = APIRouter()

# ── Public ────────────────────────────────────────────────────────────────────

_public = APIRouter(prefix="/business", tags=["business"])


@_public.get("/{business_id}", response_model=BusinessResponse)
async def get_business(business_id: int, session: SessionDep):
    result = await session.execute(select(Business).where(Business.id == business_id))
    business = result.scalar_one_or_none()
    if business is None:
        raise HTTPException(status_code=404, detail="Business not found")
    return BusinessResponse(
        id=business.id,
        name=business.name,
        description=business.description,
        phone=business.phone,
        is_active=business.is_active,
    )


# ── Admin: business settings ──────────────────────────────────────────────────

_admin_business = APIRouter(prefix="/admin/business", tags=["admin • business"])


@_admin_business.get("", response_model=AdminBusinessResponse)
async def get_my_business(business: OwnerBusinessDep):
    """Профиль бизнеса владельца — авторизация решает, чей бизнес, без id в URL."""
    return AdminBusinessResponse(
        id=business.id,
        name=business.name,
        description=business.description,
        phone=business.phone,
        is_active=business.is_active,
        owner_telegram_id=business.owner_telegram_id,
    )



# ── Admin: masters ────────────────────────────────────────────────────────────

_admin_masters = APIRouter(prefix="/admin/masters", tags=["admin • masters"])


def _master_to_response(m: Master) -> MasterResponse:
    return MasterResponse(
        id=m.id,
        full_name=m.full_name,
        description=m.description,
        telegram_id=m.telegram_id,
        is_active=m.is_active,
        google_calendar_id=m.google_calendar_id,
    )


async def _get_own_master(session, master_id: int, business_id: int) -> Master:
    master = await session.get(Master, master_id)
    if master is None or master.business_id != business_id:
        raise HTTPException(status_code=404, detail="Мастер не найден")
    return master


@_admin_masters.get("", response_model=list[MasterResponse])
async def list_masters(session: SessionDep, business: OwnerBusinessDep):
    result = await session.execute(
        select(Master).where(Master.business_id == business.id).order_by(Master.id)
    )
    return [_master_to_response(m) for m in result.scalars().all()]


@_admin_masters.post("", response_model=MasterResponse, status_code=201)
async def create_master(body: MasterCreate, session: SessionDep, business: OwnerBusinessDep):
    master = Master(
        business_id=business.id,
        full_name=body.full_name,
        description=body.description,
        telegram_id=body.telegram_id,
    )
    session.add(master)
    await session.commit()
    await session.refresh(master)
    return _master_to_response(master)


@_admin_masters.patch("/{master_id}", response_model=MasterResponse)
async def update_master(master_id: int, body: MasterUpdate, session: SessionDep, business: OwnerBusinessDep):
    master = await _get_own_master(session, master_id, business.id)
    if body.full_name is not None:
        master.full_name = body.full_name
    if body.description is not None:
        master.description = body.description
    if body.telegram_id is not None:
        master.telegram_id = body.telegram_id
    if body.is_active is not None:
        master.is_active = body.is_active
    await session.commit()
    await session.refresh(master)
    return _master_to_response(master)


@_admin_masters.delete("/{master_id}", status_code=204)
async def deactivate_master(master_id: int, session: SessionDep, business: OwnerBusinessDep):
    master = await _get_own_master(session, master_id, business.id)
    master.is_active = False
    await session.commit()


@_admin_masters.post("/{master_id}/registration-link", status_code=200)
async def create_registration_link(master_id: int, session: SessionDep, business: OwnerBusinessDep):
    master = await _get_own_master(session, master_id, business.id)
    token = secrets.token_urlsafe(24)
    master.registration_token = token
    await session.commit()
    payload = f"master_{token}"
    full_link = f"https://t.me/{settings.bot_username}?start={payload}" if settings.bot_username else None
    return {"payload": payload, "link": full_link}


# ── Assemble ──────────────────────────────────────────────────────────────────

router.include_router(_public)
router.include_router(_admin_business)
router.include_router(_admin_masters)
