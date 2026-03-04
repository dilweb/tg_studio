from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from tg_studio.api.auth import TelegramUserDep
from tg_studio.api.deps import SessionDep
from tg_studio.db.models import Business

router = APIRouter(prefix="/business", tags=["business"])


class CreateBusinessRequest(BaseModel):
    name: str
    description: str | None = None
    phone: str | None = None


class BusinessResponse(BaseModel):
    id: int
    owner_telegram_id: int
    name: str 
    description: str | None
    phone: str | None
    is_active: bool


@router.post("", response_model=BusinessResponse, status_code=201)
async def create_business(
    body: CreateBusinessRequest,
    session: SessionDep,
    tg_user: TelegramUserDep,  # telegram_id берётся из проверенного initData, не из тела запроса
):
    owner_telegram_id = tg_user["id"]

    existing = await session.execute(
        select(Business).where(Business.owner_telegram_id == owner_telegram_id)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Business already registered for this Telegram account")

    business = Business(
        owner_telegram_id=owner_telegram_id,
        name=body.name,
        description=body.description,
        phone=body.phone,
    )
    session.add(business)
    await session.commit()
    await session.refresh(business)
    return BusinessResponse(
        id=business.id,
        owner_telegram_id=business.owner_telegram_id,
        name=business.name,
        description=business.description,
        phone=business.phone,
        is_active=business.is_active,
    )


@router.get("/{business_id}", response_model=BusinessResponse)
async def get_business(business_id: int, session: SessionDep):
    result = await session.execute(select(Business).where(Business.id == business_id))
    business = result.scalar_one_or_none()
    if business is None:
        raise HTTPException(status_code=404, detail="Business not found")
    return BusinessResponse(
        id=business.id,
        owner_telegram_id=business.owner_telegram_id,
        name=business.name,
        description=business.description,
        phone=business.phone,
        is_active=business.is_active,
    )


@router.get("/by-owner/{telegram_id}", response_model=BusinessResponse)
async def get_business_by_owner(telegram_id: int, session: SessionDep):
    result = await session.execute(
        select(Business).where(Business.owner_telegram_id == telegram_id)
    )
    business = result.scalar_one_or_none()
    if business is None:
        raise HTTPException(status_code=404, detail="Business not found")
    return BusinessResponse(
        id=business.id,
        owner_telegram_id=business.owner_telegram_id,
        name=business.name,
        description=business.description,
        phone=business.phone,
        is_active=business.is_active,
    )
