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
from tg_studio.db.models import Business, Master, StudioAdmin, User, UserRole


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
# Коворкинг (05): доступ владельца и администратора студии
RequireOwnerOrAdminDep = Annotated[User, Depends(require_role(UserRole.owner, UserRole.admin))]
# Коворкинг: все роли панели (владелец, мастер, администратор)
RequireStaffDep = Annotated[
    User, Depends(require_role(UserRole.owner, UserRole.master, UserRole.admin))
]


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
    user: RequireStaffDep,
    session: SessionDep,
) -> Business:
    """Resolve the business for owners, masters and studio admins (05).

    Администратор видит свою студию целиком — для него нет «своего» профиля
    мастера, он действует за студию (как владелец, но с меньшим набором прав)."""
    if user.role == UserRole.owner:
        return await get_owner_business(user, session)
    if user.role == UserRole.admin:
        return await get_admin_business(user, session)

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


async def get_master_business_and_self(
    user: RequireStaffDep,
    session: SessionDep,
) -> tuple[Business, Master | None]:
    """Business + the acting Master row. None for an owner or admin —
    they can act on any master (админ действует за студию, 05)."""
    business = await get_master_business(user, session)
    if user.role != UserRole.master:
        return business, None
    result = await session.execute(
        select(Master).where(Master.user_id == user.id, Master.is_active.is_(True))
    )
    return business, result.scalar_one_or_none()


MasterBusinessAndSelfDep = Annotated[tuple[Business, Master | None], Depends(get_master_business_and_self)]
# То же для панели коворкинга с ролью admin (05): имя-алиас для читаемости
StaffBusinessAndSelfDep = MasterBusinessAndSelfDep


async def get_admin_business(
    user: RequireOwnerOrAdminDep,
    session: SessionDep,
) -> Business:
    """Business для владельца или администратора студии (коворкинг, 05).

    Админ видит свою студию целиком (как владелец, но с меньшим набором
    действий — это решают конкретные эндпоинты)."""
    if user.role == UserRole.owner:
        return await get_owner_business(user, session)
    result = await session.execute(
        select(StudioAdmin).where(
            StudioAdmin.user_id == user.id, StudioAdmin.is_active.is_(True)
        )
    )
    admin = result.scalar_one_or_none()
    if admin is None:
        raise HTTPException(status_code=403, detail="Профиль администратора не найден или неактивен")
    result = await session.execute(
        select(Business).where(Business.id == admin.business_id, Business.is_active.is_(True))
    )
    business = result.scalar_one_or_none()
    if business is None:
        raise HTTPException(status_code=403, detail="Business not found or inactive")
    return business


async def get_admin_business_and_self(
    user: RequireOwnerOrAdminDep,
    session: SessionDep,
) -> tuple[Business, StudioAdmin | None]:
    """Business + строка администратора (None для владельца — он действует за всех)."""
    business = await get_admin_business(user, session)
    if user.role == UserRole.owner:
        return business, None
    result = await session.execute(
        select(StudioAdmin).where(
            StudioAdmin.user_id == user.id, StudioAdmin.is_active.is_(True)
        )
    )
    return business, result.scalar_one_or_none()


AdminBusinessDep = Annotated[Business, Depends(get_admin_business)]
AdminBusinessAndSelfDep = Annotated[
    tuple[Business, StudioAdmin | None], Depends(get_admin_business_and_self)
]
