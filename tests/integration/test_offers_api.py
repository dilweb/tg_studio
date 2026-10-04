"""Интеграционные тесты распределения записей: очередь офферов.

Никакого «кто первый успел»: pending только у одного оффера, принять может
лишь тот, кому предложено. Отказ/таймаут → следующий; все отказались →
эскалация владельцу. Telegram-уведомления подменяются фейками.
"""

from datetime import UTC, datetime, timedelta

import pytest

from tg_studio.config import AllowedUser, settings
from tg_studio.db.models import (
    TattooProjectStatus,
    TattooSession,
    TattooWork,
    UserRole,
)
from tg_studio.modules.tattoo import offers

from ..conftest import make_business, make_client, make_master, make_user

OWNER_HEADERS = {"X-Debug-User-Id": "99999"}
STYLE = "Лайнворк (Linework)"
OTHER_STYLE = "Реализм (Realism)"


@pytest.fixture
async def business(db_session):
    return await make_business(db_session, owner_telegram_id=99999)


@pytest.fixture
async def client_row(db_session):
    return await make_client(db_session, full_name="Дилявер Тестов")


@pytest.fixture
def no_telegram(monkeypatch):
    """Молчаливые уведомления: фейки вместо реальных отправок."""
    notified, escalated = [], []

    async def fake_notify(session, offer):
        notified.append(offer)

    async def fake_escalate(session, work):
        escalated.append(work)

    monkeypatch.setattr(offers, "notify_master", fake_notify)
    monkeypatch.setattr(offers, "escalate_to_owner", fake_escalate)
    return notified, escalated


async def make_master_with(monkeypatch, db_session, business, tg_id, name, styles=None):
    """Мастер с user-аккаунтом (для заголовков) и специализациями."""
    user = await make_user(db_session, telegram_id=tg_id, first_name=name, role=UserRole.master)
    m = await make_master(db_session, business_id=business.id, full_name=name)
    m.user_id = user.id
    m.specializations = styles or []
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


async def create_work(api_client, client_row, **overrides) -> dict:
    """Работа (по умолчанию без мастера — уходит в очередь офферов)."""
    body = {
        "client_id": client_row.id,
        "size_length_cm": 15,
        "size_height_cm": 10,
        "complexity": "средняя",
        "style": STYLE,
        "placement": "Предплечье (внутренняя / внешняя сторона)",
        "first_session": {
            "session_date": (datetime.now(UTC) + timedelta(days=7))
            .replace(microsecond=0)
            .isoformat(),
        },
    }
    body.update(overrides)
    resp = await api_client.post("/api/tattoo/works", json=body, headers=OWNER_HEADERS)
    assert resp.status_code == 201, resp.text
    return resp.json()


def work_offers(all_offers: list[dict], work_id: int) -> list[dict]:
    return sorted((o for o in all_offers if o["work_id"] == work_id), key=lambda o: o["rank"])


