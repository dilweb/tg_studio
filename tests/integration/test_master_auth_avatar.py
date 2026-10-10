"""Integration tests: роль мастера решает БД (без env) + аватар мастера."""

import pytest

from tests.conftest import make_business, make_master
from tg_studio.config import settings
from tg_studio.db.models import UserRole

OWNER_HEADERS = {"X-Debug-User-Id": "99999"}
MASTER_TG_ID = 555000111


@pytest.fixture
def debug_on(monkeypatch):
    """Хелпер: включаем debug только внутри теста."""
    monkeypatch.setattr(settings, "debug", True)


async def test_master_role_resolved_from_db(api_client, db_session, debug_on, monkeypatch):
    """Мастер, заведённый в панели с telegram_id, получает роль master
    через debug-вход, даже если его нет в ALLOWED_USERS (env только с owner)."""
    business = await make_business(db_session, owner_telegram_id=99999)
    await make_master(
        db_session, business_id=business.id, full_name="Мария", telegram_id=MASTER_TG_ID
    )
    await db_session.commit()

    response = await api_client.get(
        "/api/auth/me", headers={"X-Debug-User-Id": str(MASTER_TG_ID)}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["role"] == UserRole.master.value
    assert body["telegram_id"] == MASTER_TG_ID
    # master_id найден: Master.user_id привязался при входе
    assert body["master_id"] is not None


async def test_unknown_tg_id_still_403(api_client, db_session, debug_on, monkeypatch):
    """Не в env и не мастер — доступа нет."""
    monkeypatch.setattr(settings, "allowed_users", [])  # даже owner убран
    response = await api_client.get("/api/auth/me", headers={"X-Debug-User-Id": "123456"})
    assert response.status_code == 403


async def test_avatar_upload_and_public(api_client, db_session, debug_on):
    """Аватар загружается, отдаётся публично, удаляется."""
    business = await make_business(db_session, owner_telegram_id=99999)
    master = await make_master(db_session, business_id=business.id)
    await db_session.commit()

    upload = await api_client.post(
        f"/api/admin/masters/{master.id}/avatar",
        headers=OWNER_HEADERS,
        files={"file": ("me.png", b"\x89PNG fake", "image/png")},
    )
    assert upload.status_code == 200
    assert upload.json()["avatar_url"].endswith(f"/api/public/masters/{master.id}/avatar")

    public = await api_client.get(f"/api/public/masters/{master.id}/avatar")
    assert public.status_code == 200
    assert public.headers["content-type"].startswith("image/png")

    delete = await api_client.delete(
        f"/api/admin/masters/{master.id}/avatar", headers=OWNER_HEADERS
    )
    assert delete.status_code == 204
    gone = await api_client.get(f"/api/public/masters/{master.id}/avatar")
    assert gone.status_code == 404


async def test_avatar_replaces_old_file(api_client, db_session, debug_on):
    """Повторная загрузка затирает старый файл (один аватар на мастера)."""
    import shutil
    from pathlib import Path

    from tg_studio.config import settings as cfg

    shutil.rmtree(Path(cfg.upload_dir) / "masters" / "avatar", ignore_errors=True)

    business = await make_business(db_session, owner_telegram_id=99999)
    master = await make_master(db_session, business_id=business.id)
    await db_session.commit()

    for i in range(2):
        resp = await api_client.post(
            f"/api/admin/masters/{master.id}/avatar",
            headers=OWNER_HEADERS,
            files={"file": (f"me{i}.png", f"fake-{i}".encode(), "image/png")},
        )
        assert resp.status_code == 200

    await db_session.refresh(master)
    from tg_studio.modules.business import portfolio_files

    assert portfolio_files.avatar_absolute_path(master).is_file()
    # На диске ровно один файл
    assert len(list(portfolio_files.avatar_absolute_path(master).parent.glob("*"))) == 1
