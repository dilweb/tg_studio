from pydantic import BaseModel, Field
from typing import Literal


class BusinessResponse(BaseModel):
    id: int
    name: str
    description: str | None
    phone: str | None
    is_active: bool


class MasterCreate(BaseModel):
    full_name: str
    description: str | None = None
    telegram_id: int | None = None
    service_ids: list[int] = []


class MasterUpdate(BaseModel):
    full_name: str | None = None
    description: str | None = None
    telegram_id: int | None = None
    is_active: bool | None = None


class MasterServicesUpdate(BaseModel):
    service_ids: list[int]


class MasterResponse(BaseModel):
    id: int
    full_name: str
    description: str | None
    telegram_id: int | None
    is_active: bool
    service_ids: list[int]


class ServiceCreate(BaseModel):
    name: str
    description: str | None = None
    price: float = Field(gt=0)


class ServiceUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    price: float | None = Field(default=None, gt=0)
    is_active: bool | None = None


class ServiceResponse(BaseModel):
    id: int
    name: str
    description: str | None
    price: float
    is_active: bool
