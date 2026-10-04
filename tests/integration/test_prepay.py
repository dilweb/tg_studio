"""Интеграционные тесты предоплаты брони.

Мастер подтвердил работу (accept оффера) → фиксируется договорная цена и
сразу счёт на prepay_percent (30% по умолчанию, настраивается в «Бизнесе»).
Не оплачен за 24ч → бронь отменяется, слот свободен, обе стороны
уведомлены. Telegram-отправки подменяются фейками.
"""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from tg_studio.config import AllowedUser, settings
from tg_studio.db.models import UserRole
from tg_studio.modules.payments import providers
from tg_studio.modules.payments import service as payments_service
from tg_studio.modules.tattoo import offers as offers_module

from ..conftest import make_business, make_client, make_master, make_user

OWNER_HEADERS = {"X-Debug-User-Id": "99999"}
STYLE = "Лайнворк (Linework)"


@pytest.fixture
async def business(db_session, monkeypatch):
    return await make_business(db_session, owner_telegram_id=99999)


@pytest.fixture
async def client_row(db_session):
    client = await make_client(db_session, full_name="Дилявер Тестов")
    client.phone = "+7 (701) 234-56-78"
    await db_session.commit()
    return client


@pytest.fixture
def no_telegram(monkeypatch):
    sent: list[tuple[int, str]] = []

    async def fake_send(telegram_id, text):
        sent.append((telegram_id, text))

    async def fake_owner(session):
        return 99999

    monkeypatch.setattr(payments_service, "send_text_to_telegram", fake_send)
    monkeypatch.setattr(payments_service, "resolve_owner_telegram_id", fake_owner)
    # Офферные уведомления тоже молчат (иначе тесты дёргают реальный Telegram)
    async def fake_notify(session, offer):
        pass

    async def fake_escalate(session, work):
        pass

    monkeypatch.setattr(offers_module, "notify_master", fake_notify)
    monkeypatch.setattr(offers_module, "escalate_to_owner", fake_escalate)
    return sent


async def make_master_with(monkeypatch, db_session, business, tg_id, name):
    user = await make_user(db_session, telegram_id=tg_id, first_name=name, role=UserRole.master)
    m = await make_master(db_session, business_id=business.id, full_name=name)
    m.user_id = user.id
    await db_session.commit()
    monkeypatch.setattr(
        settings,
        "allowed_users",
        [
            *(u for u in settings.allowed_users if u.id != tg_id),
            AllowedUser(id=tg_id, role="master"),
        ],
    )
    return m


async def create_work(api_client, client_row, price=100000, **overrides) -> dict:
    body = {
        "client_id": client_row.id,
        "master_id": None,
        "size_length_cm": 15,
        "size_height_cm": 10,
        "complexity": "средняя",
        "style": STYLE,
        "placement": "Предплечье (внутренняя / внешняя сторона)",
        "first_session": {
            "session_date": (datetime.now(UTC) + timedelta(days=7))
            .replace(microsecond=0)
            .isoformat(),
            "recommended_price": price,
        },
    }
    body.update(overrides)
    resp = await api_client.post("/api/tattoo/works", json=body, headers=OWNER_HEADERS)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def pending_offer(api_client, work_id: int) -> dict:
    resp = await api_client.get("/api/tattoo/offers", headers=OWNER_HEADERS)
    assert resp.status_code == 200
    return next(o for o in resp.json()["offers"] if o["work_id"] == work_id and o["status"] == "pending")


