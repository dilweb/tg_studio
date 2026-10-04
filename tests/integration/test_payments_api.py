"""Интеграционные тесты /api/payments — баланс работы и машина состояний.

Обе роли выставляют счёты (manual) и подтверждают оплату. Баланс вычисляется
как contract_price − Σ paid-платежей; работу нельзя закрыть с непогашенным
остатком. apipay-адаптер здесь только отрицается (ключ не задан) — его
вебхук тестируется отдельно.
"""

from datetime import UTC, datetime, timedelta

import pytest

from tg_studio.config import AllowedUser, settings
from tg_studio.db.models import UserRole

from ..conftest import make_business, make_client, make_master, make_user

OWNER_HEADERS = {"X-Debug-User-Id": "99999"}
MASTER_TG_ID = 88888
MASTER_HEADERS = {"X-Debug-User-Id": str(MASTER_TG_ID)}


@pytest.fixture
async def business(db_session):
    return await make_business(db_session, owner_telegram_id=99999)


@pytest.fixture
async def master(db_session, business, monkeypatch):
    user = await make_user(
        db_session, telegram_id=MASTER_TG_ID, first_name="Мастер Тест", role=UserRole.master
    )
    m = await make_master(db_session, business_id=business.id, full_name="Мастер Тест")
    m.user_id = user.id
    await db_session.commit()
    monkeypatch.setattr(
        settings,
        "allowed_users",
        [*settings.allowed_users, AllowedUser(id=MASTER_TG_ID, role="master")],
    )
    return m


@pytest.fixture
async def client_row(db_session, business):
    return await make_client(db_session, full_name="Дилявер Тестов")


