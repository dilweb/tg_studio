"""Integration tests for Google Calendar ACL sharing with the owner's address."""

from tests.conftest import make_business, make_master
from tg_studio.db.models import Business
from tg_studio.modules.google_calendar import api as gc_api

OWNER_HEADERS = {"X-Debug-User-Id": "99999"}


async def _connected_business(session, share_email: str | None = None) -> Business:
    business = await make_business(session, owner_telegram_id=99999)
    business.google_calendar_credentials_json = '{"type": "service_account"}'
    business.google_calendar_email = "sa@example.iam.gserviceaccount.com"
    business.google_share_email = share_email
    await session.commit()
    return business


async def test_share_requires_connection(api_client, db_session):
    await make_business(db_session, owner_telegram_id=99999)
    await db_session.commit()

    response = await api_client.post(
        "/api/admin/google-calendar/share",
        json={"email": "owner@example.com"},
        headers=OWNER_HEADERS,
    )
    assert response.status_code == 400


async def test_share_validates_email(api_client, db_session):
    await _connected_business(db_session)

    response = await api_client.post(
        "/api/admin/google-calendar/share",
        json={"email": "not-an-email"},
        headers=OWNER_HEADERS,
    )
    assert response.status_code == 422


async def test_share_requires_master_calendars(api_client, db_session):
    await _connected_business(db_session)

    response = await api_client.post(
        "/api/admin/google-calendar/share",
        json={"email": "owner@example.com"},
        headers=OWNER_HEADERS,
    )
    assert response.status_code == 400


async def test_share_grants_acl_on_all_calendars(api_client, db_session, monkeypatch):
    business = await _connected_business(db_session)
    master = await make_master(db_session, business.id)
    master.google_calendar_id = "cal-master-1"
    await db_session.commit()

    calls = []

    async def fake_share(credentials_json, calendar_id, email, role="writer"):
        calls.append((calendar_id, email, role))

    monkeypatch.setattr(gc_api, "share_calendar", fake_share)

    response = await api_client.post(
        "/api/admin/google-calendar/share",
        json={"email": "Me@Example.com"},
        headers=OWNER_HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "me@example.com"
    assert [item["calendar_id"] for item in body["shared"]] == ["cal-master-1"]
    assert all(item["ok"] for item in body["shared"])
    assert calls == [("cal-master-1", "me@example.com", "writer")]
    assert business.google_share_email == "me@example.com"

    status = (await api_client.get("/api/admin/google-calendar/status", headers=OWNER_HEADERS)).json()
    assert status["share_email"] == "me@example.com"


async def test_share_fails_when_google_rejects_everywhere(api_client, db_session, monkeypatch):
    business = await _connected_business(db_session)
    master = await make_master(db_session, business.id)
    master.google_calendar_id = "cal-master-1"
    await db_session.commit()

    async def failing_share(credentials_json, calendar_id, email, role="writer"):
        raise Exception("boom")

    monkeypatch.setattr(gc_api, "share_calendar", failing_share)

    response = await api_client.post(
        "/api/admin/google-calendar/share",
        json={"email": "owner@example.com"},
        headers=OWNER_HEADERS,
    )
    assert response.status_code == 502
    assert business.google_share_email is None


async def test_unshare_revokes_acl(api_client, db_session, monkeypatch):
    business = await _connected_business(db_session, share_email="owner@example.com")
    master = await make_master(db_session, business.id)
    master.google_calendar_id = "cal-master-1"
    await db_session.commit()

    revoked = []

    async def fake_unshare(credentials_json, calendar_id):
        revoked.append(calendar_id)

    monkeypatch.setattr(gc_api, "unshare_calendar", fake_unshare)

    response = await api_client.delete("/api/admin/google-calendar/share", headers=OWNER_HEADERS)
    assert response.status_code == 204
    assert revoked == ["cal-master-1"]
    assert business.google_share_email is None


async def test_new_master_calendar_is_shared_automatically(api_client, db_session, monkeypatch):
    business = await _connected_business(db_session, share_email="owner@example.com")
    master = await make_master(db_session, business.id)
    await db_session.commit()

    calls = []

    async def fake_create_calendar(credentials_json, summary):
        return "new-cal-id"

    async def fake_share(credentials_json, calendar_id, email, role="writer"):
        calls.append((calendar_id, email))

    monkeypatch.setattr(gc_api, "create_calendar", fake_create_calendar)
    monkeypatch.setattr(gc_api, "share_calendar", fake_share)

    response = await api_client.post(
        f"/api/admin/google-calendar/masters/{master.id}/calendar",
        headers=OWNER_HEADERS,
    )
    assert response.status_code == 201
    assert response.json() == {
        "master_id": master.id,
        "google_calendar_id": "new-cal-id",
        "share_error": None,
    }
    assert calls == [("new-cal-id", "owner@example.com")]
