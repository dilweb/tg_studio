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
        "pricing_config": {
            "global_percent": 100,
            "size_rates": {"xs": 22000, "s": 35000, "m": 60000, "l": 130000},
            "zone_factors": {"std": 1.0, "elevated": 1.2, "critical": 1.4},
            "coverup_factor": 1.4,
            "style_factors": {
                "Минимализм (Minimalism)": 1.0,
                "Лайнворк (Linework)": 1.0,
                "Леттеринг (Lettering)": 1.0,
                "Хэндпоук (Handpoke)": 1.0,
                "Традиционный (Traditional / Old School)": 1.3,
                "Нью-скул (New School)": 1.3,
                "Геометрия (Geometry)": 1.3,
                "Акварель (Watercolor)": 1.3,
                "Орнаментал (Ornamental)": 1.3,
                "Дотворк (Dotwork)": 1.3,
                "Трайбл (Tribal)": 1.3,
                "Нео-традишнл (Neo-Traditional)": 1.3,
                "Чикано (Chicano)": 1.3,
                "Эскизный / Скетч стайл (Sketch style)": 1.3,
                "Реализм (Realism)": 1.6,
                "Блэкворк (Blackwork)": 1.6,
                "Графика / Гравюра (Engraving / Woodcut)": 1.6,
                "Японский (Irezumi)": 1.6,
                "Киберпанк / Трэш-полька (Cyberpunk / Trash Polka)": 1.6,
                "Биомеханика / Биоорганика (Biomechanics / Bioorganic)": 1.6,
            },
        },
    }


async def test_get_my_business_without_business_profile(api_client, db_session):
    await make_user(db_session, telegram_id=99999, role=UserRole.owner)
    await db_session.commit()

    response = await api_client.get("/api/admin/business", headers=OWNER_HEADERS)
    assert response.status_code == 403
