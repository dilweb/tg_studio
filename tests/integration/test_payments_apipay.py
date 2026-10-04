"""Интеграционные тесты apipay: вебхук (HMAC), опрос статуса (sync).

Ключ ApiPay не задан — адаптер подменяется MockTransport'ом; секрет вебхука
монкипатчится. Подпись считается по СЫРОГОМУ телу, как и в проде.
"""

import hashlib
import hmac
from datetime import datetime
from decimal import Decimal

import httpx
import pytest

from tg_studio.config import settings
from tg_studio.db.models import Payment, UserRole
from tg_studio.modules.payments import providers

from ..conftest import make_business, make_user

OWNER_HEADERS = {"X-Debug-User-Id": "99999"}
WEBHOOK_SECRET = "whsec-test-123"


@pytest.fixture
async def business(db_session):
    return await make_business(db_session, owner_telegram_id=99999)


@pytest.fixture
async def apipay_payment(db_session, business):
    """Счёт Kaspi без работы: вебхук и sync не требуют work_id."""
    user = await make_user(db_session, telegram_id=5550001, first_name="Владелец", role=UserRole.owner)
    payment = Payment(
        business_id=business.id,
        amount=Decimal("30000"),
        kind="prepay",
        status="pending",
        provider="apipay",
        external_invoice_id="424242",
        created_by_user_id=user.id,
    )
    db_session.add(payment)
    await db_session.commit()
    return payment


