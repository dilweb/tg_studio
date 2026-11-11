"""Integration tests for the owner's business admin endpoint."""

from tests.conftest import make_business, make_init_data_headers, make_user
from tg_studio.db.models import UserRole

OWNER_HEADERS = make_init_data_headers(99999)


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


async def test_update_business_profile(api_client, db_session):
    """PUT /admin/business правит название/описание/телефон; пустая строка очищает."""
    business = await make_business(db_session, owner_telegram_id=99999, name="Old Name")
    business.phone = "777"
    await db_session.commit()

    response = await api_client.put(
        "/api/admin/business",
        headers=OWNER_HEADERS,
        json={"name": "Needles Almaty", "description": "Рыскулбекова 28/3, вход со двора", "phone": ""},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Needles Almaty"
    assert body["description"] == "Рыскулбекова 28/3, вход со двора"
    # Пустая строка = очистить
    assert body["phone"] is None

    reread = await api_client.get("/api/admin/business", headers=OWNER_HEADERS)
    assert reread.json()["name"] == "Needles Almaty"


async def test_update_business_requires_owner(api_client, db_session):
    # без авторизации — 401 (debug-доступ удалён из кода)
    await make_business(db_session, owner_telegram_id=99999)
    await db_session.commit()

    response = await api_client.put(
        "/api/admin/business",
        json={"name": "Hacked"},
    )
    assert response.status_code == 401
