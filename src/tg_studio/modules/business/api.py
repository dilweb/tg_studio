import secrets
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from tg_studio.api.admin_deps import OwnerBusinessDep
from tg_studio.api.deps import SessionDep
from tg_studio.config import settings
from tg_studio.db.models import Business, Master, MasterPortfolioFile
from tg_studio.modules.business import portfolio_files
from tg_studio.modules.business.schemas import (
    AdminBusinessResponse,
    BusinessResponse,
    BusinessUpdate,
    MasterCreate,
    MasterPortfolioFileOut,
    MasterResponse,
    MasterUpdate,
    PricingConfigUpdate,
)
from tg_studio.modules.google_calendar.client import (
    create_calendar,
    delete_calendar,
    share_calendar,
)
from tg_studio.modules.tattoo.pricing import effective_pricing

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
        pricing_config=effective_pricing(business),
    )


@_admin_business.put("", response_model=AdminBusinessResponse)
async def update_business(
    body: BusinessUpdate, session: SessionDep, business: OwnerBusinessDep
):
    """Профиль бизнеса: название, описание, телефон.

    Описание — база знаний AI-агента (ресепшн бота): он отвечает клиентам
    о студии только из этого текста.
    """
    # Пустая строка = очистить значение (description/phone — nullable)
    if body.name is not None:
        business.name = body.name
    if body.description is not None:
        business.description = body.description or None
    if body.phone is not None:
        business.phone = body.phone or None
    await session.commit()
    return AdminBusinessResponse(
        id=business.id,
        name=business.name,
        description=business.description,
        phone=business.phone,
        is_active=business.is_active,
        owner_telegram_id=business.owner_telegram_id,
        pricing_config=effective_pricing(business),
    )


@_admin_business.put("/pricing", response_model=AdminBusinessResponse)
async def update_pricing(
    body: PricingConfigUpdate, session: SessionDep, business: OwnerBusinessDep
):
    """Прайс-конфиг бизнеса: глобальный % и все коэффициенты. Правится целиком."""
    business.pricing_config = body.model_dump(mode="json")
    await session.commit()
    return AdminBusinessResponse(
        id=business.id,
        name=business.name,
        description=business.description,
        phone=business.phone,
        is_active=business.is_active,
        owner_telegram_id=business.owner_telegram_id,
        pricing_config=effective_pricing(business),
    )



# ── Admin: masters ────────────────────────────────────────────────────────────

_admin_masters = APIRouter(prefix="/admin/masters", tags=["admin • masters"])


def _portfolio_out(m: Master) -> list[MasterPortfolioFileOut]:
    """Портфолио мастера (админ-панель): метаданные + публичные ссылки."""
    return [
        MasterPortfolioFileOut(
            id=f.id,
            original_name=f.original_name,
            mime=f.mime,
            size_bytes=f.size_bytes,
            created_at=f.created_at.isoformat() if f.created_at else None,
            url=portfolio_files.portfolio_url(f),
        )
        for f in (m.portfolio_files or [])
    ]


def _master_to_response(m: Master) -> MasterResponse:
    return MasterResponse(
        id=m.id,
        full_name=m.full_name,
        description=m.description,
        telegram_id=m.telegram_id,
        is_active=m.is_active,
        deleted_at=m.deleted_at.isoformat() if m.deleted_at else None,
        google_calendar_id=m.google_calendar_id,
        default_duration_minutes=m.default_duration_minutes,
        specializations=m.specializations or [],
        # прямая ссылка: открой — календарь добавится в твой Google Calendar,
        # письмо-приглашение для этого не нужно
        calendar_add_url=(
            f"https://calendar.google.com/calendar/u/0/r?cid={quote(m.google_calendar_id)}"
            if m.google_calendar_id
            else None
        ),
        avatar_url=portfolio_files.avatar_url(m),
        portfolio=_portfolio_out(m),
    )


async def _auto_create_calendar(master: Master, business: Business) -> None:
    """Завести мастеру личный календарь в Google сразу при создании мастера
    (если GC подключен) и сразу открыть владельцу доступ. Любая ошибка не
    валит создание мастера — календарь можно завести позже кнопкой."""
    if not business.google_calendar_credentials_json:
        return
    try:
        calendar_id = await create_calendar(
            business.google_calendar_credentials_json, summary=master.full_name
        )
    except Exception:
        return
    if not calendar_id:
        return
    master.google_calendar_id = calendar_id
    if business.google_share_email:
        try:
            await share_calendar(
                business.google_calendar_credentials_json,
                calendar_id,
                business.google_share_email,
            )
        except Exception:
            pass  # дешарить/решарить можно кнопкой «Открыть доступ» в «Бизнесе»