async def create_work(api_client, business, master, client_row, **overrides) -> dict:
    body = {
        "client_id": client_row.id,
        "master_id": master.id,
        "size_length_cm": 15,
        "size_height_cm": 10,
        "complexity": "средняя",
        "style": "Лайнворк (Linework)",
        "placement": "Предплечье (внутренняя / внешняя сторона)",
        "first_session": {
            "session_date": (datetime.now(UTC) + timedelta(days=1))
            .replace(microsecond=0)
            .isoformat(),
        },
    }
    body.update(overrides)
    resp = await api_client.post(
        "/api/tattoo/works?force=true", json=body, headers=OWNER_HEADERS
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def balance(api_client, work_id: int, headers=OWNER_HEADERS) -> dict:
    resp = await api_client.get(
        f"/api/payments/works/{work_id}/balance", headers=headers
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_no_contract_no_balance(api_client, business, master, client_row):
    """Без contract_price баланс None: старые работы ведут себя как раньше."""
    work = await create_work(api_client, business, master, client_row)
    data = await balance(api_client, work["id"])
    assert data["contract_price"] is None
    assert data["balance"] is None
    assert data["paid_total"] == 0


@pytest.mark.asyncio
async def test_first_payment_fixes_contract_price(api_client, business, master, client_row):
    """Первый счёт фиксирует договорную цену; kind авто: предоплата 30к < 100к → partial."""
    work = await create_work(api_client, business, master, client_row)
    resp = await api_client.post(
        "/api/payments",
        json={
            "work_id": work["id"],
            "amount": 30000,
            "contract_price": 100000,
            "paid_now": True,
        },
        headers=MASTER_HEADERS,
    )
    assert resp.status_code == 201, resp.text
    p = resp.json()
    assert p["status"] == "paid"
    assert p["kind"] == "partial"  # 30к < 100к
    assert p["provider"] == "manual"
    assert p["master_name"] == "Мастер Тест"
    assert p["client_name"] == "Дилявер Тестов"
    assert p["paid_at"] is not None

    data = await balance(api_client, work["id"])
    assert data["contract_price"] == 100000
    assert data["paid_total"] == 30000
    assert data["balance"] == 70000


@pytest.mark.asyncio
async def test_final_payment_closes_balance(api_client, business, master, client_row):
    """Финальная доплата закрывает баланс; kind авто: 70к ≥ 70к → final."""
    work = await create_work(api_client, business, master, client_row)
    await api_client.post(
        "/api/payments",
        json={"work_id": work["id"], "amount": 30000, "contract_price": 100000, "paid_now": True},
        headers=OWNER_HEADERS,
    )
    resp = await api_client.post(
        "/api/payments",
        json={"work_id": work["id"], "amount": 70000},
        headers=MASTER_HEADERS,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["kind"] == "final"
    assert resp.json()["status"] == "pending"

    # Оплачиваем через отдельный эндпоинт (счёт сначала pending)
    payment_id = resp.json()["id"]
    resp = await api_client.post(f"/api/payments/{payment_id}/mark-paid", headers=MASTER_HEADERS)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "paid"

    data = await balance(api_client, work["id"], headers=MASTER_HEADERS)
    assert data["balance"] == 0


@pytest.mark.asyncio
async def test_contract_price_revision_covers_agreement(
    api_client, business, master, client_row
):
    """Кейс «половина за 50к»: цена пересмотрена 100к → 50к, доплата 20к закрывает."""
    work = await create_work(api_client, business, master, client_row)
    await api_client.post(
        "/api/payments",
        json={"work_id": work["id"], "amount": 30000, "contract_price": 100000, "paid_now": True},
        headers=OWNER_HEADERS,
    )
    resp = await api_client.post(
        "/api/payments",
        json={"work_id": work["id"], "amount": 20000, "contract_price": 50000, "paid_now": True},
        headers=MASTER_HEADERS,
    )
    assert resp.status_code == 201, resp.text
    data = await balance(api_client, work["id"])
    assert data["contract_price"] == 50000
    assert data["paid_total"] == 50000
    assert data["balance"] == 0


@pytest.mark.asyncio
async def test_completion_blocked_with_outstanding(api_client, business, master, client_row):
    """Работу нельзя закрывать, пока остаток не оплачен: PATCH работы и сеанса → 409."""
    work = await create_work(api_client, business, master, client_row)
    await api_client.post(
        "/api/payments",
        json={"work_id": work["id"], "amount": 30000, "contract_price": 100000, "paid_now": True},
        headers=OWNER_HEADERS,
    )

    resp = await api_client.patch(
        f"/api/tattoo/works/{work['id']}", json={"status": "completed"}, headers=OWNER_HEADERS
    )
    assert resp.status_code == 409
    assert "не оплачен" in resp.json()["detail"]

    session_id = work["sessions"][0]["id"]
    resp = await api_client.patch(
        f"/api/tattoo/works/{work['id']}/sessions/{session_id}",
        json={"is_final_session": True},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_completion_allowed_after_paid(api_client, business, master, client_row):
    """Баланс закрыт → и работа, и сеанс завершаются без помех."""
    work = await create_work(api_client, business, master, client_row)
    await api_client.post(
        "/api/payments",
        json={"work_id": work["id"], "amount": 100000, "contract_price": 100000, "paid_now": True},
        headers=OWNER_HEADERS,
    )
    resp = await api_client.patch(
        f"/api/tattoo/works/{work['id']}", json={"status": "completed"}, headers=OWNER_HEADERS
    )
    assert resp.status_code == 200, resp.text

    session_id = work["sessions"][0]["id"]
    resp = await api_client.patch(
        f"/api/tattoo/works/{work['id']}/sessions/{session_id}",
        json={"status": "completed"},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "completed"


@pytest.mark.asyncio
async def test_mark_paid_only_pending_and_manual(api_client, business, master, client_row):
    """Повторное подтверждение → 409; paid_now с apipay → 422."""
    work = await create_work(api_client, business, master, client_row)
    resp = await api_client.post(
        "/api/payments",
        json={"work_id": work["id"], "amount": 5000, "contract_price": 5000, "paid_now": True},
        headers=MASTER_HEADERS,
    )
    payment_id = resp.json()["id"]
    resp = await api_client.post(f"/api/payments/{payment_id}/mark-paid", headers=OWNER_HEADERS)
    assert resp.status_code == 409

    resp = await api_client.post(
        "/api/payments",
        json={"work_id": work["id"], "amount": 1000, "provider": "apipay", "paid_now": True},
        headers=MASTER_HEADERS,
    )
    assert resp.status_code == 409, "ключ apipay не задан → счёт не создать"
    assert "не подключён" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_cancel_pending_with_note(api_client, business, master, client_row):
    work = await create_work(api_client, business, master, client_row)
    resp = await api_client.post(
        "/api/payments",
        json={"work_id": work["id"], "amount": 5000, "contract_price": 5000},
        headers=MASTER_HEADERS,
    )
    payment_id = resp.json()["id"]
    resp = await api_client.post(
        f"/api/payments/{payment_id}/cancel",
        json={"note": "клиент передумал"},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "cancelled"
    assert resp.json()["note"] == "клиент передумал"

    # Отменённый платёж не участвует в балансе
    data = await balance(api_client, work["id"])
    assert data["paid_total"] == 0
    assert data["balance"] == 5000


@pytest.mark.asyncio
async def test_master_scoped_to_own_works(db_session, api_client, business, master, client_row):
    """Мастер не видит платежи чужой работы: список отфильтрован, баланс чужой — 404."""
    other_master = await make_master(db_session, business_id=business.id, full_name="Другой Мастер")
    other_work = await create_work(api_client, business, other_master, client_row)
    await api_client.post(
        "/api/payments",
        json={"work_id": other_work["id"], "amount": 5000, "contract_price": 5000, "paid_now": True},
        headers=OWNER_HEADERS,
    )
    resp = await api_client.get("/api/payments", headers=MASTER_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["total"] == 0
    resp = await api_client.get(
        f"/api/payments/works/{other_work['id']}/balance", headers=MASTER_HEADERS
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_phone_normalization_inline(db_session, api_client, business, master):
    client_row = await make_client(db_session, full_name="С телефоном")
    client_row.phone = "+7 (701) 234-56-78"
    await db_session.commit()
    work = await create_work(api_client, business, master, client_row)
    data = await balance(api_client, work["id"])
    assert data["client_phone"] == "87012345678"

    # без телефона / мусор — None (счёт apipay такой клиент не получит)
    client_row2 = await make_client(db_session, telegram_id=43, full_name="Без телефона")
    work2 = await create_work(api_client, business, master, client_row2)
    data2 = await balance(api_client, work2["id"])
    assert data2["client_phone"] is None