async def list_offers(api_client, headers=OWNER_HEADERS) -> list[dict]:
    resp = await api_client.get("/api/tattoo/offers", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["offers"]


@pytest.mark.asyncio
async def test_queue_ranking(
    api_client, monkeypatch, db_session, business, client_row, no_telegram
):
    """Очередь детерминированная: меньше загрузки за месяц — выше."""
    master_a = await make_master_with(monkeypatch, db_session, business, 70001, "Мастер А")
    master_b = await make_master_with(monkeypatch, db_session, business, 70002, "Мастер Б")

    # Работа с мастером А в этом же месяце: +1 час загрузки
    busy_start = (datetime.now(UTC) + timedelta(days=3)).replace(microsecond=0).isoformat()
    await create_work(
        api_client,
        client_row,
        master_id=master_a.id,
        force=True,
        first_session={"session_date": busy_start},
    )

    work = await create_work(api_client, client_row)
    queue = work_offers(await list_offers(api_client), work["id"])
    assert [o["master_id"] for o in queue] == [master_b.id, master_a.id]
    assert queue[0]["status"] == "pending"
    assert queue[1]["status"] == "queued"
    assert queue[0]["master_name"] == "Мастер Б"
    assert work["master_id"] is None

    notified, _ = no_telegram
    assert len(notified) == 1 and notified[0].master_id == master_b.id


@pytest.mark.asyncio
async def test_specialization_filters_pool(
    api_client, monkeypatch, db_session, business, client_row, no_telegram
):
    """Мастер не по стилю не попадает в очередь, даже если он свободнее."""
    master_a = await make_master_with(
        monkeypatch, db_session, business, 70001, "Мастер А", styles=[STYLE]
    )
    await make_master_with(
        monkeypatch, db_session, business, 70002, "Мастер Б", styles=[OTHER_STYLE]
    )
    work = await create_work(api_client, client_row)
    queue = work_offers(await list_offers(api_client), work["id"])
    assert [o["master_id"] for o in queue] == [master_a.id]


@pytest.mark.asyncio
async def test_decline_advances_queue_then_escalates(
    api_client, monkeypatch, db_session, business, client_row, no_telegram
):
    """Отказ → следующий; все отказались → эскалация владельцу."""
    await make_master_with(monkeypatch, db_session, business, 70001, "Мастер А")
    await make_master_with(monkeypatch, db_session, business, 70002, "Мастер Б")
    work = await create_work(api_client, client_row)
    queue = work_offers(await list_offers(api_client), work["id"])

    resp = await api_client.post(
        f"/api/tattoo/offers/{queue[0]['id']}/decline", headers={"X-Debug-User-Id": "70001"}
    )
    assert resp.status_code == 200, resp.text
    after = work_offers(await list_offers(api_client), work["id"])
    assert [o["status"] for o in after] == ["declined", "pending"]

    resp = await api_client.post(
        f"/api/tattoo/offers/{after[1]['id']}/decline", headers={"X-Debug-User-Id": "70002"}
    )
    assert resp.status_code == 200, resp.text
    _, escalated = no_telegram
    assert [w.id for w in escalated] == [work["id"]]
    resp = await api_client.get(f"/api/tattoo/works/{work['id']}", headers=OWNER_HEADERS)
    assert resp.json()["master_id"] is None


@pytest.mark.asyncio
async def test_accept_assigns_master_and_closes_queue(
    api_client, monkeypatch, db_session, business, client_row, no_telegram
):
    """Принять может только тот, кому предложено; остальные офферы закрываются."""
    master_a = await make_master_with(monkeypatch, db_session, business, 70001, "Мастер А")
    await make_master_with(monkeypatch, db_session, business, 70002, "Мастер Б")
    work = await create_work(api_client, client_row)
    queue = work_offers(await list_offers(api_client), work["id"])

    # Б не может принять оффер, предложенный А
    resp = await api_client.post(
        f"/api/tattoo/offers/{queue[0]['id']}/accept", headers={"X-Debug-User-Id": "70002"}
    )
    assert resp.status_code == 404

    resp = await api_client.post(
        f"/api/tattoo/offers/{queue[0]['id']}/accept", headers={"X-Debug-User-Id": "70001"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "accepted"
    assert resp.json()["master_id"] == master_a.id

    resp = await api_client.get(f"/api/tattoo/works/{work['id']}", headers=OWNER_HEADERS)
    assert resp.json()["master_id"] == master_a.id

    statuses = {o["id"]: o["status"] for o in await list_offers(api_client)}
    assert statuses[queue[1]["id"]] == "closed"


@pytest.mark.asyncio
async def test_accept_slot_conflict(
    api_client, monkeypatch, db_session, business, client_row, no_telegram
):
    """У мастера в это время уже сеанс → 409; владелец может назначить силой."""
    master_a = await make_master_with(monkeypatch, db_session, business, 70001, "Мастер А")
    # Б не в пуле этого стиля — очередь только из А
    await make_master_with(
        monkeypatch, db_session, business, 70002, "Мастер Б", styles=[OTHER_STYLE]
    )
    same_time = (datetime.now(UTC) + timedelta(days=7)).replace(microsecond=0).isoformat()
    busy = await create_work(
        api_client,
        client_row,
        master_id=master_a.id,
        force=True,
        first_session={"session_date": same_time},
    )
    assert busy["master_id"] == master_a.id

    offered = await create_work(api_client, client_row, first_session={"session_date": same_time})
    offer = work_offers(await list_offers(api_client), offered["id"])[0]

    resp = await api_client.post(
        f"/api/tattoo/offers/{offer['id']}/accept", headers={"X-Debug-User-Id": "70001"}
    )
    assert resp.status_code == 409
    assert "занято" in resp.json()["detail"]
    resp = await api_client.get(f"/api/tattoo/works/{offered['id']}", headers=OWNER_HEADERS)
    assert resp.json()["master_id"] is None

    # Владелец разруливает сам
    resp = await api_client.post(
        f"/api/tattoo/offers/{offer['id']}/accept?force=true", headers=OWNER_HEADERS
    )
    assert resp.status_code == 200, resp.text
    resp = await api_client.get(f"/api/tattoo/works/{offered['id']}", headers=OWNER_HEADERS)
    assert resp.json()["master_id"] == master_a.id


@pytest.mark.asyncio
async def test_accept_twice_rejected(
    api_client, monkeypatch, db_session, business, client_row, no_telegram
):
    await make_master_with(monkeypatch, db_session, business, 70001, "Мастер А")
    work = await create_work(api_client, client_row)
    offer = work_offers(await list_offers(api_client), work["id"])[0]
    headers = {"X-Debug-User-Id": "70001"}
    resp = await api_client.post(f"/api/tattoo/offers/{offer['id']}/accept", headers=headers)
    assert resp.status_code == 200
    resp = await api_client.post(f"/api/tattoo/offers/{offer['id']}/accept", headers=headers)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_owner_accepts_after_refusals(
    api_client, monkeypatch, db_session, business, client_row, no_telegram
):
    """Эскалация решается владельцем: accept чужого оффера = ручное назначение."""
    master_a = await make_master_with(monkeypatch, db_session, business, 70001, "Мастер А")
    work = await create_work(api_client, client_row)
    offer = work_offers(await list_offers(api_client), work["id"])[0]
    resp = await api_client.post(f"/api/tattoo/offers/{offer['id']}/accept", headers=OWNER_HEADERS)
    assert resp.status_code == 200, resp.text
    resp = await api_client.get(f"/api/tattoo/works/{work['id']}", headers=OWNER_HEADERS)
    assert resp.json()["master_id"] == master_a.id


@pytest.mark.asyncio
async def test_expire_stale_offers(monkeypatch, db_session, business, client_row, no_telegram):
    """Таймаут: pending сгорает → следующий pending; очередь пуста → эскалация."""
    master_a = await make_master_with(monkeypatch, db_session, business, 70001, "Мастер А")
    master_b = await make_master_with(monkeypatch, db_session, business, 70002, "Мастер Б")

    work = TattooWork(
        client_id=client_row.id,
        master_id=None,
        business_id=business.id,
        size_length_cm=10,
        size_height_cm=10,
        complexity="средняя",
        style=STYLE,
        placement="Спина",
    )
    db_session.add(work)
    await db_session.flush()
    start = datetime.now(UTC) + timedelta(days=3)
    db_session.add(
        TattooSession(work_id=work.id, session_date=start, status=TattooProjectStatus.planned.value)
    )
    await db_session.flush()
    created = await offers.create_offers(db_session, work, start)
    await db_session.commit()
    assert [o.master_id for o in created] == [master_a.id, master_b.id]

    created[0].expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await db_session.commit()
    count = await offers.expire_stale_offers(db_session)
    assert count == 1
    await db_session.refresh(created[0])
    await db_session.refresh(created[1])
    assert created[0].status == "expired"
    assert created[1].status == "pending"

    notified, _ = no_telegram
    assert len(notified) == 1 and notified[0].master_id == master_b.id

    # Очередь закончилась: сгорел последний → эскалация владельцу
    await db_session.refresh(created[1])
    created[1].expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await db_session.commit()
    await offers.expire_stale_offers(db_session)
    _, escalated = no_telegram
    assert [w.id for w in escalated] == [work.id]
