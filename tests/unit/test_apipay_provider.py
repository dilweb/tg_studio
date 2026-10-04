"""Юнит-тесты адаптера ApiPay: HTTP-контракт и перевод статусов.

Провайдер подменяется MockTransport'ом — сеть не дергается. Ключ ApiPay
здесь не участвует (он в env и никогда не в тестах).
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from tg_studio.db.models import Payment
from tg_studio.modules.payments.providers import (
    ApipayProvider,
    InvoiceContext,
    PaymentProviderError,
    apply_provider_status,
    provider_status_to_local,
)

BASE = "https://api.apipay.kz/api/v1"
API = "/api/v1"  # префикс пути внутри request.url.path


def make_payment(**overrides) -> Payment:
    defaults = dict(
        id=1,
        business_id=1,
        amount=Decimal("15000"),
        kind="partial",
        status="pending",
        provider="apipay",
        external_invoice_id="555",
    )
    defaults.update(overrides)
    return Payment(**defaults)


def provider_with(handler) -> ApipayProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=BASE)
    return ApipayProvider(http=client)


def reject_handler(request: httpx.Request) -> httpx.Response:
    pytest.fail(f"HTTP не должен вызываться: {request.method} {request.url.path}")


CTX = InvoiceContext(client_phone="87012345678", client_name="Клиент", description="Тату-работа #1")


async def test_create_invoice_contract():
    """POST /invoices: телефон 8XXXXXXXXXX, целые тенге, idempotency от payment.id."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            201, json={"id": 42, "status": "processing", "amount": "15000.00", "kaspi_qr_link": None}
        )

    created = await provider_with(handler).create_invoice(make_payment(), CTX)
    assert created.external_id == "42"
    assert created.payment_url is None
    assert created.expires_at > datetime.now(UTC) + timedelta(hours=23)

    body = calls[0].read()
    assert b'"phone_number":"87012345678"' in body
    assert b'"amount":15000' in body  # целое число, не строка
    assert b'"external_order_id":"payment-1"' in body
    assert b'"external_order_id_idempotency":"payment-1"' in body


async def test_create_invoice_requires_phone_and_whole_tenge():
    """Без телефона и с дробными тенге — ошибка ещё до HTTP."""
    p = provider_with(reject_handler)
    with pytest.raises(PaymentProviderError, match="телефона"):
        await p.create_invoice(
            make_payment(), InvoiceContext(client_phone=None, client_name="", description="")
        )
    with pytest.raises(PaymentProviderError, match="целые тенге"):
        await p.create_invoice(
            make_payment(amount=Decimal("15000.50")),
            InvoiceContext(client_phone="87012345678", client_name="", description=""),
        )


async def test_create_invoice_truncates_description():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(201, json={"id": 7, "status": "processing"})

    long_ctx = InvoiceContext(
        client_phone="87012345678", client_name="", description="х" * 100
    )
    await provider_with(handler).create_invoice(make_payment(), long_ctx)
    # Лимит ApiPay — 60 символов
    assert '"description":"' + "х" * 60 in calls[0].read().decode()


async def test_create_invoice_error_surfaced():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"error_code": "amount_must_be_whole_tenge"})

    with pytest.raises(PaymentProviderError, match="amount_must_be_whole_tenge"):
        await provider_with(handler).create_invoice(make_payment(), CTX)


async def test_cancel_and_fetch():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == API + "/invoices/555/cancel":
            return httpx.Response(200, json={"id": 555, "status": "cancelled"})
        if request.method == "GET" and request.url.path == API + "/invoices/555":
            return httpx.Response(
                200, json={"id": 555, "status": "paid", "paid_at": "2026-10-03T10:00:00Z"}
            )
        pytest.fail(f"Unexpected HTTP call: {request.method} {request.url.path}")

    p = provider_with(handler)
    await p.cancel_invoice(make_payment())
    invoice = await p.fetch_invoice(make_payment())
    assert invoice["status"] == "paid"


async def test_provider_status_mapping():
    assert provider_status_to_local("processing") == "pending"
    assert provider_status_to_local("error") == "pending"
    assert provider_status_to_local("partially_refunded") == "paid"
    assert provider_status_to_local("refunded") is None


def test_apply_status_transitions():
    paid_at = datetime(2026, 10, 3, 10, 0, tzinfo=UTC)

    # pending → paid: фиксируем paid_at от платёжной системы
    p = make_payment()
    assert apply_provider_status(p, "paid", paid_at) is True
    assert p.status == "paid" and p.paid_at == paid_at

    # «paid» липкий: paid → cancelled задним числом не проводим
    assert apply_provider_status(p, "cancelled") is False
    assert p.status == "paid"

    # повторный paid — no-op, paid_at не перетирается
    assert apply_provider_status(p, "paid") is False
    assert p.paid_at == paid_at

    # статусы могут идти назад: cancelled → paid — легальный переход
    p2 = make_payment(status="cancelled")
    assert apply_provider_status(p2, "paid", paid_at) is True
    assert p2.status == "paid" and p2.paid_at == paid_at

    # error — временное состояние, в CRM не попадает
    p3 = make_payment(status="expired")
    assert apply_provider_status(p3, "error") is True
    assert p3.status == "pending"

    # неизвестный статус — игнор
    assert apply_provider_status(make_payment(), "refunded") is False