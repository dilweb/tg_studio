from pydantic import BaseModel, Field

from tg_studio.modules.business import portfolio_files


class BusinessResponse(BaseModel):
    id: int
    name: str
    description: str | None
    phone: str | None
    is_active: bool


class AdminBusinessResponse(BusinessResponse):
    """Профиль бизнеса для панели владельца (с телеграм-id владельца)."""

    owner_telegram_id: int | None


class BusinessUpdate(BaseModel):
    """Правка профиля бизнеса владельцем в разделе «Бизнес»."""

    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=4000)
    phone: str | None = Field(None, max_length=32)


class MasterCreate(BaseModel):
    full_name: str
    description: str | None = None
    telegram_id: int | None = None
    default_duration_minutes: int | None = None


class MasterUpdate(BaseModel):
    full_name: str | None = None
    description: str | None = None
    telegram_id: int | None = None
    is_active: bool | None = None
    default_duration_minutes: int | None = None
    # Резидент студии (коворкинг): на него действуют резидентские скидки (03)
    is_resident: bool | None = None
    # Телефон для выставления счетов Kaspi (apipay) за бронь мест (коворкинг, 04)
    phone: str | None = None


class MasterPortfolioFileOut(BaseModel):
    id: int
    original_name: str
    mime: str
    size_bytes: int
    created_at: str | None = None
    # публичная ссылка на файл (без авторизации — маркетинговый материал)
    url: str | None = None


class MasterResponse(BaseModel):
    id: int
    full_name: str
    description: str | None
    telegram_id: int | None
    is_active: bool
    # Мягкое удаление: строка остаётся в БД, из списков пропадает
    deleted_at: str | None = None
    # Фото профиля (аватар) — публичная ссылка
    avatar_url: str | None = None
    google_calendar_id: str | None = None
    default_duration_minutes: int | None = None
    # Резидент студии (коворкинг) — резидентские скидки на бронь мест
    is_resident: bool = False
    # Телефон для Kaspi-счётов за бронь мест (коворкинг, 04)
    phone: str | None = None
    # прямая ссылка «добавить календарь в мой Google Calendar» (без письма)
    calendar_add_url: str | None = None
    portfolio: list[MasterPortfolioFileOut] = []


class PublicMasterResponse(BaseModel):
    """Мастер для клиентов (бот, агент записи, миниапп) — без авторизации.

    Только маркетинг: имя, описание, специализации, фото. Без PII:
    telegram_id (писать мастеру в обход студии), google_calendar_id и
    calendar_add_url (подписка на чужой календарь / инъекция событий).
    """

    id: int
    full_name: str
    description: str | None
    avatar_url: str | None = None
    portfolio: list[MasterPortfolioFileOut] = []


def _public_portfolio_out(m) -> list[MasterPortfolioFileOut]:
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


def public_master_out(m) -> PublicMasterResponse:
    return PublicMasterResponse(
        id=m.id,
        full_name=m.full_name,
        description=m.description,
        avatar_url=portfolio_files.avatar_url(m),
        portfolio=_public_portfolio_out(m),
    )