async def accept(api_client, offer_id: int, headers=OWNER_HEADERS, **params) -> dict:
    query = "".join(f"&{k}={v}" for k, v in params.items())
    resp = await api_client.post(
        f"/api/tattoo/offers/{offer_id}/accept?1=1{query}", headers=headers
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def balance(api_client, work_id: int) -> dict:
    resp = await api_client.get(f"/api/payments/works/{work_id}/balance", headers=OWNER_HEADERS)
    assert resp.status_code == 200, resp.text
    return resp.json()


def mock_apipay(invoice: dict | None = None, fail: bool = False):
    """Подмена ApiPay-адаптера: успех, ошибка — как скажешь."""

    def handler(request: httpx.Request) -> httpx.Response:
        if fail:
            return httpx.Response(500, json={"error_code": "boom"})
        return httpx.Response(201, json=invoice or {"id": 777, "status": "processing"})

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.apipay.kz/api/v1"
    )
    return providers.ApipayProvider(http=client)


@pytest.mark.asyncio
async def test_accept_creates_manual_prepay(
    api_client, monkeypatch, db_session, business, client_row, no_telegram
):
    """Ключа apipay нет → manual-счёт 30%; цена фиксируется; дедлайн ~24ч."""
    master_a = await make_master_with(monkeypatch, db_session, business, 70001, "Мастер А")
    work = await create_work(api_client, client_row, price=100000)
    offer = await pending_offer(api_client, work["id"])

    data = await accept(api_client, offer["id"], headers={"X-Debug-User-Id": "70001"})
    resp = await api_client.get(f"/api/tattoo/works/{work['id']}", headers=OWNER_HEADERS)
    assert resp.json()["master_id"] == master_a.id

    assert data["prepay_amount"] == 30000
    assert data["prepay_provider"] == "manual"

    b = await balance(api_client, work["id"])
    assert b["contract_price"] == 100000
    # Счёт ещё pending: в балансе только оплаченное
    assert b["paid_total"] == 0
    assert b["balance"] == 100000

    resp = await api_client.get("/api/payments?limit=10", headers=OWNER_HEADERS)
    payment = next(p for p in resp.json()["payments"] if p["work_id"] == work["id"])
    assert payment["kind"] == "prepay"
    assert payment["status"] == "pending"
    assert payment["amount"] == 30000

    # Оплата предоплаты закрывает часть баланса
    await api_client.post(f"/api/payments/{payment['id']}/mark-paid", headers=OWNER_HEADERS)
    b = await balance(api_client, work["id"])
    assert b["paid_total"] == 30000 and b["balance"] == 70000


@pytest.mark.asyncio
async def test_accept_creates_apipay_prepay(
    api_client, monkeypatch, db_session, business, client_row, no_telegram
):
    """ApPay настроен и есть телефон → счёт Kaspi с external_invoice_id."""
    await make_master_with(monkeypatch, db_session, business, 70001, "Мастер А")
    monkeypatch.setattr(settings, "apipay_api_key", "test-key")
    monkeypatch.setattr(providers, "_APIPAY", mock_apipay())
    work = await create_work(api_client, client_row, price=100000)
    offer = await pending_offer(api_client, work["id"])

    data = await accept(api_client, offer["id"])
    assert data["prepay_provider"] == "apipay"
    assert data["prepay_amount"] == 30000

    resp = await api_client.get("/api/payments?limit=10", headers=OWNER_HEADERS)
    payment = next(p for p in resp.json()["payments"] if p["work_id"] == work["id"])
    assert payment["provider"] == "apipay"
    assert payment["external_invoice_id"] == "777"


@pytest.mark.asyncio
async def test_apipay_failure_falls_back_to_manual(
    api_client, monkeypatch, db_session, business, client_row, no_telegram
):
    """Сбой Kaspi не срывает подтверждение работы: тихо падаем в manual."""
    await make_master_with(monkeypatch, db_session, business, 70001, "Мастер А")
    monkeypatch.setattr(settings, "apipay_api_key", "test-key")
    monkeypatch.setattr(providers, "_APIPAY", mock_apipay(fail=True))
    work = await create_work(api_client, client_row, price=100000)
    offer = await pending_offer(api_client, work["id"])

    data = await accept(api_client, offer["id"])
    assert data["prepay_provider"] == "manual"
    assert data["prepay_amount"] == 30000


