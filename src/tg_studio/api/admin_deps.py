"""
Admin dependencies — role-based access control via JWT.

Owner: full access to their business.
Master: access scoped to their master profile within the business.
"""

from typing import Annotated

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import load_only

from tg_studio.api.auth import CurrentUserDep
from tg_studio.api.deps import SessionDep
from tg_studio.db.models import Business, Master, User, UserRole


def require_role(*allowed_roles: UserRole):
    """Factory for role-checking dependencies."""
    async def _check(user: CurrentUserDep) -> User:
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=403,
                detail=f"Role '{user.role.value}' is not allowed. Required: {[r.value for r in allowed_roles]}",
            )
        return user
    return _check


RequireOwnerDep = Annotated[User, Depends(require_role(UserRole.owner))]
RequireOwnerOrMasterDep = Annotated[User, Depends(require_role(UserRole.owner, UserRole.master))]


async def get_owner_business(
    user: RequireOwnerDep,
    session: SessionDep,
) -> Business:
    result = await session.execute(
        select(Business).where(
            Business.owner_id == user.id,
            Business.is_active.is_(True),
        )
    )
    business = result.scalar_one_or_none()
    if business is None:
        raise HTTPException(
            status_code=403,
            detail="Бизнес не найден. Зарегистрируйте бизнес через POST /api/auth/register/owner",
        )
    return business


async def get_owner_business_for_ai(
    user: RequireOwnerDep,
    session: SessionDep,
) -> Business:
    result = await session.execute(
        select(Business)
        .options(
            load_only(
                Business.id,
                Business.name,
                Business.description,
            )
        )
        .where(
            Business.owner_id == user.id,
            Business.is_active.is_(True),
        )
    )
    business = result.scalar_one_or_none()
    if business is None:
        raise HTTPException(
            status_code=403,
            detail="Бизнес не найден. Зарегистрируйте бизнес через POST /api/auth/register/owner",
        )
    return business


async def get_master_business(
    user: RequireOwnerOrMasterDep,
    session: SessionDep,
) -> Business:
    """Resolve the business for both owners and masters."""
    if user.role == UserRole.owner:
        return await get_owner_business(user, session)

    result = await session.execute(
        select(Master).where(Master.user_id == user.id, Master.is_active.is_(True))
    )
    master = result.scalar_one_or_none()
    if not master:
        raise HTTPException(status_code=403, detail="Master profile not found or inactive")

    result = await session.execute(
        select(Business).where(Business.id == master.business_id, Business.is_active.is_(True))
    )
    business = result.scalar_one_or_none()
    if not business:
        raise HTTPException(status_code=403, detail="Business not found or inactive")
    return business


OwnerBusinessDep = Annotated[Business, Depends(get_owner_business)]
OwnerBusinessAIDep = Annotated[Business, Depends(get_owner_business_for_ai)]
MasterBusinessDep = Annotated[Business, Depends(get_master_business)]
