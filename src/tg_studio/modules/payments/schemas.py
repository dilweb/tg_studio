"""Pydantic-схемы платежей."""

import enum
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class PaymentKindIn(str, enum.Enum):
    """Тип платежа. None в PaymentCreate → авто: сумма ≥ баланса → final."""

    prepay = "prepay"
    partial = "partial"
    final = "final"


class PaymentCreate(BaseModel):
    """Выставить счёт. contract_price — фиксация/пересмотр договорной цены:
    при первом счёте работы обязателен (frontend подставляет цену из прайса),
    дальше передаётся, если цену пересмотрели."""

    work_id: int
    session_id: int | None = None
    amount: float = Field(..., gt=0)
    kind: PaymentKindIn | None = None
    contract_price: float | None = Field(None, ge=0)
    # None → авто: apipay, если ключ подключён, иначе manual.
    # Явный manual — «клиент платит наличные», даже когда apipay есть.
    provider: Literal["manual", "apipay"] | None = None
    # Оплатили на месте (наличные/перевод) — только manual: apipay
    # подтверждает только вебхук от платёжной системы
    paid_now: bool = False
    note: str | None = Field(None, max_length=2000)


class PaymentCancelRequest(BaseModel):
    """Отмена счёта (pending). Причина — в note."""

    note: str | None = Field(None, max_length=2000)


class PaymentOut(BaseModel):
    """Платёж. client_name/master_name проставляются эндпоинтом."""

    model_config = {"from_attributes": True}

    id: int
    work_id: int | None
    session_id: int | None
    amount: float
    kind: str
    status: str
    provider: str
    external_invoice_id: str | None
    payment_url: str | None
    expires_at: datetime | None
    master_id: int | None
    master_name: str | None = None
    client_name: str | None = None
    note: str | None
    created_at: datetime
    paid_at: datetime | None


class PaymentListResponse(BaseModel):
    payments: list[PaymentOut]
    total: int


class WorkBalanceOut(BaseModel):
    """Баланс работы: всё вычислено на лету из фактов (цена, платежи).

    contract_price None — цена ещё не зафиксирована (счёта не было).
    client_phone нужен диалогу завершения сеанса: apipay-счёт идёт по
    номеру клиента.
    """

    work_id: int
    client_id: int
    client_name: str
    client_phone: str | None
    contract_price: float | None
    paid_total: float
    balance: float | None
    # Счёт Kaspi доступен (ключ подключён) — фронт предлагает его
    apipay_ready: bool
