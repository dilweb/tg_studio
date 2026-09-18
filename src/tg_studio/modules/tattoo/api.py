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
from tg_studio.db.models import Business, Master, TattooSession, TattooWork, TattooWorkStatus
from tg_studio.modules.google_calendar.client import (
    cancel_event,
    create_event,
    update_event,
)
from tg_studio.modules.tattoo.schemas import (
    MasterAuthResponse,
    MasterLoginRequest,
    TattooSessionCreate,
    TattooSessionResponse,
    TattooSessionUpdate,
    TattooWorkCreate,
    TattooWorkListResponse,
    TattooWorkResponse,
    TattooWorkUpdate,
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


async def _sync_session_to_calendar(
    tattoo_session: TattooSession,
    work: TattooWork,
    master: Master,
    business: Business,
) -> str | None:
    """Создать или обновить событие в Google Calendar для сеанса."""
    if not business.google_calendar_credentials_json:
        return None

    summary = f"Тату: {work.size}, {work.complexity} — {master.full_name}"
    description = (
        f"Работа #{work.id}, сеанс #{tattoo_session.id}\n"
        f"Мастер: {master.full_name}\n"
        f"Студия: {business.name}\n"
        f"Размер: {work.size}\n"
        f"Сложность: {work.complexity}\n"
        f"Стиль: {work.style}\n"
        f"Место: {work.placement}\n"
        f"Стоимость сеанса: {tattoo_session.cost} KZT"
    )

    start_dt = tattoo_session.session_date.isoformat()
    # Длительность по умолчанию — 3 часа
    end_dt = (tattoo_session.session_date + timedelta(hours=3)).isoformat()
    calendar_id = master.google_calendar_id or "primary"

    if tattoo_session.google_event_id:
        success = await update_event(
            credentials_json=business.google_calendar_credentials_json,
            event_id=tattoo_session.google_event_id,
            summary=summary,
            description=description,
            start_datetime=start_dt,
            end_datetime=end_dt,
            calendar_id=calendar_id,
        )
        return tattoo_session.google_event_id if success else None
    else:
        event_id = await create_event(
            credentials_json=business.google_calendar_credentials_json,
            summary=summary,
            description=description,
            start_datetime=start_dt,
            end_datetime=end_dt,
            calendar_id=calendar_id,
        )
        return event_id


async def _remove_session_from_calendar(
    tattoo_session: TattooSession,
    master: Master,
    business: Business,
) -> None:
    """Удалить событие из Google Calendar."""
    if tattoo_session.google_event_id and business.google_calendar_credentials_json:
        await cancel_event(
            credentials_json=business.google_calendar_credentials_json,
            event_id=tattoo_session.google_event_id,
            calendar_id=master.google_calendar_id or "primary",
        )


# ---------------------------------------------------------------------------
# TattooWork CRUD
# ---------------------------------------------------------------------------


async def _get_work_or_404(session: SessionDep, work_id: int, master: Master) -> TattooWork:
    result = await session.execute(
        select(TattooWork)
        .where(TattooWork.id == work_id, TattooWork.master_id == master.id)
        .options(selectinload(TattooWork.sessions))
    )
    work = result.scalar_one_or_none()
    if not work:
        raise HTTPException(status_code=404, detail="Work not found")
    return work


@router.get("/works", response_model=TattooWorkListResponse)
async def list_works(
    session: SessionDep,
    master=MasterDep,
):
    """Получить список всех тату-работ текущего мастера."""
    result = await session.execute(
        select(TattooWork)
        .where(TattooWork.master_id == master.id)
        .options(selectinload(TattooWork.sessions))
        .order_by(TattooWork.created_at.desc())
    )
    works = result.scalars().all()
    return TattooWorkListResponse(
        works=[TattooWorkResponse.model_validate(w) for w in works],
        total=len(works),
    )


@router.post("/works", response_model=TattooWorkResponse, status_code=201)
async def create_work(
    body: TattooWorkCreate,
    session: SessionDep,
    master=MasterDep,
):
    """Создать новую тату-работу вместе с первым сеансом и событием в Google Calendar."""
    work = TattooWork(
        client_id=body.client_id,
        master_id=master.id,
        business_id=master.business_id,
        size=body.size,
        complexity=body.complexity,
        style=body.style,
        placement=body.placement,
    )
    session.add(work)
    await session.flush()

    tattoo_session = TattooSession(
        work_id=work.id,
        session_date=body.first_session.session_date,
        quoted_cost=body.first_session.quoted_cost,
        cost=body.first_session.cost,
        sketch_file_id=body.first_session.sketch_file_id,
        result_file_id=body.first_session.result_file_id,
        is_final_session=body.first_session.is_final_session,
    )
    session.add(tattoo_session)
    await session.flush()

    if tattoo_session.is_final_session:
        work.status = TattooWorkStatus.completed

    business = master.business
    try:
        event_id = await _sync_session_to_calendar(tattoo_session, work, master, business)
        if event_id:
            tattoo_session.google_event_id = event_id
    except Exception:
        logger.exception("Failed to sync session %d to Google Calendar", tattoo_session.id)

    await session.commit()
    await session.refresh(work, attribute_names=["sessions"])
    return TattooWorkResponse.model_validate(work)


@router.get("/works/{work_id}", response_model=TattooWorkResponse)
async def get_work(
    work_id: int,
    session: SessionDep,
    master=MasterDep,
):
    """Получить детали тату-работы со всеми сеансами."""
    work = await _get_work_or_404(session, work_id, master)
    return TattooWorkResponse.model_validate(work)


@router.patch("/works/{work_id}", response_model=TattooWorkResponse)
async def update_work(
    work_id: int,
    body: TattooWorkUpdate,
    session: SessionDep,
    master=MasterDep,
):
    """Обновить поля тату-работы (без сеансов)."""
    work = await _get_work_or_404(session, work_id, master)

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(work, field, value)

    await session.commit()
    await session.refresh(work, attribute_names=["sessions"])
    return TattooWorkResponse.model_validate(work)


@router.delete("/works/{work_id}", status_code=204)
async def delete_work(
    work_id: int,
    session: SessionDep,
    master=MasterDep,
):
    """Удалить тату-работу вместе со всеми сеансами и событиями из Google Calendar."""
    work = await _get_work_or_404(session, work_id, master)

    business = master.business
    for tattoo_session in work.sessions:
        try:
            await _remove_session_from_calendar(tattoo_session, master, business)
        except Exception:
            logger.exception(
                "Failed to remove session %d from Google Calendar", tattoo_session.id
            )

    await session.delete(work)
    await session.commit()


# ---------------------------------------------------------------------------
# TattooSession CRUD (вложены в TattooWork)
# ---------------------------------------------------------------------------


async def _get_session_or_404(
    session: SessionDep, work_id: int, session_id: int, master: Master
) -> tuple[TattooSession, TattooWork]:
    work = await _get_work_or_404(session, work_id, master)
    result = await session.execute(
        select(TattooSession).where(
            TattooSession.id == session_id,
            TattooSession.work_id == work.id,
        )
    )
    tattoo_session = result.scalar_one_or_none()
    if not tattoo_session:
        raise HTTPException(status_code=404, detail="Session not found")
    return tattoo_session, work


@router.post(
    "/works/{work_id}/sessions", response_model=TattooSessionResponse, status_code=201
)
async def create_session(
    work_id: int,
    body: TattooSessionCreate,
    session: SessionDep,
    master=MasterDep,
):
    """
    Добавить новый сеанс к существующей тату-работе и отправить событие в Google Calendar.

    Если работа уже была завершена, новый сеанс возвращает её в статус in_progress
    (например, доработка/коррекция), если только сам не помечен как финальный.
    """
    work = await _get_work_or_404(session, work_id, master)

    tattoo_session = TattooSession(
        work_id=work.id,
        session_date=body.session_date,
        quoted_cost=body.quoted_cost,
        cost=body.cost,
        sketch_file_id=body.sketch_file_id,
        result_file_id=body.result_file_id,
        is_final_session=body.is_final_session,
    )
    session.add(tattoo_session)
    await session.flush()

    work.status = (
        TattooWorkStatus.completed if body.is_final_session else TattooWorkStatus.in_progress
    )

    business = master.business
    try:
        event_id = await _sync_session_to_calendar(tattoo_session, work, master, business)
        if event_id:
            tattoo_session.google_event_id = event_id
    except Exception:
        logger.exception("Failed to sync session %d to Google Calendar", tattoo_session.id)

    await session.commit()
    await session.refresh(tattoo_session)
    return TattooSessionResponse.model_validate(tattoo_session)


@router.patch(
    "/works/{work_id}/sessions/{session_id}", response_model=TattooSessionResponse
)
async def update_session(
    work_id: int,
    session_id: int,
    body: TattooSessionUpdate,
    session: SessionDep,
    master=MasterDep,
):
    """Обновить существующий сеанс и синхронизировать с Google Calendar."""
    tattoo_session, work = await _get_session_or_404(session, work_id, session_id, master)

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(tattoo_session, field, value)

    await session.flush()

    if "is_final_session" in update_data:
        work.status = (
            TattooWorkStatus.completed
            if tattoo_session.is_final_session
            else TattooWorkStatus.in_progress
        )

    business = master.business
    try:
        event_id = await _sync_session_to_calendar(tattoo_session, work, master, business)
        if event_id:
            tattoo_session.google_event_id = event_id
    except Exception:
        logger.exception("Failed to sync session %d to Google Calendar", tattoo_session.id)

    await session.commit()
    await session.refresh(tattoo_session)
    return TattooSessionResponse.model_validate(tattoo_session)


@router.delete("/works/{work_id}/sessions/{session_id}", status_code=204)
async def delete_session(
    work_id: int,
    session_id: int,
    session: SessionDep,
    master=MasterDep,
):
    """Удалить сеанс и убрать событие из Google Calendar."""
    tattoo_session, _work = await _get_session_or_404(session, work_id, session_id, master)

    business = master.business
    try:
        await _remove_session_from_calendar(tattoo_session, master, business)
    except Exception:
        logger.exception("Failed to remove session %d from Google Calendar", tattoo_session.id)

    await session.delete(tattoo_session)
    await session.commit()