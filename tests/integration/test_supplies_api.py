"""Интеграционные тесты /api/supplies — склад расходников.

Роли: мастер видит остатки и только списывает; владелец — CRUD, пополнение,
ревизия, журнал. Списание в минус запрещено; переход порога уведомляет
владельца в Telegram (monkeypatch на I/O).
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
    """Мастер с привязанным User — чтобы ходить под его debug-заголовком."""
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


def supply_payload(**overrides):
    body = {
        "name": "Чёрная краска Intenze 30ml",
        "category": "Краска",
        "unit": "мл",
        "quantity": 10,
        "min_quantity": 2,
    }
    body.update(overrides)
    return body


@pytest.mark.asyncio
async def test_create_requires_owner(api_client, business, master):
    resp = await api_client.post("/api/supplies", json=supply_payload(), headers=MASTER_HEADERS)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_create_and_init_movement(api_client, business):
    resp = await api_client.post("/api/supplies", json=supply_payload(), headers=OWNER_HEADERS)
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["quantity"] == 10
    assert data["low"] is False

    movements = (
        await api_client.get(
            f"/api/supplies/movements?supply_id={data['id']}", headers=OWNER_HEADERS
        )
    ).json()["movements"]
    assert len(movements) == 1
    assert movements[0]["kind"] == "init"
    assert movements[0]["delta"] == 10
    assert movements[0]["supply_name"] == "Чёрная краска Intenze 30ml"


@pytest.mark.asyncio
async def test_master_sees_stock_but_cannot_modify(api_client, business, master):
    created = await api_client.post(
        "/api/supplies", json=supply_payload(), headers=OWNER_HEADERS
    )
    sid = created.json()["id"]

    listed = await api_client.get("/api/supplies", headers=MASTER_HEADERS)
    assert listed.status_code == 200
    assert [s["id"] for s in listed.json()["supplies"]] == [sid]

    patch = await api_client.patch(
        f"/api/supplies/{sid}", json={"name": "хак"}, headers=MASTER_HEADERS
    )
    assert patch.status_code == 403

    restock = await api_client.post(
        f"/api/supplies/{sid}/restock", json={"amount": 5}, headers=MASTER_HEADERS
    )
    assert restock.status_code == 403


@pytest.mark.asyncio
async def test_use_by_master_records_movement(api_client, business, master):
    created = await api_client.post(
        "/api/supplies", json=supply_payload(quantity=5, min_quantity=None), headers=OWNER_HEADERS
    )
    sid = created.json()["id"]

    resp = await api_client.post(
        f"/api/supplies/{sid}/use",
        json={"amount": 1.5, "note": " сеанс #8"},
        headers=MASTER_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["quantity"] == 3.5

    movements = (
        await api_client.get(
            f"/api/supplies/movements?supply_id={sid}", headers=OWNER_HEADERS
        )
    ).json()["movements"]
    use_rows = [m for m in movements if m["kind"] == "use"]
    assert len(use_rows) == 1
    assert use_rows[0]["delta"] == -1.5
    assert use_rows[0]["master_id"] == master.id
    assert use_rows[0]["master_name"] == "Мастер Тест"


@pytest.mark.asyncio
async def test_use_cannot_go_negative(api_client, business, master):
    created = await api_client.post(
        "/api/supplies", json=supply_payload(quantity=2, min_quantity=None), headers=OWNER_HEADERS
    )
    sid = created.json()["id"]
    resp = await api_client.post(
        f"/api/supplies/{sid}/use", json={"amount": 5}, headers=MASTER_HEADERS
    )
    assert resp.status_code == 400
    assert "только 2" in resp.json()["detail"]
    # остаток не изменился
    listed = await api_client.get("/api/supplies", headers=OWNER_HEADERS)
    assert listed.json()["supplies"][0]["quantity"] == 2


@pytest.mark.asyncio
async def test_low_stock_notify_once_on_crossing(api_client, business, master, monkeypatch):
    sent = []

    async def fake_send(tg_id, text):
        sent.append((tg_id, text))
        return 1

    async def fake_owner(session):
        return 555000

    monkeypatch.setattr(
        "tg_studio.modules.supplies.api.send_text_to_telegram", fake_send
    )
    monkeypatch.setattr(
        "tg_studio.modules.supplies.api.resolve_owner_telegram_id", fake_owner
    )

    created = await api_client.post(
        "/api/supplies", json=supply_payload(quantity=2, min_quantity=1), headers=OWNER_HEADERS
    )
    sid = created.json()["id"]

    # переход через порог — уведомление
    resp = await api_client.post(
        f"/api/supplies/{sid}/use", json={"amount": 1.5}, headers=MASTER_HEADERS
    )
    assert resp.status_code == 200
    assert resp.json()["low"] is True
    assert len(sent) == 1
    tg_id, text = sent[0]
    assert tg_id == 555000
    assert "Краска" in text or "краска" in text

    # уже ниже порога — повторных уведомлений нет
    await api_client.post(f"/api/supplies/{sid}/use", json={"amount": 0.3}, headers=MASTER_HEADERS)
    assert len(sent) == 1

    # пополнение выше порога → следующее списание через порог снова уведомит
    await api_client.post(
        f"/api/supplies/{sid}/restock", json={"amount": 2}, headers=OWNER_HEADERS
    )
    await api_client.post(f"/api/supplies/{sid}/use", json={"amount": 2}, headers=MASTER_HEADERS)
    assert len(sent) == 2


@pytest.mark.asyncio
async def test_restock_and_adjust(api_client, business):
    created = await api_client.post(
        "/api/supplies", json=supply_payload(quantity=10, min_quantity=2), headers=OWNER_HEADERS
    )
    sid = created.json()["id"]

    restock = await api_client.post(
        f"/api/supplies/{sid}/restock",
        json={"amount": 5, "note": "занесли из Waso"},
        headers=OWNER_HEADERS,
    )
    assert restock.status_code == 200
    assert restock.json()["quantity"] == 15

    adjust = await api_client.post(
        f"/api/supplies/{sid}/adjust",
        json={"new_quantity": 12, "note": "ревизия"},
        headers=OWNER_HEADERS,
    )
    assert adjust.status_code == 200
    assert adjust.json()["quantity"] == 12

    same = await api_client.post(
        f"/api/supplies/{sid}/adjust", json={"new_quantity": 12}, headers=OWNER_HEADERS
    )
    assert same.status_code == 422

    movements = (
        await api_client.get(f"/api/supplies/movements?supply_id={sid}", headers=OWNER_HEADERS)
    ).json()["movements"]
    kinds = [m["kind"] for m in movements]
    assert kinds == ["adjust", "purchase", "init"]
    assert movements[0]["delta"] == -3  # 12 - 15


@pytest.mark.asyncio
async def test_use_linked_to_work_and_session(api_client, business, master, db_session):
    client = await make_client(db_session)
    work_payload = {
        "client_id": client.id,
        "master_id": master.id,
        "size_length_cm": 10,
        "size_height_cm": 5,
        "complexity": "средняя",
        "style": "Лайнворк (Linework)",
        "placement": "Предплечье (внутренняя / внешняя сторона)",
        "first_session": {
            "session_date": (
                datetime.now(UTC) + timedelta(days=30)
            ).replace(microsecond=0).isoformat()
        },
    }
    work = await api_client.post("/api/tattoo/works", json=work_payload, headers=OWNER_HEADERS)
    assert work.status_code == 201, work.text
    work_id = work.json()["id"]
    session_id = work.json()["sessions"][0]["id"]

    supply = await api_client.post(
        "/api/supplies",
        json=supply_payload(name="Картридж Magnum 9RS", category="Иглы и картриджи",
                            unit="шт", quantity=20, min_quantity=None),
        headers=OWNER_HEADERS,
    )
    sid = supply.json()["id"]

    resp = await api_client.post(
        f"/api/supplies/{sid}/use",
        json={"amount": 3, "work_id": work_id, "session_id": session_id},
        headers=MASTER_HEADERS,
    )
    assert resp.status_code == 200

    movements = (
        await api_client.get(f"/api/supplies/movements?supply_id={sid}", headers=OWNER_HEADERS)
    ).json()["movements"]
    assert movements[0]["work_id"] == work_id
    assert movements[0]["session_id"] == session_id

    # чужой work_id → 404
    bad = await api_client.post(
        f"/api/supplies/{sid}/use", json={"amount": 1, "work_id": 424242}, headers=MASTER_HEADERS
    )
    assert bad.status_code == 404


@pytest.mark.asyncio
async def test_patch_and_archive(api_client, business):
    created = await api_client.post(
        "/api/supplies", json=supply_payload(), headers=OWNER_HEADERS
    )
    sid = created.json()["id"]

    patch = await api_client.patch(
        f"/api/supplies/{sid}",
        json={"min_quantity": 3, "name": "Чёрная краска 15ml", "note": None},
        headers=OWNER_HEADERS,
    )
    assert patch.status_code == 200
    assert patch.json()["name"] == "Чёрная краска 15ml"
    assert patch.json()["min_quantity"] == 3

    # PATCH не должен трогать quantity: журнал не должен расходиться с остатком
    assert patch.json()["quantity"] == 10

    deleted = await api_client.delete(f"/api/supplies/{sid}", headers=OWNER_HEADERS)
    assert deleted.status_code == 204

    # из обычного списка пропала, в архиве видна, списывать нельзя
    listed = await api_client.get("/api/supplies", headers=OWNER_HEADERS)
    assert listed.json()["supplies"] == []
    archived = await api_client.get(
        "/api/supplies?include_inactive=true", headers=OWNER_HEADERS
    )
    assert [s["id"] for s in archived.json()["supplies"]] == [sid]

    use = await api_client.post(
        f"/api/supplies/{sid}/use", json={"amount": 1}, headers=OWNER_HEADERS
    )
    assert use.status_code == 409

    restore = await api_client.patch(
        f"/api/supplies/{sid}", json={"is_active": True}, headers=OWNER_HEADERS
    )
    assert restore.status_code == 200
    assert restore.json()["is_active"] is True
