"""Интеграционные тесты ручных клиентов: дедуп, E.164, правка, работы."""

import pytest

from ..conftest import make_client

OWNER_HEADERS = {"X-Debug-User-Id": "99999"}
MASTER_HEADERS = {"X-Debug-User-Id": "88888"}


@pytest.fixture
async def business(db_session):
    from ..conftest import make_business

    return await make_business(db_session, owner_telegram_id=99999)


@pytest.fixture
async def master(db_session, business, monkeypatch):
    from tg_studio.config import AllowedUser, settings
    from tg_studio.db.models import UserRole

    from ..conftest import make_master, make_user

    user = await make_user(
        db_session, telegram_id=88888, first_name="Мастер Тест", role=UserRole.master
    )
    m = await make_master(db_session, business_id=business.id, full_name="Мастер Тест")
    m.user_id = user.id
    await db_session.commit()
    monkeypatch.setattr(
        settings,
        "allowed_users",
        [*settings.allowed_users, AllowedUser(id=88888, role="master")],
    )
    return m


def client_payload(**overrides):
    body = {"full_name": "Айгуль", "phone": "+77011234567"}
    body.update(overrides)
    return {k: v for k, v in body.items() if v is not None or k in ("full_name",)}


@pytest.mark.asyncio
async def test_create_manual_client(api_client, db_session, business):
    resp = await api_client.post(
        "/api/tattoo/clients",
        json={"full_name": "Айгуль", "phone": "8 (701) 123-45-67", "note": "пришла от Аиды"},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    # Телефон нормализован в E.164
    assert body["phone"] == "+77011234567"
    assert body["note"] == "пришла от Аиды"


@pytest.mark.asyncio
async def test_create_requires_name(api_client, db_session, business):
    resp = await api_client.post("/api/tattoo/clients", json={"phone": "+77011234567"}, headers=OWNER_HEADERS)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_dedupe_by_phone_enriches(api_client, db_session, business):
    resp1 = await api_client.post(
        "/api/tattoo/clients", json=client_payload(), headers=OWNER_HEADERS
    )
    assert resp1.status_code == 201
    first = resp1.json()

    # Тот же номер в другом формате + инста → существующий клиент обогащён
    resp2 = await api_client.post(
        "/api/tattoo/clients",
        json={"full_name": "Айгуль Т.", "phone": "87011234567", "instagram_username": "@aigul.tattoo"},
        headers=OWNER_HEADERS,
    )
    assert resp2.status_code == 200
    second = resp2.json()
    assert second["id"] == first["id"]
    assert second["phone"] == "+77011234567"
    assert second["instagram_username"] == "@aigul.tattoo" or second["instagram_username"] == "aigul.tattoo"
    # Имя существующего не перезаписали
    assert second["full_name"] == "Айгуль"


@pytest.mark.asyncio
async def test_dedupe_by_instagram(api_client, db_session, business):
    resp1 = await api_client.post(
        "/api/tattoo/clients",
        json={"full_name": "Дана", "instagram_username": "Dana_KZ"},
        headers=OWNER_HEADERS,
    )
    assert resp1.status_code == 201
    assert resp1.json()["instagram_username"] == "dana_kz"  # lowercase

    resp2 = await api_client.post(
        "/api/tattoo/clients",
        json={"full_name": "Дана2", "instagram_username": "@dana_kz", "phone": "+77079998877"},
        headers=OWNER_HEADERS,
    )
    assert resp2.status_code == 200
    body = resp2.json()
    assert body["id"] == resp1.json()["id"]
    assert body["phone"] == "+77079998877"  # дозаполнили пустой телефон


@pytest.mark.asyncio
async def test_no_identifiers_creates_new(api_client, db_session, business):
    """Клиент с улицы: только имя — создаётся, дедупить не по чему."""
    resp = await api_client.post(
        "/api/tattoo/clients", json={"full_name": "Без контактов"}, headers=OWNER_HEADERS
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["phone"] is None

    # Второй такой же — тоже новый (не дедуп)
    resp2 = await api_client.post(
        "/api/tattoo/clients", json={"full_name": "Без контактов"}, headers=OWNER_HEADERS
    )
    assert resp2.status_code == 201
    assert resp2.json()["id"] != body["id"]


@pytest.mark.asyncio
async def test_patch_client_and_conflict(api_client, db_session, business):
    resp = await api_client.post(
        "/api/tattoo/clients", json=client_payload(), headers=OWNER_HEADERS
    )
    cid = resp.json()["id"]

    resp = await api_client.patch(
        f"/api/tattoo/clients/{cid}",
        json={"note": "цвет волос — блонд"},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["note"] == "цвет волос — блонд"

    # Второй клиент пытается занять телефон первого → 409
    other = await api_client.post(
        "/api/tattoo/clients", json={"full_name": "Другой"}, headers=OWNER_HEADERS
    )
    resp = await api_client.patch(
        f"/api/tattoo/clients/{other.json()['id']}",
        json={"phone": "+77011234567"},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_manual_client_used_in_work(api_client, db_session, business, master):
    """Работа на ручного клиента (без User/telegram_id) создаётся штатно."""
    resp = await api_client.post(
        "/api/tattoo/clients", json=client_payload(), headers=OWNER_HEADERS
    )
    cid = resp.json()["id"]
    # Клиент без telegram_id и без User
    from sqlalchemy import select

    from tg_studio.db.models import Client

    result = await db_session.execute(select(Client).where(Client.id == cid))
    db_client = result.scalar_one()
    assert db_client.telegram_id is None
    assert db_client.user_id is None
    await db_session.commit()

    from .test_tattoo_api import work_payload

    # Владелец создаёт работу на своего мастера
    resp = await api_client.post(
        "/api/tattoo/works", json=work_payload(cid, master.id), headers=OWNER_HEADERS
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["client_id"] == cid


@pytest.mark.asyncio
async def test_existing_tg_client_matched_by_phone(api_client, db_session, business):
    """tg-клиент с телефоном: ручное создание с тем же телефоном находит его."""

    tg_client = await make_client(db_session, telegram_id=321, full_name="Из Телеграма")
    tg_client.phone = "+77011234567"
    await db_session.commit()

    resp = await api_client.post(
        "/api/tattoo/clients",
        json={"full_name": "Айгуль", "phone": "8 701 123-45-67"},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == tg_client.id
    assert resp.json()["full_name"] == "Из Телеграма"