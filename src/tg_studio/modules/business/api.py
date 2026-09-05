import secrets

from fastapi import APIRouter, HTTPException
from sqlalchemy import delete, select

from tg_studio.api.admin_deps import OwnerBusinessDep
from tg_studio.api.deps import SessionDep
from tg_studio.config import settings
from tg_studio.db.models import Business, Master, MasterService, Service
from tg_studio.modules.business.schemas import (
    BusinessResponse,
    MasterCreate,
    MasterResponse,
    MasterServicesUpdate,
    MasterUpdate,
    ServiceCreate,
    ServiceResponse,
    ServiceUpdate,
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




# ── Admin: masters ────────────────────────────────────────────────────────────

_admin_masters = APIRouter(prefix="/admin/masters", tags=["admin • masters"])


async def _load_service_ids(session, master_id: int) -> list[int]:
    result = await session.execute(
        select(MasterService.service_id).where(MasterService.master_id == master_id)
    )
    return list(result.scalars().all())


def _master_to_response(m: Master, service_ids: list[int]) -> MasterResponse:
    return MasterResponse(
        id=m.id,
        full_name=m.full_name,
        description=m.description,
        telegram_id=m.telegram_id,
        is_active=m.is_active,
        service_ids=service_ids,
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
    masters = result.scalars().all()
    all_links = await session.execute(
        select(MasterService).where(MasterService.master_id.in_([m.id for m in masters]))
    )
    links_by_master: dict[int, list[int]] = {}
    for link in all_links.scalars().all():
        links_by_master.setdefault(link.master_id, []).append(link.service_id)
    return [_master_to_response(m, links_by_master.get(m.id, [])) for m in masters]


@_admin_masters.post("", response_model=MasterResponse, status_code=201)
async def create_master(body: MasterCreate, session: SessionDep, business: OwnerBusinessDep):
    if body.service_ids:
        valid = await session.execute(
            select(Service.id).where(Service.id.in_(body.service_ids), Service.business_id == business.id)
        )
        invalid = set(body.service_ids) - set(valid.scalars().all())
        if invalid:
            raise HTTPException(status_code=404, detail=f"Услуги не найдены: {sorted(invalid)}")
    master = Master(
        business_id=business.id,
        full_name=body.full_name,
        description=body.description,
        telegram_id=body.telegram_id,
    )
    session.add(master)
    await session.flush()
    for service_id in body.service_ids:
        session.add(MasterService(master_id=master.id, service_id=service_id))
    await session.commit()
    await session.refresh(master)
    return _master_to_response(master, body.service_ids)


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
    return _master_to_response(master, await _load_service_ids(session, master.id))


@_admin_masters.delete("/{master_id}", status_code=204)
async def deactivate_master(master_id: int, session: SessionDep, business: OwnerBusinessDep):
    master = await _get_own_master(session, master_id, business.id)
    master.is_active = False
    await session.commit()


@_admin_masters.put("/{master_id}/services", response_model=MasterResponse)
async def set_master_services(master_id: int, body: MasterServicesUpdate, session: SessionDep, business: OwnerBusinessDep):
    master = await _get_own_master(session, master_id, business.id)
    if body.service_ids:
        valid = await session.execute(
            select(Service.id).where(Service.id.in_(body.service_ids), Service.business_id == business.id)
        )
        invalid = set(body.service_ids) - set(valid.scalars().all())
        if invalid:
            raise HTTPException(status_code=404, detail=f"Услуги не найдены: {sorted(invalid)}")
    await session.execute(delete(MasterService).where(MasterService.master_id == master_id))
    for service_id in body.service_ids:
        session.add(MasterService(master_id=master_id, service_id=service_id))
    await session.commit()
    return _master_to_response(master, list(body.service_ids))


@_admin_masters.post("/{master_id}/services/{service_id}", status_code=201)
async def attach_service(master_id: int, service_id: int, session: SessionDep, business: OwnerBusinessDep):
    await _get_own_master(session, master_id, business.id)
    svc = await session.get(Service, service_id)
    if svc is None or svc.business_id != business.id:
        raise HTTPException(status_code=404, detail="Услуга не найдена")
    existing = await session.execute(
        select(MasterService).where(MasterService.master_id == master_id, MasterService.service_id == service_id)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Услуга уже привязана к этому мастеру")
    session.add(MasterService(master_id=master_id, service_id=service_id))
    await session.commit()
    master = await _get_own_master(session, master_id, business.id)
    return _master_to_response(master, await _load_service_ids(session, master_id))


@_admin_masters.delete("/{master_id}/services/{service_id}", status_code=204)
async def detach_service(master_id: int, service_id: int, session: SessionDep, business: OwnerBusinessDep):
    await _get_own_master(session, master_id, business.id)
    result = await session.execute(
        select(MasterService).where(MasterService.master_id == master_id, MasterService.service_id == service_id)
    )
    link = result.scalar_one_or_none()
    if link is None:
        raise HTTPException(status_code=404, detail="Связь не найдена")
    await session.delete(link)
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


# ── Admin: services ───────────────────────────────────────────────────────────

_admin_services = APIRouter(prefix="/admin/services", tags=["admin • services"])


def _service_to_response(s: Service) -> ServiceResponse:
    return ServiceResponse(
        id=s.id,
        name=s.name,
        description=s.description,
        price=float(s.price),
        is_active=s.is_active,
    )


async def _get_own_service(session, service_id: int, business_id: int) -> Service:
    service = await session.get(Service, service_id)
    if service is None or service.business_id != business_id:
        raise HTTPException(status_code=404, detail="Услуга не найдена")
    return service


@_admin_services.get("", response_model=list[ServiceResponse])
async def list_services(session: SessionDep, business: OwnerBusinessDep):
    result = await session.execute(
        select(Service).where(Service.business_id == business.id).order_by(Service.id)
    )
    return [_service_to_response(s) for s in result.scalars().all()]


@_admin_services.post("", response_model=ServiceResponse, status_code=201)
async def create_service(body: ServiceCreate, session: SessionDep, business: OwnerBusinessDep):
    service = Service(
        business_id=business.id,
        name=body.name,
        description=body.description,
        price=body.price,
    )
    session.add(service)
    await session.commit()
    await session.refresh(service)
    return _service_to_response(service)


@_admin_services.patch("/{service_id}", response_model=ServiceResponse)
async def update_service(service_id: int, body: ServiceUpdate, session: SessionDep, business: OwnerBusinessDep):
    service = await _get_own_service(session, service_id, business.id)
    if body.name is not None:
        service.name = body.name
    if body.description is not None:
        service.description = body.description
    if body.price is not None:
        service.price = body.price
    if body.is_active is not None:
        service.is_active = body.is_active
    await session.commit()
    await session.refresh(service)
    return _service_to_response(service)


@_admin_services.delete("/{service_id}", status_code=204)
async def deactivate_service(service_id: int, session: SessionDep, business: OwnerBusinessDep):
    service = await _get_own_service(session, service_id, business.id)
    service.is_active = False
    await session.commit()


# ── Assemble ──────────────────────────────────────────────────────────────────

router.include_router(_public)
router.include_router(_admin_masters)
router.include_router(_admin_services)
