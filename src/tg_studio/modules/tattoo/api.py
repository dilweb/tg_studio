"""
API для мастеров тату-салона (Owner App).

Реализует:
- Авторизацию мастера по ID + пароль (без User/email)
- CRUD тату-проектов с синхронизацией в Google Calendar
"""

import logging
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from tg_studio.api.auth import (
    hash_password,
    verify_password,
)
from tg_studio.api.deps import SessionDep
from tg_studio.config import settings
from tg_studio.db.models import Business, Master, TattooProject, TattooProjectStatus
from tg_studio.modules.google_calendar.client import (
    cancel_event,
    create_event,
    update_event,
)
from tg_studio.modules.tattoo.schemas import (
    MasterAuthResponse,
    MasterLoginRequest,
    TattooProjectCreate,
    TattooProjectListResponse,
    TattooProjectResponse,
    TattooProjectUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tattoo", tags=["tattoo"])


# ---------------------------------------------------------------------------
# Master JWT helpers (изолированные от основной auth-системы)
# ---------------------------------------------------------------------------

MASTER_TOKEN_EXPIRE_HOURS = 24


def _create_master_token(master_id: int, business_id: int) -> str:
    """Создать JWT для мастера (отдельный от User-токенов)."""
    expire = datetime.now(UTC) + timedelta(hours=MASTER_TOKEN_EXPIRE_HOURS)
    payload = {
        "sub": str(master_id),
        "business_id": str(business_id),
        "type": "master_access",
        "exp": expire,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def _decode_master_token(token: str) -> dict:
    """Декодировать и валидировать мастер-токен."""
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired") from None
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token") from None

    if payload.get("type") != "master_access":
        raise HTTPException(status_code=401, detail="Invalid token type")
    return payload


# ---------------------------------------------------------------------------
# Dependency: текущий мастер по JWT
# ---------------------------------------------------------------------------


from typing import Annotated

from fastapi import Header


async def get_current_master(
    session: SessionDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Master:
    """
    FastAPI dependency — извлекает мастера из JWT-токена.

    Токен передаётся в заголовке Authorization: Bearer <token>.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")

    token = authorization.removeprefix("Bearer ")
    payload = _decode_master_token(token)
    master_id = int(payload["sub"])

    result = await session.execute(
        select(Master)
        .where(Master.id == master_id, Master.is_active.is_(True))
        .options(selectinload(Master.business))
    )
    master = result.scalar_one_or_none()
    if not master:
        raise HTTPException(status_code=401, detail="Master not found or inactive")

    return master


# Используем Depends напрямую, чтобы FastAPI не пытался интерпретировать
# SQLAlchemy модель Master как Pydantic response model
MasterDep = Depends(get_current_master)


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------


@router.post("/auth/login", response_model=MasterAuthResponse)
async def master_login(body: MasterLoginRequest, session: SessionDep):
    """
    Логин мастера по Telegram ID (или внутреннему ID) и паролю.
    """
    # Ищем сначала по telegram_id, затем по внутреннему id
    master = None
    if body.master_id:
        result = await session.execute(
            select(Master).where(
                Master.telegram_id == body.master_id,
                Master.is_active.is_(True),
            )
        )
        master = result.scalar_one_or_none()

    if not master:
        raise HTTPException(status_code=401, detail="Invalid master ID or password")

    if not master.password_hash:
        raise HTTPException(status_code=401, detail="Invalid master ID or password")

    if not verify_password(body.password, master.password_hash):
        raise HTTPException(status_code=401, detail="Invalid master ID or password")

    token = _create_master_token(master.id, master.business_id)
    return MasterAuthResponse(
        access_token=token,
        master_id=master.id,
        master_name=master.full_name,
        business_id=master.business_id,
    )


# ---------------------------------------------------------------------------
# Google Calendar helpers
# ---------------------------------------------------------------------------


async def _sync_project_to_calendar(
    project: TattooProject,
    master: Master,
    business: Business,
) -> str | None:
    """Создать или обновить событие в Google Calendar для проекта."""
    if not business.google_calendar_credentials_json:
        return None

    summary = f"Тату: {project.size}, {project.complexity} — {master.full_name}"
    description = (
        f"Проект #{project.id}\n"
        f"Мастер: {master.full_name}\n"
        f"Студия: {business.name}\n"
        f"Размер: {project.size}\n"
        f"Сложность: {project.complexity}\n"
        f"Место: {project.placement}\n"
        f"Стоимость: {project.cost} KZT"
    )

    start_dt = project.session_date.isoformat()
    # Длительность по умолчанию — 3 часа
    end_dt = (project.session_date + timedelta(hours=3)).isoformat()

    if project.google_event_id:
        success = await update_event(
            credentials_json=business.google_calendar_credentials_json,
            event_id=project.google_event_id,
            summary=summary,
            description=description,
            start_datetime=start_dt,
            end_datetime=end_dt,
        )
        return project.google_event_id if success else None
    else:
        event_id = await create_event(
            credentials_json=business.google_calendar_credentials_json,
            summary=summary,
            description=description,
            start_datetime=start_dt,
            end_datetime=end_dt,
        )
        return event_id


async def _remove_project_from_calendar(
    project: TattooProject,
    business: Business,
) -> None:
    """Удалить событие из Google Calendar."""
    if project.google_event_id and business.google_calendar_credentials_json:
        await cancel_event(
            credentials_json=business.google_calendar_credentials_json,
            event_id=project.google_event_id,
        )


# ---------------------------------------------------------------------------
# CRUD endpoints
# ---------------------------------------------------------------------------


@router.get("/projects", response_model=TattooProjectListResponse)
async def list_projects(
    session: SessionDep,
    master=MasterDep,
):
    """Получить список всех проектов текущего мастера."""
    result = await session.execute(
        select(TattooProject)
        .where(TattooProject.master_id == master.id)
        .order_by(TattooProject.session_date.desc())
    )
    projects = result.scalars().all()
    return TattooProjectListResponse(
        projects=[TattooProjectResponse.model_validate(p) for p in projects],
        total=len(projects),
    )


@router.post("/projects", response_model=TattooProjectResponse, status_code=201)
async def create_project(
    body: TattooProjectCreate,
    session: SessionDep,
    master=MasterDep,
):
    """Создать новый проект татуировки и отправить событие в Google Calendar."""
    project = TattooProject(
        master_id=master.id,
        business_id=master.business_id,
        size=body.size,
        complexity=body.complexity,
        placement=body.placement,
        session_date=body.session_date,
        cost=body.cost,
    )
    session.add(project)
    await session.flush()

    # Синхронизация с Google Calendar
    business = master.business
    try:
        event_id = await _sync_project_to_calendar(project, master, business)
        if event_id:
            project.google_event_id = event_id
    except Exception:
        logger.exception("Failed to sync project %d to Google Calendar", project.id)

    await session.commit()
    await session.refresh(project)
    return TattooProjectResponse.model_validate(project)


@router.get("/projects/{project_id}", response_model=TattooProjectResponse)
async def get_project(
    project_id: int,
    session: SessionDep,
    master=MasterDep,
):
    """Получить детали конкретного проекта."""
    result = await session.execute(
        select(TattooProject).where(
            TattooProject.id == project_id,
            TattooProject.master_id == master.id,
        )
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return TattooProjectResponse.model_validate(project)


@router.patch("/projects/{project_id}", response_model=TattooProjectResponse)
async def update_project(
    project_id: int,
    body: TattooProjectUpdate,
    session: SessionDep,
    master=MasterDep,
):
    """Обновить существующий проект и синхронизировать с Google Calendar."""
    result = await session.execute(
        select(TattooProject).where(
            TattooProject.id == project_id,
            TattooProject.master_id == master.id,
        )
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Обновляем только переданные поля
    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(project, field, value)

    await session.flush()

    # Синхронизация с Google Calendar
    business = master.business
    try:
        event_id = await _sync_project_to_calendar(project, master, business)
        if event_id:
            project.google_event_id = event_id
    except Exception:
        logger.exception("Failed to sync project %d to Google Calendar", project.id)

    await session.commit()
    await session.refresh(project)
    return TattooProjectResponse.model_validate(project)


@router.delete("/projects/{project_id}", status_code=204)
async def delete_project(
    project_id: int,
    session: SessionDep,
    master=MasterDep,
):
    """Удалить проект и убрать событие из Google Calendar."""
    result = await session.execute(
        select(TattooProject).where(
            TattooProject.id == project_id,
            TattooProject.master_id == master.id,
        )
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Удаляем событие из календаря
    business = master.business
    try:
        await _remove_project_from_calendar(project, business)
    except Exception:
        logger.exception("Failed to remove project %d from Google Calendar", project.id)

    await session.delete(project)
    await session.commit()