"""Интеграционные тесты портфолио мастеров: админ-API + публичные эндпоинты."""

import pytest

from tg_studio.config import settings
from tg_studio.db.models import MasterPortfolioFile
from tg_studio.modules.business import portfolio_files

from ..conftest import make_business, make_master

OWNER_HEADERS = {"X-Debug-User-Id": "99999"}

PNG_BYTES = b"\x89PNG\r\n\x1a\nfakepng"


@pytest.fixture
async def setup(db_session):
    business = await make_business(db_session)
    master = await make_master(db_session, business.id, full_name="Мастер Портфолио")
    return business, master


@pytest.fixture(autouse=True)
def upload_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    return tmp_path


@pytest.mark.asyncio
async def test_upload_and_list(api_client, db_session, setup):
    business, master = setup
    resp = await api_client.post(
        f"/api/admin/masters/{master.id}/portfolio",
        files={"file": ("work.png", PNG_BYTES, "image/png")},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 201
    portfolio = resp.json()["portfolio"]
    assert len(portfolio) == 1
    f = portfolio[0]
    assert f["original_name"] == "work.png"
    assert f["mime"] == "image/png"
    assert f["url"].endswith(f"/api/public/masters/{master.id}/portfolio/{f['id']}")

    # файл на диске по относительному пути
    record = await db_session.get(MasterPortfolioFile, f["id"])
    assert record is not None
    assert record.stored_path.startswith("masters/portfolio/")

    # мастер-строка отдаёт портфолио
    resp = await api_client.get("/api/admin/masters", headers=OWNER_HEADERS)
    target = next(m for m in resp.json() if m["id"] == master.id)
    assert len(target["portfolio"]) == 1


@pytest.mark.asyncio
async def test_upload_rejects_non_image(api_client, setup):
    business, master = setup
    resp = await api_client.post(
        f"/api/admin/masters/{master.id}/portfolio",
        files={"file": ("doc.pdf", b"%PDF", "application/pdf")},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 415


@pytest.mark.asyncio
async def test_upload_too_large(api_client, setup):
    business, master = setup
    resp = await api_client.post(
        f"/api/admin/masters/{master.id}/portfolio",
        files={"file": ("big.png", b"x" * (portfolio_files.MAX_FILE_BYTES + 1), "image/png")},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 413


@pytest.mark.asyncio
async def test_upload_requires_owner(api_client, setup, monkeypatch):
    """Без заголовков и вне debug — нельзя (как в проде)."""
    monkeypatch.setattr(settings, "debug", False)
    business, master = setup
    resp = await api_client.post(
        f"/api/admin/masters/{master.id}/portfolio",
        files={"file": ("a.png", PNG_BYTES, "image/png")},
    )
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_public_masters_and_file(api_client, db_session, setup):
    business, master = setup
    await api_client.post(
        f"/api/admin/masters/{master.id}/portfolio",
        files={"file": ("work.png", PNG_BYTES, "image/png")},
        headers=OWNER_HEADERS,
    )

    # публичный список без авторизации
    resp = await api_client.get("/api/public/masters")
    assert resp.status_code == 200
    masters = resp.json()
    assert len(masters) == 1
    url = masters[0]["portfolio"][0]["url"]
    assert url.startswith("http://testserver/api/public/masters/")

    # файл отдаётся без авторизации
    record = (await db_session.execute(
        __import__("sqlalchemy").select(MasterPortfolioFile)
    )).scalar_one()
    resp = await api_client.get(f"/api/public/masters/{master.id}/portfolio/{record.id}")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content == PNG_BYTES

    # чужой master_id в пути — 404
    resp = await api_client.get(f"/api/public/masters/9999/portfolio/{record.id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_inactive_master_hidden_from_public(api_client, db_session, setup):
    business, master = setup
    await api_client.post(
        f"/api/admin/masters/{master.id}/portfolio",
        files={"file": ("work.png", PNG_BYTES, "image/png")},
        headers=OWNER_HEADERS,
    )
    record = (await db_session.execute(
        __import__("sqlalchemy").select(MasterPortfolioFile)
    )).scalar_one()

    master.is_active = False
    await db_session.commit()

    resp = await api_client.get("/api/public/masters")
    assert resp.json() == []

    resp = await api_client.get(f"/api/public/masters/{master.id}/portfolio/{record.id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_portfolio_file(api_client, db_session, setup):
    business, master = setup
    resp = await api_client.post(
        f"/api/admin/masters/{master.id}/portfolio",
        files={"file": ("work.png", PNG_BYTES, "image/png")},
        headers=OWNER_HEADERS,
    )
    fid = resp.json()["portfolio"][0]["id"]
    record = await db_session.get(MasterPortfolioFile, fid)
    path = portfolio_files.absolute_path(record)
    assert path.is_file()

    resp = await api_client.delete(
        f"/api/admin/masters/{master.id}/portfolio/{fid}", headers=OWNER_HEADERS
    )
    assert resp.status_code == 204
    assert not path.is_file()
    assert await db_session.get(MasterPortfolioFile, fid) is None
