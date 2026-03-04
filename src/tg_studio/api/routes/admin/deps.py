"""
Admin dependency — проверяет что запрашивающий является владельцем бизнеса.
Возвращает объект Business. Используется во всех admin-роутерах.
"""

from typing import Annotated

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_studio.api.auth import TelegramUserDep
from tg_studio.api.deps import SessionDep
from tg_studio.db.models import Business


async def get_owner_business(
    tg_user: TelegramUserDep,
    session: SessionDep,
) -> Business:
    result = await session.execute(
        select(Business).where(
            Business.owner_telegram_id == tg_user["id"],
            Business.is_active.is_(True),
        )
    )
    business = result.scalar_one_or_none()
    if business is None:
        raise HTTPException(
            status_code=403,
            detail="Бизнес не найден. Сначала зарегистрируйте бизнес через POST /api/business",
        )
    return business


OwnerBusinessDep = Annotated[Business, Depends(get_owner_business)]