@pytest.mark.asyncio
async def test_prepay_percent_from_config(
    api_client, monkeypatch, db_session, business, client_row, no_telegram
):
    """prepay_percent из pricing_config бизнеса (50%)."""
    business.pricing_config = {"prepay_percent": 50}
    await db_session.commit()
    await make_master_with(monkeypatch, db_session, business, 70001, "Мастер А")
    work = await create_work(api_client, client_row, price=90000)
    offer = await pending_offer(api_client, work["id"])

    data = await accept(api_client, offer["id"])
    assert data["prepay_amount"] == 45000


@pytest.mark.asyncio
async def test_expire_unpaid_prepay_cancels_booking(
    api_client, monkeypatch, db_session, business, client_row, no_telegram
):
    """Счёт не оплачен за 24ч: expired, работа и сеансы cancelled, обе стороны уведомлены."""
    await make_master_with(monkeypatch, db_session, business, 70001, "Мастер А")
    monkeypatch.setattr(settings, "apipay_api_key", "test-key")
    monkeypatch.setattr(providers, "_APIPAY", mock_apipay())
    work = await create_work(api_client, client_row, price=100000)
    offer = await pending_offer(api_client, work["id"])
    await accept(api_client, offer["id"])

    resp = await api_client.get("/api/payments?limit=10", headers=OWNER_HEADERS)
    payment = next(p for p in resp.json()["payments"] if p["work_id"] == work["id"])
    assert payment["provider"] == "apipay"

    # Дедлайн наступил
    from tg_studio.db.models import Payment

    pay_row = await db_session.get(Payment, payment["id"])
    pay_row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await db_session.commit()

    count = await payments_service.expire_unpaid_prepay(db_session)
    assert count == 1
    await db_session.refresh(pay_row)
    assert pay_row.status == "expired"

    resp = await api_client.get(f"/api/tattoo/works/{work['id']}", headers=OWNER_HEADERS)
    data = resp.json()
    assert data["status"] == "cancelled"
    assert all(s["status"] == "cancelled" for s in data["sessions"])

    # Уведомления: клиенту (telegram_id из фикстуры) и владельцу
    sent = no_telegram
    assert len(sent) == 2
    assert any(text for _, text in sent if "отменена" in text)


@pytest.mark.asyncio
async def test_paid_and_manual_prepay_survive_expiry(
    api_client, monkeypatch, db_session, business, client_row, no_telegram
):
    """Оплаченный счёт и manual-предоплата не сгорают."""
    await make_master_with(monkeypatch, db_session, business, 70001, "Мастер А")

    # manual-предоплата с прошедшим дедлайном — не трогается
    work = await create_work(api_client, client_row, price=100000)
    offer = await pending_offer(api_client, work["id"])
    await accept(api_client, offer["id"], headers={"X-Debug-User-Id": "70001"})

    from tg_studio.db.models import Payment

    resp = await api_client.get("/api/payments?limit=10", headers=OWNER_HEADERS)
    payment = next(p for p in resp.json()["payments"] if p["work_id"] == work["id"])
    pay_row = await db_session.get(Payment, payment["id"])
    pay_row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await db_session.commit()

    assert await payments_service.expire_unpaid_prepay(db_session) == 0
    await db_session.refresh(pay_row)
    assert pay_row.status == "pending"
    resp = await api_client.get(f"/api/tattoo/works/{work['id']}", headers=OWNER_HEADERS)
    assert resp.json()["status"] == "in_progress"

    # Оплаченная предоплата (manual → paid) тоже не сгорает
    await api_client.post(f"/api/payments/{payment['id']}/mark-paid", headers=OWNER_HEADERS)
    assert await payments_service.expire_unpaid_prepay(db_session) == 0
    resp = await api_client.get(f"/api/tattoo/works/{work['id']}", headers=OWNER_HEADERS)
    assert resp.json()["status"] == "in_progress"
