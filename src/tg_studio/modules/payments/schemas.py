"""Pydantic-схемы платежей."""

from datetime import datetime

from pydantic import BaseModel, Field


class PaymentCancelRequest(BaseModel):
    """Отмена счёта (pending). Причина — в note."""

    note: str | None = Field(None, max_length=2000)


class PaymentOut(BaseModel):
    """Платёж. master_name проставляется эндпоинтом."""

    model_config = {"from_attributes": True}

    id: int
    booking_id: int | None
    amount: float
    kind: str
    status: str
    provider: str
    external_invoice_id: str | None
    payment_url: str | None
    expires_at: datetime | None
    master_id: int | None
    master_name: str | None = None
    note: str | None
    created_at: datetime
    paid_at: datetime | None


class PaymentListResponse(BaseModel):
    payments: list[PaymentOut]
    total: int
