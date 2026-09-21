from pydantic import BaseModel


class BusinessResponse(BaseModel):
    id: int
    name: str
    description: str | None
    phone: str | None
    is_active: bool


class AdminBusinessResponse(BusinessResponse):
    """Профиль бизнеса для панели владельца (с телеграм-id владельца)."""

    owner_telegram_id: int | None


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


class MasterResponse(BaseModel):
    id: int
    full_name: str
    description: str | None
    telegram_id: int | None
    is_active: bool
    google_calendar_id: str | None = None
    default_duration_minutes: int | None = None
    # прямая ссылка «добавить календарь в мой Google Calendar» (без письма)
    calendar_add_url: str | None = None
