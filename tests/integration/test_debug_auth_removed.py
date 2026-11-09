"""Регрессия: debug-аутентификация удалена из кода.

Smoke 2026-10-26 нашёл в проде полный доступ владельца без единого
credential: при DEBUG=true get_current_user брал ALLOWED_USERS[0] даже без
заголовка, а X-Debug-User-Id пускал любой id из ALLOWED_USERS/мастеров.
Теперь без кредов — всегда 401, debug-заголовок игнорируется.
"""

import pytest

from tests.conftest import make_business, make_init_data_headers, make_master

OWNER_TG_ID = 99999  # owner из ALLOWED_USERS в conftest
MASTER_TG_ID = 70001


@pytest.mark.asyncio
async def test_no_credentials_401_even_with_debug_enabled(api_client, db_session, monkeypatch):
    """DEBUG=true (как в тестовом env) не открывает доступ без токена."""
    from tg_studio.config import settings

    monkeypatch.setattr(settings, "debug", True)
    await make_business(db_session)
    await db_session.commit()

    r = await api_client.get("/api/admin/chats")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_debug_header_ignored_for_owner_id(api_client, db_session, monkeypatch):
    """X-Debug-User-Id с валидным owner-id из ALLOWED_USERS — всё равно 401."""
    from tg_studio.config import settings

    monkeypatch.setattr(settings, "debug", True)
    await make_business(db_session)
    await db_session.commit()

    r = await api_client.get("/api/admin/chats", headers={"X-Debug-User-Id": str(OWNER_TG_ID)})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_debug_header_ignored_for_master_id(api_client, db_session, monkeypatch):
    """То же для мастера из БД — заголовок не даёт роль."""
    from tg_studio.config import settings

    monkeypatch.setattr(settings, "debug", True)
    business = await make_business(db_session)
    await make_master(db_session, business.id, telegram_id=MASTER_TG_ID)
    await db_session.commit()

    r = await api_client.get("/api/auth/me", headers={"X-Debug-User-Id": str(MASTER_TG_ID)})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_valid_init_data_still_grants_access(api_client, db_session):
    """Настоящий путь (подписанный initData) работает: owner из ALLOWED_USERS."""
    await make_business(db_session)
    await db_session.commit()

    r = await api_client.get("/api/auth/me", headers=make_init_data_headers(OWNER_TG_ID))
    assert r.status_code == 200
    assert r.json()["role"] == "owner"
