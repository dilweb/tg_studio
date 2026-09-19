"""Integration tests for the owner's business admin endpoint."""

from tests.conftest import make_business, make_user
from tg_studio.db.models import UserRole

OWNER_HEADERS = {"X-Debug-User-Id": "99999"}


async def test_get_my_business(api_client, db_session):
    business = await make_business(db_session, owner_telegram_id=99999, name="Test Studio")
    await db_session.commit()

    response = await api_client.get("/api/admin/business", headers=OWNER_HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "id": business.id,
        "name": "Test Studio",
        "description": None,
        "phone": None,
        "is_active": True,
        "owner_telegram_id": 99999,
    }


async def test_get_my_business_without_business_profile(api_client, db_session):
    await make_user(db_session, telegram_id=99999, role=UserRole.owner)
    await db_session.commit()

    response = await api_client.get("/api/admin/business", headers=OWNER_HEADERS)
    assert response.status_code == 403