async def _get_own_master(session, master_id: int, business_id: int) -> Master:
    master = await session.get(Master, master_id)
    if master is None or master.business_id != business_id:
        raise HTTPException(status_code=404, detail="Мастер не найден")
    return master


@_admin_masters.get("", response_model=list[MasterResponse])
async def list_masters(
    session: SessionDep,
    business: OwnerBusinessDep,
    include_deleted: bool = False,
):
    """Список мастеров; удалённые (мягко) по умолчанию скрыты."""
    query = select(Master).where(Master.business_id == business.id)
    if not include_deleted:
        query = query.where(Master.deleted_at.is_(None))
    result = await session.execute(
        query.options(selectinload(Master.portfolio_files)).order_by(Master.id)
    )
    return [_master_to_response(m) for m in result.scalars().all()]


@_admin_masters.post("", response_model=MasterResponse, status_code=201)
async def create_master(body: MasterCreate, session: SessionDep, business: OwnerBusinessDep):
    master = Master(
        business_id=business.id,
        full_name=body.full_name,
        description=body.description,
        telegram_id=body.telegram_id,
        default_duration_minutes=body.default_duration_minutes,
        specializations=body.specializations,
    )
    session.add(master)
    await _auto_create_calendar(master, business)
    await session.commit()
    await session.refresh(master, attribute_names=["portfolio_files"])
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
    if body.default_duration_minutes is not None:
        master.default_duration_minutes = body.default_duration_minutes
    if body.specializations is not None:
        master.specializations = body.specializations
    await session.commit()
    await session.refresh(master, attribute_names=["portfolio_files"])
    return _master_to_response(master)


@_admin_masters.delete("/{master_id}", status_code=204)
async def delete_master(master_id: int, session: SessionDep, business: OwnerBusinessDep):
    """Удалить мастера (мягко): строка и вся история остаются в БД (работы,
    платежи — для анализа), но мастер пропадает из списков, а его календарь
    в Google удаляется."""
    master = await _get_own_master(session, master_id, business.id)
    master.deleted_at = datetime.now(timezone.utc)
    master.is_active = False

    # Календарь мастера удаляем, id в БД затираем (календарь больше не существует).
    if master.google_calendar_id and business.google_calendar_credentials_json:
        deleted = await delete_calendar(
            business.google_calendar_credentials_json, master.google_calendar_id
        )
        if deleted:
            master.google_calendar_id = None

    await session.commit()


@_admin_masters.post("/{master_id}/restore", response_model=MasterResponse)
async def restore_master(
    master_id: int, session: SessionDep, business: OwnerBusinessDep
):
    """Вернуть удалённого мастера в списки (запись в БД не удалялась)."""
    master = await _get_own_master(session, master_id, business.id)
    master.deleted_at = None
    master.is_active = True
    await session.commit()
    await session.refresh(master, attribute_names=["portfolio_files"])
    return _master_to_response(master)


@_admin_masters.post("/{master_id}/registration-link", status_code=200)
async def create_registration_link(master_id: int, session: SessionDep, business: OwnerBusinessDep):
    master = await _get_own_master(session, master_id, business.id)
    token = secrets.token_urlsafe(24)
    master.registration_token = token
    await session.commit()
    payload = f"master_{token}"
    full_link = f"https://t.me/{settings.bot_username}?start={payload}" if settings.bot_username else None
    return {"payload": payload, "link": full_link}


# ── Admin: portfolio мастеров ────────────────────────────────────────────────

async def _read_image_upload(file: UploadFile) -> tuple[bytes, str]:
    """Общая проверка аплоада: image/*, не пустой, ≤10 МБ."""
    mime = (file.content_type or "").lower()
    if not mime.startswith("image/"):
        raise HTTPException(status_code=415, detail="Только изображения (image/*)")
    data = await file.read()
    if len(data) == 0:
        raise HTTPException(status_code=422, detail="Пустой файл")
    if len(data) > portfolio_files.MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="Файл больше 10 МБ")
    return data, mime


