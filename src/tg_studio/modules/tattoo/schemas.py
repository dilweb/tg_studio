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


class TattooProjectCreate(BaseModel):
    """Создание нового проекта татуировки."""
    size: str = Field(..., description="Размер: маленький, средний, большой")
    complexity: str = Field(..., description="Сложность: низкая, средняя, высокая")
    placement: str = Field(..., description="Место нанесения")
    session_date: datetime = Field(..., description="Дата и время сеанса (ISO 8601)")
    cost: float = Field(..., gt=0, description="Стоимость в тенге")


class TattooProjectUpdate(BaseModel):
    """Обновление существующего проекта."""
    size: str | None = None
    complexity: str | None = None
    placement: str | None = None
    session_date: datetime | None = None
    cost: float | None = Field(None, gt=0)


class TattooProjectResponse(BaseModel):
    """Ответ с данными проекта."""
    id: int
    master_id: int
    business_id: int
    size: str
    complexity: str
    placement: str
    session_date: datetime
    cost: float
    status: str
    google_event_id: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TattooProjectListResponse(BaseModel):
    """Список проектов мастера."""
    projects: list[TattooProjectResponse]
    total: int