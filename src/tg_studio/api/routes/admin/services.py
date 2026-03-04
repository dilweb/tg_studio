from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from tg_studio.api.deps import SessionDep
from tg_studio.api.routes.admin.deps import OwnerBusinessDep
from tg_studio.db.models import Service, ServiceType

router = APIRouter(prefix="/services", tags=["admin • services"])


class ServiceCreate(BaseModel):
    name: str
    description: str | None = None
    service_type: Literal["appointment", "project"] = "appointment"
    price: float = Field(gt=0)
    prepayment_percent: int = Field(default=50, ge=0, le=100)
    cancel_deadline_hours: int = Field(default=3, ge=0)


class ServiceUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    price: float | None = Field(default=None, gt=0)
    prepayment_percent: int | None = Field(default=None, ge=0, le=100)
    cancel_deadline_hours: int | None = Field(default=None, ge=0)
    is_active: bool | None = None


class ServiceResponse(BaseModel):
    id: int
    name: str
    description: str | None
    service_type: str
    price: float
    prepayment_percent: int
    cancel_deadline_hours: int
    is_active: bool


def _to_response(s: Service) -> ServiceResponse:
    return ServiceResponse(
        id=s.id,
        name=s.name,
        description=s.description,
        service_type=s.service_type.value,
        price=float(s.price),
        prepayment_percent=s.prepayment_percent,
        cancel_deadline_hours=s.cancel_deadline_hours,
        is_active=s.is_active,
    )


@router.get("", response_model=list[ServiceResponse])
async def list_services(session: SessionDep, business: OwnerBusinessDep):
    result = await session.execute(
        select(Service).where(Service.business_id == business.id).order_by(Service.id)
    )
    return [_to_response(s) for s in result.scalars().all()]


@router.post("", response_model=ServiceResponse, status_code=201)
async def create_service(
    body: ServiceCreate,
    session: SessionDep,
    business: OwnerBusinessDep,
):
    service = Service(
        business_id=business.id,
        name=body.name,
        description=body.description,
        service_type=ServiceType(body.service_type),
        price=body.price,
        prepayment_percent=body.prepayment_percent,
        cancel_deadline_hours=body.cancel_deadline_hours,
    )
    session.add(service)
    await session.commit()
    await session.refresh(service)
    return _to_response(service)


@router.patch("/{service_id}", response_model=ServiceResponse)
async def update_service(
    service_id: int,
    body: ServiceUpdate,
    session: SessionDep,
    business: OwnerBusinessDep,
):
    service = await _get_own_service(session, service_id, business.id)
    if body.name is not None:
        service.name = body.name
    if body.description is not None:
        service.description = body.description
    if body.price is not None:
        service.price = body.price
    if body.prepayment_percent is not None:
        service.prepayment_percent = body.prepayment_percent
    if body.cancel_deadline_hours is not None:
        service.cancel_deadline_hours = body.cancel_deadline_hours
    if body.is_active is not None:
        service.is_active = body.is_active
    await session.commit()
    await session.refresh(service)
    return _to_response(service)


@router.delete("/{service_id}", status_code=204)
async def deactivate_service(
    service_id: int,
    session: SessionDep,
    business: OwnerBusinessDep,
):
    service = await _get_own_service(session, service_id, business.id)
    service.is_active = False
    await session.commit()


async def _get_own_service(session, service_id: int, business_id: int) -> Service:
    service = await session.get(Service, service_id)
    if service is None or service.business_id != business_id:
        raise HTTPException(status_code=404, detail="Услуга не найдена")
    return service