@_admin_masters.post("/{master_id}/avatar", response_model=MasterResponse)
async def upload_avatar(
    master_id: int,
    file: UploadFile,
    session: SessionDep,
    business: OwnerBusinessDep,
):
    """Загрузить фото профиля мастера (image/*, до 10 МБ). Публично видно клиентам."""
    master = await _get_own_master(session, master_id, business.id)
    data, mime = await _read_image_upload(file)
    # save_avatar сам затирает прошлый файл
    master.avatar_stored_path = portfolio_files.save_avatar(
        master, data, file.filename or "avatar", mime
    )
    await session.commit()
    await session.refresh(master, attribute_names=["portfolio_files"])
    return _master_to_response(master)


@_admin_masters.delete("/{master_id}/avatar", status_code=204)
async def delete_avatar(
    master_id: int, session: SessionDep, business: OwnerBusinessDep
):
    master = await _get_own_master(session, master_id, business.id)
    portfolio_files.delete_avatar_file(master)
    await session.commit()


@_admin_masters.post("/{master_id}/portfolio", response_model=MasterResponse, status_code=201)
async def upload_portfolio(
    master_id: int,
    file: UploadFile,
    session: SessionDep,
    business: OwnerBusinessDep,
):
    """Загрузить фото в портфолио мастера (image/*, до 10 МБ)."""
    master = await _get_own_master(session, master_id, business.id)
    data, mime = await _read_image_upload(file)
    portfolio_files.save_file(
        session,
        master.id,
        data,
        original_name=file.filename or "photo",
        mime=mime,
    )
    await session.commit()
    await session.refresh(master, attribute_names=["portfolio_files"])
    return _master_to_response(master)


@_admin_masters.delete("/{master_id}/portfolio/{file_id}", status_code=204)
async def delete_portfolio_file(
    master_id: int, file_id: int, session: SessionDep, business: OwnerBusinessDep
):
    master = await _get_own_master(session, master_id, business.id)
    record = await session.get(MasterPortfolioFile, file_id)
    if record is None or record.master_id != master.id:
        raise HTTPException(status_code=404, detail="Файл не найден")
    portfolio_files.delete_file(record)
    await session.delete(record)
    await session.commit()


# ── Public: мастера для клиентов (бот, агент записи) ─────────────────────────

_public_masters = APIRouter(prefix="/public/masters", tags=["public • masters"])


async def _public_master_list(session: AsyncSession) -> list[Master]:
    result = await session.execute(
        select(Master)
        .where(Master.is_active.is_(True))
        .options(selectinload(Master.portfolio_files))
        .order_by(Master.id)
    )
    return list(result.scalars().all())


@_public_masters.get("", response_model=list[MasterResponse])
async def public_masters(session: SessionDep):
    """Активные мастера с портфолио — без авторизации (маркетинговый материал)."""
    return [_master_to_response(m) for m in await _public_master_list(session)]


@_public_masters.get("/{master_id}/avatar")
async def public_master_avatar(master_id: int, session: SessionDep):
    """Фото профиля мастера — публичный read-only (клиенты видят в миниаппе/боте)."""
    master = await session.get(Master, master_id)
    # Неактивного мастера клиентам не показываем
    if master is None or not master.is_active or not master.avatar_stored_path:
        raise HTTPException(status_code=404, detail="Файл не найден")
    data = portfolio_files.read_avatar(master)
    if data is None:
        raise HTTPException(status_code=404, detail="Файл не найден")
    return Response(content=data, media_type=portfolio_files.avatar_mime(master))


@_public_masters.get("/{master_id}/portfolio/{file_id}")
async def public_portfolio_file(master_id: int, file_id: int, session: SessionDep):
    """Фото портфолио — публичный read-only (клиент переходит по ссылке из бота)."""
    result = await session.execute(
        select(MasterPortfolioFile).where(
            MasterPortfolioFile.id == file_id,
            MasterPortfolioFile.master_id == master_id,
        )
    )
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="Файл не найден")
    master = await session.get(Master, master_id)
    # Неактивного мастера клиентам не показываем
    if master is None or not master.is_active:
        raise HTTPException(status_code=404, detail="Файл не найден")
    data = portfolio_files.read_file(record)
    if data is None:
        raise HTTPException(status_code=404, detail="Файл не найден")
    return Response(content=data, media_type=record.mime)


# ── Assemble ──────────────────────────────────────────────────────────────────

router.include_router(_public)
router.include_router(_admin_business)
router.include_router(_admin_masters)
router.include_router(_public_masters)
