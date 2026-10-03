"""Pydantic-схемы склада расходников."""

import enum
from datetime import datetime

from pydantic import BaseModel, Field

# Справочник категорий — строки в БД, растим без миграций (как стили тату)
SUPPLY_CATEGORIES = [
    "Краска",
    "Иглы и картриджи",
    "Перчатки",
    "Гигиена",
    "Уход",
    "Прочее",
]

SUPPLY_UNITS = ["шт", "мл", "г", "упаковка"]


class SupplyCategory(str, enum.Enum):
    """Валидация категории на входе; значение хранится как есть."""

    PAINT = "Краска"
    NEEDLES = "Иглы и картриджи"
    GLOVES = "Перчатки"
    HYGIENE = "Гигиена"
    AFTERCARE = "Уход"
    OTHER = "Прочее"


class SupplyCreate(BaseModel):
    """Создание позиции. quantity — начальный остаток (движение kind=init)."""

    name: str = Field(..., min_length=1, max_length=256)
    category: SupplyCategory
    unit: str = Field(..., min_length=1, max_length=16)
    quantity: float = Field(0, ge=0)
    min_quantity: float | None = Field(None, ge=0)
    note: str | None = Field(None, max_length=2000)


class SupplyUpdate(BaseModel):
    """Правка позиции. PATCH-семантика: переданное — меняем, None в
    min_quantity/note — очистить. quantity правится только движениями
    (restock/use/adjust), чтобы журнал не расходился с остатком."""

    name: str | None = Field(None, min_length=1, max_length=256)
    category: SupplyCategory | None = None
    unit: str | None = Field(None, min_length=1, max_length=16)
    min_quantity: float | None = Field(None, ge=0)
    note: str | None = Field(None, max_length=2000)
    is_active: bool | None = None


class SupplyActionRequest(BaseModel):
    """Списание (use) или пополнение (restock)."""

    amount: float = Field(..., gt=0)
    note: str | None = Field(None, max_length=2000)
    # Владелец может списать от имени мастера; мастер игнорируется в пользу self
    master_id: int | None = None
    # Привязка к работе/сеансу — база для будущей себестоимости сеанса
    work_id: int | None = None
    session_id: int | None = None


class SupplyAdjustRequest(BaseModel):
    """Ревизия: выставить фактический остаток (delta считается на сервере)."""

    new_quantity: float = Field(..., ge=0)
    note: str | None = Field(None, max_length=2000)


class SupplyOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    name: str
    category: str
    unit: str
    quantity: float
    min_quantity: float | None
    note: str | None
    is_active: bool
    low: bool
    created_at: datetime
    updated_at: datetime


class SupplyListResponse(BaseModel):
    supplies: list[SupplyOut]


class SupplyMovementOut(BaseModel):
    """Движение по складу. master_name/supply_name проставляются эндпоинтом."""

    model_config = {"from_attributes": True}

    id: int
    supply_id: int
    supply_name: str | None = None
    delta: float
    kind: str
    master_id: int | None
    master_name: str | None = None
    work_id: int | None
    session_id: int | None
    note: str | None
    created_at: datetime


class SupplyMovementListResponse(BaseModel):
    movements: list[SupplyMovementOut]
