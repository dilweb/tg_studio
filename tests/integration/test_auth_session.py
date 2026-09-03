"""
Интеграционные тесты: login / refresh / logout с HttpOnly-cookies,
JSON-телом и заголовками ответа; /me по cookie и по Bearer.
"""

from unittest.mock import MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from tg_studio.api.app import app
from tg_studio.api.auth import hash_password
from tg_studio.config import settings
from tg_studio.db.models import User, UserRole


@pytest.fixture
def disable_debug_auth_stub(monkeypatch):
    """Иначе get_current_user в DEBUG вернёт первого юзера без проверки cookie."""
    monkeypatch.setattr(settings, "debug", False)


@pytest.fixture
def noop_verification_email_delay(monkeypatch):
    monkeypatch.setattr(
        "tg_studio.modules.identity.api.send_verification_email_task.delay",
        MagicMock(return_value=None),
    )


@pytest.mark.asyncio
async def test_login_returns_json_headers_and_cookies(
    api_client, db_session, disable_debug_auth_stub
):
    u = User(
        email="owner@example.com",
        password_hash=hash_password("pass12345"),
        first_name="O",
        role=UserRole.owner,
        is_email_verified=True,
    )
    db_session.add(u)
    await db_session.commit()

    r = await api_client.post(
        "/api/auth/login",
        json={"email": "owner@example.com", "password": "pass12345"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["user_id"] == u.id
    assert data["role"] == "owner"
    assert data["token_type"] == "bearer"
    assert data["access_token"]
    assert data["refresh_token"]
    auth_h = r.headers.get("authorization") or r.headers.get("Authorization")
    assert auth_h.startswith("Bearer ")
    assert auth_h.split(" ", 1)[1] == data["access_token"]
    refresh_h = r.headers.get("x-refresh-token") or r.headers.get("X-Refresh-Token")
    assert refresh_h == data["refresh_token"]
    assert "tg_access" in r.cookies
    assert "tg_refresh" in r.cookies
    assert r.cookies["tg_access"] == data["access_token"]


@pytest.mark.asyncio
async def test_me_with_cookie_only(api_client, db_session, disable_debug_auth_stub):
    u = User(
        email="cookie@example.com",
        password_hash=hash_password("secret"),
        first_name="C",
        role=UserRole.owner,
        is_email_verified=True,
    )
    db_session.add(u)
    await db_session.commit()

    login_r = await api_client.post(
        "/api/auth/login",
        json={"email": "cookie@example.com", "password": "secret"},
    )
    assert login_r.status_code == 200

    me_r = await api_client.get("/api/auth/me")
    assert me_r.status_code == 200
    assert me_r.json()["email"] == "cookie@example.com"
    assert me_r.json()["id"] == u.id


@pytest.mark.asyncio
async def test_me_bearer_overrides_cookie_when_both_sent(
    api_client, db_session, disable_debug_auth_stub
):
    owner = User(
        email="owner2@example.com",
        password_hash=hash_password("a"),
        first_name="O",
        role=UserRole.owner,
        is_email_verified=True,
    )
    master = User(
        email="master@example.com",
        password_hash=hash_password("b"),
        first_name="M",
        role=UserRole.master,
        is_email_verified=True,
    )
    db_session.add_all([owner, master])
    await db_session.commit()

    await api_client.post("/api/auth/login", json={"email": "owner2@example.com", "password": "a"})

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c2:
        login_m = await c2.post(
            "/api/auth/login",
            json={"email": "master@example.com", "password": "b"},
        )
        master_token = login_m.json()["access_token"]

    me_r = await api_client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {master_token}"},
    )
    assert me_r.status_code == 200
    assert me_r.json()["role"] == "master"
    assert me_r.json()["email"] == "master@example.com"


@pytest.mark.asyncio
async def test_refresh_with_empty_body_uses_refresh_cookie(
    api_client, db_session, disable_debug_auth_stub
):
    u = User(
        email="refresh@example.com",
        password_hash=hash_password("x"),
        first_name="R",
        role=UserRole.owner,
        is_email_verified=True,
    )
    db_session.add(u)
    await db_session.commit()

    await api_client.post(
        "/api/auth/login",
        json={"email": "refresh@example.com", "password": "x"},
    )

    ref_r = await api_client.post("/api/auth/refresh", json={})
    assert ref_r.status_code == 200
    body = ref_r.json()
    assert body["user_id"] == u.id
    assert body["access_token"]
    assert body["refresh_token"]
    assert api_client.cookies.get("tg_refresh") == body["refresh_token"]
    auth_h = ref_r.headers.get("authorization") or ref_r.headers.get("Authorization")
    assert auth_h == f"Bearer {body['access_token']}"


@pytest.mark.asyncio
async def test_refresh_accepts_refresh_token_in_body(
    api_client, db_session, disable_debug_auth_stub
):
    u = User(
        email="refreshbody@example.com",
        password_hash=hash_password("p"),
        first_name="B",
        role=UserRole.owner,
        is_email_verified=True,
    )
    db_session.add(u)
    await db_session.commit()

    login_r = await api_client.post(
        "/api/auth/login",
        json={"email": "refreshbody@example.com", "password": "p"},
    )
    rt = login_r.json()["refresh_token"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as raw:
        ref_r = await raw.post("/api/auth/refresh", json={"refresh_token": rt})

    assert ref_r.status_code == 200
    assert ref_r.json()["user_id"] == u.id


@pytest.mark.asyncio
async def test_refresh_missing_token_401(api_client, disable_debug_auth_stub):
    r = await api_client.post("/api/auth/refresh", json={})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_logout_clears_cookies_then_me_unauthorized(
    api_client, db_session, disable_debug_auth_stub
):
    u = User(
        email="logout@example.com",
        password_hash=hash_password("z"),
        first_name="X",
        role=UserRole.owner,
        is_email_verified=True,
    )
    db_session.add(u)
    await db_session.commit()

    login_r = await api_client.post(
        "/api/auth/login",
        json={"email": "logout@example.com", "password": "z"},
    )
    assert login_r.status_code == 200
    assert "tg_access" in api_client.cookies

    out_r = await api_client.post("/api/auth/logout")
    assert out_r.status_code == 200

    me_r = await api_client.get("/api/auth/me")
    assert me_r.status_code == 401


@pytest.mark.asyncio
async def test_register_owner_sets_auth_on_response(
    api_client, db_session, disable_debug_auth_stub, noop_verification_email_delay
):
    payload = {
        "email": "newowner@example.com",
        "password": "longpassword1",
        "first_name": "N",
        "business_name": "Studio",
        "business_description": None,
    }
    r = await api_client.post("/api/auth/register/owner", json=payload)
    assert r.status_code == 201
    data = r.json()
    assert data["role"] == "owner"
    assert data["access_token"]
    assert data["refresh_token"]
    assert "tg_access" in r.cookies
    me_r = await api_client.get("/api/auth/me")
    assert me_r.status_code == 200
    assert me_r.json()["email"] == "newowner@example.com"