def signed_headers(body: bytes, secret: str = WEBHOOK_SECRET) -> dict:
    return {"X-Webhook-Signature": "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()}


def webhook_body(invoice: dict, event: str = "invoice.status_changed") -> bytes:
    import json

    return json.dumps({"event": event, "invoice": invoice, "source": "test"}).encode()


@pytest.mark.asyncio
async def test_webhook_requires_secret(api_client, apipay_payment):
    """Секрет не настроен — вебхук закрыт (503), иначе его можно подделать."""
    body = webhook_body({"id": 424242, "status": "paid"})
    resp = await api_client.post(
        "/api/payments/webhook/apipay", content=body, headers=signed_headers(body, "любой")
    )
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_webhook_bad_signature(api_client, apipay_payment, monkeypatch):
    monkeypatch.setattr(settings, "apipay_webhook_secret", WEBHOOK_SECRET)
    body = webhook_body({"id": 424242, "status": "paid"})
    resp = await api_client.post(
        "/api/payments/webhook/apipay",
        content=body,
        headers=signed_headers(body, "чужой-секрет"),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_webhook_marks_paid(api_client, db_session, business, apipay_payment, monkeypatch):
    """Валидный вебхук: pending → paid, paid_at от платёжной системы."""
    monkeypatch.setattr(settings, "apipay_webhook_secret", WEBHOOK_SECRET)
    body = webhook_body(
        {
            "id": 424242,
            "status": "paid",
            "amount": "30000.00",
            "paid_at": "2026-10-03T10:00:00+00:00",
        }
    )
    resp = await api_client.post(
        "/api/payments/webhook/apipay", content=body, headers=signed_headers(body)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["changed"] is True

    await db_session.refresh(apipay_payment)
    assert apipay_payment.status == "paid"
    # SQLite отдаёт naive datetime — сравниваем без таймзоны
    assert apipay_payment.paid_at.replace(tzinfo=None) == datetime(2026, 10, 3, 10, 0)

    # Повторная доставка того же события (ретрай) — идемпотентна
    resp = await api_client.post(
        "/api/payments/webhook/apipay", content=body, headers=signed_headers(body)
    )
    assert resp.json()["changed"] is False
    assert apipay_payment.paid_at.replace(tzinfo=None) == datetime(2026, 10, 3, 10, 0)


@pytest.mark.asyncio
async def test_webhook_cancelled_then_paid(api_client, db_session, apipay_payment, monkeypatch):
    """Статусы могут идти назад: cancelled → paid — легальный переход."""
    monkeypatch.setattr(settings, "apipay_webhook_secret", WEBHOOK_SECRET)
    cancelled = webhook_body({"id": 424242, "status": "cancelled"})
    resp = await api_client.post(
        "/api/payments/webhook/apipay", content=cancelled, headers=signed_headers(cancelled)
    )
    assert resp.status_code == 200
    await db_session.refresh(apipay_payment)
    assert apipay_payment.status == "cancelled"

    paid = webhook_body({"id": 424242, "status": "paid"})
    resp = await api_client.post(
        "/api/payments/webhook/apipay", content=paid, headers=signed_headers(paid)
    )
    assert resp.json()["changed"] is True
    await db_session.refresh(apipay_payment)
    assert apipay_payment.status == "paid"


@pytest.mark.asyncio
async def test_webhook_paid_not_downgraded(api_client, db_session, apipay_payment, monkeypatch):
    """Оплаченный счёт не отменяется задним числом (paid — липкий)."""
    monkeypatch.setattr(settings, "apipay_webhook_secret", WEBHOOK_SECRET)
    paid = webhook_body({"id": 424242, "status": "paid"})
    await api_client.post("/api/payments/webhook/apipay", content=paid, headers=signed_headers(paid))
    await db_session.refresh(apipay_payment)
    assert apipay_payment.status == "paid"

    cancelled = webhook_body({"id": 424242, "status": "cancelled"})
    resp = await api_client.post(
        "/api/payments/webhook/apipay", content=cancelled, headers=signed_headers(cancelled)
    )
    assert resp.json()["changed"] is False
    await db_session.refresh(apipay_payment)
    assert apipay_payment.status == "paid"


@pytest.mark.asyncio
async def test_webhook_unknown_invoice_and_events(api_client, monkeypatch):
    """Неизвестный счёт — ok (не провоцируем ретраи); прочие события — игнор."""
    monkeypatch.setattr(settings, "apipay_webhook_secret", WEBHOOK_SECRET)
    unknown = webhook_body({"id": 999999, "status": "paid"})
    resp = await api_client.post(
        "/api/payments/webhook/apipay", content=unknown, headers=signed_headers(unknown)
    )
    assert resp.status_code == 200
    assert resp.json()["unknown"] == "999999"

    other = webhook_body({"id": 424242, "status": "paid"}, event="webhook.test")
    resp = await api_client.post(
        "/api/payments/webhook/apipay", content=other, headers=signed_headers(other)
    )
    assert resp.json()["ignored"] == "webhook.test"

    # невалидная подпись у служебного события всё равно отклоняется
    bad = webhook_body({"id": 424242, "status": "paid"}, event="webhook.test")
    resp = await api_client.post(
        "/api/payments/webhook/apipay", content=bad, headers=signed_headers(bad, "прочее")
    )
    assert resp.status_code == 403


def make_mock_provider(invoice: dict) -> providers.ApipayProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=invoice)

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://api.apipay.kz/api/v1",
    )
    return providers.ApipayProvider(http=client)


@pytest.mark.asyncio
async def test_sync_polls_apipay(api_client, db_session, business, apipay_payment, monkeypatch):
    """Опрос как страховка вебхука: GET /invoices/{id} подтягивает paid."""
    monkeypatch.setattr(settings, "apipay_api_key", "test-key")  # sync не проверяет ключ, но env-стиль
    monkeypatch.setattr(providers, "_APIPAY", make_mock_provider({"id": 424242, "status": "paid"}))
    resp = await api_client.post(f"/api/payments/{apipay_payment.id}/sync", headers=OWNER_HEADERS)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "paid"
    await db_session.refresh(apipay_payment)
    assert apipay_payment.status == "paid"


@pytest.mark.asyncio
async def test_sync_manual_rejected(api_client, db_session, business, apipay_payment):
    """Ручной платёж не опрашивается — его статус знает только человек."""
    user = await make_user(db_session, telegram_id=5550002, first_name="Владелец 2", role=UserRole.owner)
    payment = Payment(
        business_id=business.id,
        amount=Decimal("1000"),
        kind="partial",
        status="pending",
        provider="manual",
        created_by_user_id=user.id,
    )
    db_session.add(payment)
    await db_session.commit()
    resp = await api_client.post(f"/api/payments/{payment.id}/sync", headers=OWNER_HEADERS)
    assert resp.status_code == 409
    assert "Kaspi" in resp.json()["detail"]