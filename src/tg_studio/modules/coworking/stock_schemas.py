"""Pydantic-схемы склада и продаж коворкинга (06)."""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Позиции склада (products) — расширение supplies ценами
# ---------------------------------------------------------------------------


class ProductCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    category: str = Field(..., min_length=1, max_length=64)
    unit: str = Field(..., min_length=1, max_length=16)
    quantity: float = Field(0, ge=0)
    min_quantity: float | None = Field(None, ge=0)
    purchase_price: float | None = Field(None, ge=0)
    sale_price: float | None = Field(None, ge=0)
    note: str | None = Field(None, max_length=2000)


class ProductUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=256)
    category: str | None = Field(None, min_length=1, max_length=64)
    unit: str | None = Field(None, min_length=1, max_length=16)
    min_quantity: float | None = Field(None, ge=0)
    purchase_price: float | None = Field(None, ge=0)
    sale_price: float | None = Field(None, ge=0)
    note: str | None = Field(None, max_length=2000)
    is_active: bool | None = None


class ProductOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    name: str
    category: str
    unit: str
    quantity: float
    min_quantity: float | None
    purchase_price: float | None
    sale_price: float | None
    note: str | None
    is_active: bool
    low: bool
    created_at: datetime
    updated_at: datetime


class ProductListResponse(BaseModel):
    products: list[ProductOut]


# ---------------------------------------------------------------------------
# Движения склада
# ---------------------------------------------------------------------------


class StockPurchase(BaseModel):
    """Закупка: пополнение с пересчётом средневзвешенной себестоимости."""

    supply_id: int
    qty: float = Field(..., gt=0)
    unit_cost: float | None = Field(None, ge=0)
    note: str | None = Field(None, max_length=2000)


class StockConsume(BaseModel):
    """Обеспечение площадки: списание без оплаты (расход студии)."""

    supply_id: int
    qty: float = Field(..., gt=0)
    reason: str = Field(..., min_length=1, max_length=2000)
    booking_id: int | None = None

    @field_validator("reason")
    @classmethod
    def _strip_reason(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Причина не должна быть пустой")
        return v


class StockAdjust(BaseModel):
    """Ревизия: выставить фактический остаток."""

    supply_id: int
    new_quantity: float = Field(..., ge=0)
    reason: str = Field(..., min_length=1, max_length=2000)

    @field_validator("reason")
    @classmethod
    def _strip_reason(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Основание не должно быть пустым")
        return v


class StockMovementOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    supply_id: int
    supply_name: str | None = None
    delta: float
    kind: str
    master_id: int | None
    master_name: str | None = None
    sale_id: int | None
    booking_id: int | None
    actor_user_id: int | None
    note: str | None
    created_at: datetime


class StockMovementListResponse(BaseModel):
    movements: list[StockMovementOut]
    total: int


# ---------------------------------------------------------------------------
# Продажи
# ---------------------------------------------------------------------------


class SaleItemCreate(BaseModel):
    supply_id: int
    qty: float = Field(..., gt=0)


class SaleCreate(BaseModel):
    """Продажа: корзина + покупатель + способ оплаты.

    Покупатель: master_id (продажа мастеру) ИЛИ buyer_name/buyer_phone
    (разовый контакт клиента мастера). balance — только мастеру.
    """

    items: list[SaleItemCreate] = Field(..., min_length=1, max_length=50)
    master_id: int | None = None
    buyer_name: str | None = Field(None, max_length=256)
    buyer_phone: str | None = Field(None, max_length=32)
    payment_method: str  # balance | cash | card
    booking_id: int | None = None
    note: str | None = Field(None, max_length=2000)

    @model_validator(mode="after")
    def _check(self):
        if self.payment_method not in ("balance", "cash", "card"):
            raise ValueError("payment_method: balance | cash | card")
        # Мастер может не передавать master_id — API подставит его сам.
        # Для админа/владельца без master_id нужен разовый контакт.
        if (
            self.master_id is None
            and not (self.buyer_name or "").strip()
            and self.payment_method != "balance"
        ):
            raise ValueError("Укажите покупателя: master_id или имя разового контакта")
        if self.payment_method == "balance" and self.master_id is None and (self.buyer_name or "").strip():
            raise ValueError("Оплата с баланса доступна только мастеру")
        return self


class SaleItemOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    supply_id: int
    supply_name: str | None = None
    qty: float
    unit_price: float
    unit_cost: float
    amount: float


class SaleOut(BaseModel):
    model_config = {"from_attributes": True}

    id: int
    business_id: int
    master_id: int | None
    master_name: str | None = None
    buyer_name: str | None
    buyer_phone: str | None
    total_amount: float
    currency: str
    payment_method: str
    status: str
    admin_id: int | None
    admin_name: str | None = None
    created_by_user_id: int
    booking_id: int | None
    note: str | None
    created_at: datetime
    items: list[SaleItemOut]
    payment_url: str | None = None


class SaleListResponse(BaseModel):
    sales: list[SaleOut]
    total: int


class SaleRefundRequest(BaseModel):
    """Возврат: полный или частичный по позициям."""

    items: list[SaleItemCreate] | None = None  # None — полный возврат
    reason: str = Field(..., min_length=1, max_length=2000)
