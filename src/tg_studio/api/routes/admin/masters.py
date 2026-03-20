import secrets

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select

from tg_studio.api.deps import SessionDep
from tg_studio.api.routes.admin.deps import OwnerBusinessDep
from tg_studio.config import settings
from tg_studio.db.models import Master, MasterService, Service

router = APIRouter(prefix="/masters", tags=["admin • masters"])


class MasterCreate(BaseModel):
    full_name: str
    description: str | None = None
    telegram_id: int | None = None
    service_ids: list[int] = []


class MasterUpdate(BaseModel):
    full_name: str | None = None
    description: str | None = None
    telegram_id: int | None = None
    is_active: bool | None = None


class MasterServicesUpdate(BaseModel):
    service_ids: list[int]


class MasterResponse(BaseModel):
    id: int
    full_name: str
    description: str | None
    telegram_id: int | None
    is_active: bool
    service_ids: list[int]


async def _load_service_ids(session, master_id: int) -> list[int]:
    result = await session.execute(
        select(MasterService.service_id).where(MasterService.master_id == master_id)
    )
    return list(result.scalars().all())


def _to_response(m: Master, service_ids: list[int]) -> MasterResponse:
    return MasterResponse(
        id=m.id,
        full_name=m.full_name,
        description=m.description,
        telegram_id=m.telegram_id,
        is_active=m.is_active,
        service_ids=service_ids,
    )


@router.get("", response_model=list[MasterResponse])
async def list_masters(session: SessionDep, business: OwnerBusinessDep):
    result = await session.execute(
        select(Master).where(Master.business_id == business.id).order_by(Master.id)
    )
    masters = result.scalars().all()

    all_links = await session.execute(
        select(MasterService).where(
            MasterService.master_id.in_([m.id for m in masters])
        )
    )
    links_by_master: dict[int, list[int]] = {}
    for link in all_links.scalars().all():
        links_by_master.setdefault(link.master_id, []).append(link.service_id)

    return [_to_response(m, links_by_master.get(m.id, [])) for m in masters]


@router.post("", response_model=MasterResponse, status_code=201)
async def create_master(
    body: MasterCreate,
    session: SessionDep,
    business: OwnerBusinessDep,
):
    if body.service_ids:
        valid = await session.execute(
            select(Service.id).where(
                Service.id.in_(body.service_ids),
                Service.business_id == business.id,
            )
        )
        valid_ids = set(valid.scalars().all())
        invalid = set(body.service_ids) - valid_ids
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
    return _to_response(master, body.service_ids)


@router.patch("/{master_id}", response_model=MasterResponse)
async def update_master(
    master_id: int,
    body: MasterUpdate,
    session: SessionDep,
    business: OwnerBusinessDep,
):
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
    service_ids = await _load_service_ids(session, master.id)
    return _to_response(master, service_ids)


@router.delete("/{master_id}", status_code=204)
async def deactivate_master(
    master_id: int,
    session: SessionDep,
    business: OwnerBusinessDep,
):
    master = await _get_own_master(session, master_id, business.id)
    master.is_active = False
    await session.commit()


# ─── Управление услугами мастера ──────────────────────────────────────────────

@router.put("/{master_id}/services", response_model=MasterResponse)
async def set_master_services(
    master_id: int,
    body: MasterServicesUpdate,
    session: SessionDep,
    business: OwnerBusinessDep,
):
    """Полная замена списка услуг мастера. Передай пустой список — снимет все услуги."""
    master = await _get_own_master(session, master_id, business.id)

    if body.service_ids:
        valid = await session.execute(
            select(Service.id).where(
                Service.id.in_(body.service_ids),
                Service.business_id == business.id,
            )
        )
        valid_ids = set(valid.scalars().all())
        invalid = set(body.service_ids) - valid_ids
        if invalid:
            raise HTTPException(status_code=404, detail=f"Услуги не найдены: {sorted(invalid)}")

    await session.execute(
        delete(MasterService).where(MasterService.master_id == master_id)
    )
    for service_id in body.service_ids:
        session.add(MasterService(master_id=master_id, service_id=service_id))

    await session.commit()
    return _to_response(master, list(body.service_ids))


@router.post("/{master_id}/services/{service_id}", status_code=201)
async def attach_service(
    master_id: int,
    service_id: int,
    session: SessionDep,
    business: OwnerBusinessDep,
):
    """Привязать одну услугу к мастеру."""
    await _get_own_master(session, master_id, business.id)

    svc = await session.get(Service, service_id)
    if svc is None or svc.business_id != business.id:
        raise HTTPException(status_code=404, detail="Услуга не найдена")

    existing = await session.execute(
        select(MasterService).where(
            MasterService.master_id == master_id,
            MasterService.service_id == service_id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Услуга уже привязана к этому мастеру")

    session.add(MasterService(master_id=master_id, service_id=service_id))
    await session.commit()
    service_ids = await _load_service_ids(session, master_id)
    master = await _get_own_master(session, master_id, business.id)
    return _to_response(master, service_ids)


@router.post("/{master_id}/registration-link", status_code=200)
async def create_registration_link(
    master_id: int,
    session: SessionDep,
    business: OwnerBusinessDep,
):
    """
    Сгенерировать одноразовую ссылку для регистрации мастера в боте.
    Мастер переходит по ссылке → в боте нажимает Start → привязывается telegram_id.
    """
    master = await _get_own_master(session, master_id, business.id)
    token = secrets.token_urlsafe(24)
    master.registration_token = token
    await session.commit()

    payload = f"master_{token}"
    full_link = (
        f"https://t.me/{settings.bot_username}?start={payload}"
        if settings.bot_username
        else None
    )
    return {
        "payload": payload,
        "link": full_link,
        "hint": "Отправьте ссылку мастеру. Он откроет её в Telegram и нажмёт «Запустить» — после этого аккаунт привяжется.",
    }


@router.delete("/{master_id}/services/{service_id}", status_code=204)
async def detach_service(
    master_id: int,
    service_id: int,
    session: SessionDep,
    business: OwnerBusinessDep,
):
    """Отвязать одну услугу от мастера."""
    await _get_own_master(session, master_id, business.id)
    result = await session.execute(
        select(MasterService).where(
            MasterService.master_id == master_id,
            MasterService.service_id == service_id,
        )
    )
    link = result.scalar_one_or_none()
    if link is None:
        raise HTTPException(status_code=404, detail="Связь не найдена")
    await session.delete(link)
    await session.commit()


async def _get_own_master(session, master_id: int, business_id: int) -> Master:
    master = await session.get(Master, master_id)
    if master is None or master.business_id != business_id:
        raise HTTPException(status_code=404, detail="Мастер не найден")
    return master
