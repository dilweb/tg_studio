from datetime import datetime

from pydantic import BaseModel, Field


class MasterLoginRequest(BaseModel):
    """Логин мастера по ID (из DBeaver) и паролю."""
    master_id: int
    password: str


class MasterAuthResponse(BaseModel):
    """Ответ с JWT токеном для мастера."""
    access_token: str
    token_type: str = "bearer"
    master_id: int
    master_name: str
    business_id: int


# ---------------------------------------------------------------------------
# TattooSession — один сеанс
# ---------------------------------------------------------------------------


class TattooSessionCreate(BaseModel):
    """Данные для нового сеанса (первый сеанс создаётся вместе с TattooWork)."""
    session_date: datetime = Field(..., description="Дата и время сеанса (ISO 8601)")
    quoted_cost: float | None = Field(None, gt=0, description="Озвученная клиенту стоимость")
    cost: float = Field(..., gt=0, description="Фактическая стоимость сеанса в тенге")
    sketch_file_id: str | None = Field(None, description="Telegram file_id эскиза")
    result_file_id: str | None = Field(None, description="Telegram file_id итогового фото")
    is_final_session: bool = Field(False, description="Этим сеансом работа закрывается")


class TattooSessionUpdate(BaseModel):
    """Обновление существующего сеанса."""
    session_date: datetime | None = None
    quoted_cost: float | None = Field(None, gt=0)
    cost: float | None = Field(None, gt=0)
    sketch_file_id: str | None = None
    result_file_id: str | None = None
    is_final_session: bool | None = None


class TattooSessionResponse(BaseModel):
    """Ответ с данными сеанса."""
    id: int
    work_id: int
    session_date: datetime
    quoted_cost: float | None
    cost: float
    status: str
    google_event_id: str | None
    sketch_file_id: str | None
    result_file_id: str | None
    is_final_session: bool
    llm_verdict: str | None
    llm_observed_size: str | None
    llm_observed_color: str | None
    llm_observed_style: str | None
    llm_notes: str | None
    alert_sent_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# TattooWork — тату целиком (может состоять из нескольких сеансов)
# ---------------------------------------------------------------------------


class TattooWorkCreate(BaseModel):
    """Создание новой тату-работы вместе с первым сеансом."""
    client_id: int
    size: str = Field(..., description="Размер: маленький, средний, большой")
    complexity: str = Field(..., description="Сложность: низкая, средняя, высокая")
    style: str = Field(..., description="Стиль тату")
    placement: str = Field(..., description="Место нанесения")
    first_session: TattooSessionCreate


class TattooWorkUpdate(BaseModel):
    """Обновление полей тату-работы (без сеансов)."""
    size: str | None = None
    complexity: str | None = None
    style: str | None = None
    placement: str | None = None
    status: str | None = None


class TattooWorkResponse(BaseModel):
    """Ответ с данными тату-работы и её сеансами."""
    id: int
    client_id: int
    master_id: int
    business_id: int
    size: str
    complexity: str
    style: str
    placement: str
    status: str
    created_at: datetime
    updated_at: datetime
    sessions: list[TattooSessionResponse] = []

    model_config = {"from_attributes": True}


class TattooWorkListResponse(BaseModel):
    """Список тату-работ мастера."""
    works: list[TattooWorkResponse]
    total: int
