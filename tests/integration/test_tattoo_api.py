"""Интеграционные тесты /api/tattoo — авторизация owner/master и CRUD работ."""

from datetime import UTC, datetime, timedelta

import pytest

from tg_studio.config import AllowedUser, settings
from tg_studio.db.models import UserRole

from ..conftest import make_business, make_client, make_master, make_user

OWNER_HEADERS = {"X-Debug-User-Id": "99999"}
MASTER_TG_ID = 88888
MASTER_HEADERS = {"X-Debug-User-Id": str(MASTER_TG_ID)}


@pytest.fixture
async def business(db_session):
    return await make_business(db_session, owner_telegram_id=99999)


@pytest.fixture
async def master(db_session, business, monkeypatch):
    """Мастер с привязанным User — чтобы ходить под его debug-заголовком."""
    user = await make_user(
        db_session, telegram_id=MASTER_TG_ID, first_name="Мастер Тест", role=UserRole.master
    )
    m = await make_master(db_session, business_id=business.id, full_name="Мастер Тест")
    m.user_id = user.id
    await db_session.commit()
    # Debug-auth разрешает роль из ALLOWED_USERS — добавляем мастера к владельцу
    monkeypatch.setattr(
        settings,
        "allowed_users",
        [*settings.allowed_users, AllowedUser(id=MASTER_TG_ID, role="master")],
    )
    return m


def work_payload(client_id: int, master_id: int | None = None, first_session: dict | None = None):
    body = {
        "client_id": client_id,
        "size_length_cm": 15,
        "size_height_cm": 10,
        "complexity": "средняя",
        "style": "Лайнворк (Linework)",
        "placement": "Предплечье (внутренняя / внешняя сторона)",
        "first_session": first_session
        or {
            "session_date": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "cost": 30000,
        },
    }
    if master_id is not None:
        body["master_id"] = master_id
    return body


def _iso(days_ahead: int) -> str:
    return (datetime.now(UTC) + timedelta(days=days_ahead)).replace(microsecond=0).isoformat()


@pytest.mark.asyncio
async def test_price_estimate(api_client, db_session, business):
    """Рекомендуемая цена: ставка размера × K_стиль × K_зона × K_доп × K_глоб."""
    async def estimate(**params):
        resp = await api_client.get(
            "/api/tattoo/price/estimate", params=params, headers=OWNER_HEADERS
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["recommended_price"]

    # Пример из прайса: микро-реализм 8×8 на внутренней стороне бедра
    # S=35 000 × 1.6 (реализм) × 1.4 (внутр. бедро) = 78 400 → 78 000
    assert (
        await estimate(
            length_cm=8,
            height_cm=8,
            style="Реализм (Realism)",
            placement="Внутренняя поверхность бедра",
        )
        == 78_000
    )

    # Тот же размер, лёгкий стиль, стандартная зона: S = 35 000 × 1.0 × 1.0
    assert (
        await estimate(
            length_cm=8,
            height_cm=8,
            style="Минимализм (Minimalism)",
            placement="Предплечье (внутренняя / внешняя сторона)",
        )
        == 35_000
    )

    # Cover-up: × 1.4 → 78 400 × 1.4 = 109 760 → 110 000
    assert (
        await estimate(
            length_cm=8,
            height_cm=8,
            style="Реализм (Realism)",
            placement="Внутренняя поверхность бедра",
            coverup=True,
        )
        == 110_000
    )

    # Крупный проект (>20 см) — дневной сеанс
    assert (
        await estimate(
            length_cm=25,
            height_cm=15,
            style="Минимализм (Minimalism)",
            placement="Предплечье (внутренняя / внешняя сторона)",
        )
        == 130_000
    )

    # Миниатюра — минимум вызова мастера (база 22 000)
    assert (
        await estimate(
            length_cm=3,
            height_cm=3,
            style="Минимализм (Minimalism)",
            placement="Предплечье (внутренняя / внешняя сторона)",
        )
        == 22_000
    )

    # Сложная зона дороже стандартной при прочих равных
    assert (
        await estimate(
            length_cm=8,
            height_cm=8,
            style="Минимализм (Minimalism)",
            placement="Ребра и бока",
        )
        > 35_000
    )


@pytest.mark.asyncio
async def test_pricing_config_owner_calibrates(api_client, db_session, business):
    """Глобальный % и коэффициенты из «Бизнеса» влияют на оценку."""
    base = await api_client.get(
        "/api/tattoo/price/estimate",
        params={"length_cm": 8, "height_cm": 8, "style": "Реализм (Realism)"},
        headers=OWNER_HEADERS,
    )
    base_price = base.json()["recommended_price"]

    # Скидка 10%: все цены × 0.9
    resp = await api_client.put(
        "/api/admin/business/pricing",
        json={"global_percent": 90, "size_rates": {"xs": 22000, "s": 35000, "m": 60000, "l": 130000},
              "style_factors": {"Реализм (Realism)": 1.6},
              "zone_factors": {"std": 1.0, "elevated": 1.2, "critical": 1.4},
              "coverup_factor": 1.4},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    discounted = await api_client.get(
        "/api/tattoo/price/estimate",
        params={"length_cm": 8, "height_cm": 8, "style": "Реализм (Realism)"},
        headers=OWNER_HEADERS,
    )
    assert discounted.json()["recommended_price"] == int(base_price * 0.9 / 1000) * 1000

    # Своя ставка S вместо дефолтной
    resp = await api_client.put(
        "/api/admin/business/pricing",
        json={"global_percent": 100, "size_rates": {"xs": 22000, "s": 50_000, "m": 60000, "l": 130000},
              "style_factors": {}, "zone_factors": {"std": 1.0, "elevated": 1.2, "critical": 1.4},
              "coverup_factor": 1.4},
        headers=OWNER_HEADERS,
    )
    custom = await api_client.get(
        "/api/tattoo/price/estimate",
        params={"length_cm": 8, "height_cm": 8, "style": "Минимализм (Minimalism)"},
        headers=OWNER_HEADERS,
    )
    assert custom.json()["recommended_price"] == 50_000

    # Возврат к дефолтам — восстановим 100% и S=35000
    resp = await api_client.put(
        "/api/admin/business/pricing",
        json={"global_percent": 100, "size_rates": {"xs": 22000, "s": 35000, "m": 60000, "l": 130000},
              "style_factors": {"Реализм (Realism)": 1.6},
              "zone_factors": {"std": 1.0, "elevated": 1.2, "critical": 1.4},
              "coverup_factor": 1.4},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["pricing_config"]["global_percent"] == 100


@pytest.mark.asyncio
async def test_pricing_config_validation(api_client, db_session, business):
    """Невалидный прайс — 422, конфиг не меняется."""
    resp = await api_client.put(
        "/api/admin/business/pricing",
        json={"global_percent": 3000, "size_rates": {"xs": 1, "s": 1, "m": 1, "l": 1},
              "style_factors": {}, "zone_factors": {"std": 1, "elevated": 1, "critical": 1},
              "coverup_factor": 1},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 422

    resp = await api_client.put(
        "/api/admin/business/pricing",
        json={"global_percent": 100, "size_rates": {"xs": 1, "s": 1, "m": 1, "l": 1},
              "style_factors": {"Стиль-которого-нет": 2.0},
              "zone_factors": {"std": 1, "elevated": 1, "critical": 1},
              "coverup_factor": 1},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_options_catalog(api_client, db_session, business):
    resp = await api_client.get("/api/tattoo/options", headers=OWNER_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert "Лайнворк (Linework)" in body["styles"]
    assert len(body["styles"]) == 20
    forearm = next(p for p in body["placements"] if p["value"].startswith("Предплечье"))
    assert forearm["difficult"] is False
    ribs = next(p for p in body["placements"] if p["value"] == "Ребра и бока")
    assert ribs["difficult"] is True
    assert body["complexities"] == ["низкая", "средняя", "высокая"]


@pytest.mark.asyncio
async def test_create_rejects_unknown_style(api_client, db_session, business, master):
    client = await make_client(db_session, telegram_id=121)
    body = work_payload(client.id)
    body["style"] = "хип-хоп"
    resp = await api_client.post("/api/tattoo/works", json=body, headers=MASTER_HEADERS)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_clients_list(api_client, db_session, business):
    await make_client(db_session, telegram_id=111, full_name="Клиент Один")
    resp = await api_client.get("/api/tattoo/clients", headers=OWNER_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["full_name"] == "Клиент Один"


@pytest.mark.asyncio
async def test_owner_creates_work_for_master(api_client, db_session, business, master):
    client = await make_client(db_session, telegram_id=112)
    resp = await api_client.post(
        "/api/tattoo/works", json=work_payload(client.id, master.id), headers=OWNER_HEADERS
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["master_id"] == master.id
    assert body["business_id"] == business.id
    assert body["status"] == "in_progress"
    assert len(body["sessions"]) == 1
    assert body["sessions"][0]["cost"] == 30000


@pytest.mark.asyncio
async def test_owner_create_requires_master_id(api_client, db_session, business):
    client = await make_client(db_session, telegram_id=113)
    resp = await api_client.post(
        "/api/tattoo/works", json=work_payload(client.id), headers=OWNER_HEADERS
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_master_scoped_to_own_works(api_client, db_session, business, master):
    other = await make_master(db_session, business_id=business.id, full_name="Другой Мастер")
    client = await make_client(db_session, telegram_id=114)

    resp = await api_client.post(
        "/api/tattoo/works", json=work_payload(client.id), headers=MASTER_HEADERS
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["master_id"] == master.id  # body.master_id игнорируется

    # Работа другого мастера не видна и не удалится
    resp2 = await api_client.post(
        "/api/tattoo/works", json=work_payload(client.id, other.id), headers=OWNER_HEADERS
    )
    other_work_id = resp2.json()["id"]

    resp = await api_client.get("/api/tattoo/works", headers=MASTER_HEADERS)
    body = resp.json()
    assert body["total"] == 1
    assert body["works"][0]["id"] != other_work_id

    resp = await api_client.get(f"/api/tattoo/works/{other_work_id}", headers=MASTER_HEADERS)
    assert resp.status_code == 404

    resp = await api_client.delete(f"/api/tattoo/works/{other_work_id}", headers=MASTER_HEADERS)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_owner_filters_by_master(api_client, db_session, business, master):
    other = await make_master(db_session, business_id=business.id, full_name="Второй Мастер")
    client = await make_client(db_session, telegram_id=115)
    await api_client.post(
        "/api/tattoo/works", json=work_payload(client.id, master.id), headers=OWNER_HEADERS
    )
    await api_client.post(
        "/api/tattoo/works", json=work_payload(client.id, other.id), headers=OWNER_HEADERS
    )

    resp = await api_client.get(
        "/api/tattoo/works", params={"master_id": master.id}, headers=OWNER_HEADERS
    )
    body = resp.json()
    assert body["total"] == 1
    assert body["works"][0]["master_id"] == master.id


@pytest.mark.asyncio
async def test_add_final_session_completes_work(api_client, db_session, business, master):
    client = await make_client(db_session, telegram_id=116)
    resp = await api_client.post(
        "/api/tattoo/works", json=work_payload(client.id), headers=MASTER_HEADERS
    )
    work_id = resp.json()["id"]

    next_week = (datetime.now(UTC) + timedelta(days=7)).replace(microsecond=0).isoformat()
    resp = await api_client.post(
        f"/api/tattoo/works/{work_id}/sessions",
        json={"session_date": next_week, "cost": 15000, "is_final_session": True},
        headers=MASTER_HEADERS,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["is_final_session"] is True

    resp = await api_client.get(f"/api/tattoo/works/{work_id}", headers=MASTER_HEADERS)
    assert resp.json()["status"] == "completed"
    assert len(resp.json()["sessions"]) == 2


@pytest.mark.asyncio
async def test_session_without_cost_is_planned(api_client, db_session, business, master):
    """Сеанс планируется без фактической суммы — только рекомендуемая цена."""
    client = await make_client(db_session, telegram_id=117)
    resp = await api_client.post(
        "/api/tattoo/works",
        json=work_payload(
            client.id, first_session={"session_date": _iso(1), "recommended_price": 60000}
        ),
        headers=MASTER_HEADERS,
    )
    assert resp.status_code == 201, resp.text
    first = resp.json()["sessions"][0]
    assert first["cost"] is None
    assert first["recommended_price"] == 60000

    # Второй сеанс тоже без cost
    resp = await api_client.post(
        f"/api/tattoo/works/{resp.json()['id']}/sessions",
        json={"session_date": _iso(14)},
        headers=MASTER_HEADERS,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["cost"] is None


@pytest.mark.asyncio
async def test_create_validates_client(api_client, db_session, business, master):
    resp = await api_client.post(
        "/api/tattoo/works", json=work_payload(999999), headers=MASTER_HEADERS
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_session_overlap_conflict(api_client, db_session, business, master):
    """Дубль записи у того же мастера — 409; force=true прокидывает."""
    client1 = await make_client(db_session, telegram_id=130)
    client2 = await make_client(db_session, telegram_id=131)

    resp = await api_client.post(
        "/api/tattoo/works", json=work_payload(client1.id, master.id), headers=OWNER_HEADERS
    )
    assert resp.status_code == 201, resp.text

    # та же минута, тот же мастер — конфликт (работа 1 у мастера уже есть)
    resp = await api_client.post(
        "/api/tattoo/works", json=work_payload(client2.id, master.id), headers=OWNER_HEADERS
    )
    assert resp.status_code == 409, resp.text
    assert "уже занято" in resp.json()["detail"]

    # force — осознанная запись поверх
    resp = await api_client.post(
        "/api/tattoo/works",
        json=work_payload(client2.id, master.id),
        params={"force": True},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 201, resp.text

    # у другого мастера в то же время — не конфликт
    other = await make_master(db_session, business_id=business.id, full_name="Второй Мастер")
    resp = await api_client.post(
        "/api/tattoo/works", json=work_payload(client2.id, other.id), headers=OWNER_HEADERS
    )
    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_add_session_overlap_and_move(api_client, db_session, business, master):
    """Добавление сеанса в занятый слот — 409; перенос на свободное время — ок."""
    client = await make_client(db_session, telegram_id=132)
    resp = await api_client.post(
        "/api/tattoo/works", json=work_payload(client.id, master.id), headers=MASTER_HEADERS
    )
    assert resp.status_code == 201, resp.text
    work_id = resp.json()["id"]
    busy_time = resp.json()["sessions"][0]["session_date"]

    # новый сеанс в тот же слот (и той же работы) — 409
    resp = await api_client.post(
        f"/api/tattoo/works/{work_id}/sessions",
        json={"session_date": busy_time},
        headers=MASTER_HEADERS,
    )
    assert resp.status_code == 409, resp.text

    # другой сеанс +1 день — ок
    resp = await api_client.post(
        f"/api/tattoo/works/{work_id}/sessions",
        json={"session_date": _iso(1)},
        headers=MASTER_HEADERS,
    )
    assert resp.status_code == 201, resp.text
    session_id = resp.json()["id"]

    # перенос на занятое время — 409
    resp = await api_client.patch(
        f"/api/tattoo/works/{work_id}/sessions/{session_id}",
        json={"session_date": busy_time},
        headers=MASTER_HEADERS,
    )
    assert resp.status_code == 409, resp.text

    # перенос на чуть более свободное время — ок
    resp = await api_client.patch(
        f"/api/tattoo/works/{work_id}/sessions/{session_id}",
        json={"session_date": _iso(2)},
        headers=MASTER_HEADERS,
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_calendar_conflict_detected(api_client, db_session, business, master, monkeypatch):
    """Событие, созданное руками в Google Calendar мимо CRM, блокирует слот."""
    business.google_calendar_credentials_json = "{}"
    await db_session.commit()

    busy_start = (datetime.now(UTC) + timedelta(hours=1)).replace(microsecond=0)

    async def fake_list_events(**kwargs):
        return [
            {
                "id": "ev-personal",
                "summary": "Личная встреча",
                "start": {"dateTime": busy_start.isoformat()},
                "end": {"dateTime": (busy_start + timedelta(hours=2)).isoformat()},
            }
        ]

    monkeypatch.setattr(
        "tg_studio.modules.tattoo.api.list_events", fake_list_events, raising=False
    )

    client = await make_client(db_session, telegram_id=133)
    resp = await api_client.post(
        "/api/tattoo/works", json=work_payload(client.id, master.id), headers=OWNER_HEADERS
    )
    assert resp.status_code == 409, resp.text
    assert "Google Calendar" in resp.json()["detail"]
    assert "Личная встреча" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_update_work_and_session(api_client, db_session, business, master):
    """Правка работы (с валидацией статуса) и сеанса после создания."""
    client = await make_client(db_session, telegram_id=134)
    resp = await api_client.post(
        "/api/tattoo/works", json=work_payload(client.id, master.id), headers=OWNER_HEADERS
    )
    assert resp.status_code == 201, resp.text
    work_id = resp.json()["id"]

    resp = await api_client.patch(
        f"/api/tattoo/works/{work_id}",
        json={"size_length_cm": 20, "style": "Реализм (Realism)", "status": "completed"},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["size_length_cm"] == 20.0
    assert body["style"] == "Реализм (Realism)"
    assert body["status"] == "completed"

    # мусорный статус — 422
    resp = await api_client.patch(
        f"/api/tattoo/works/{work_id}", json={"status": "какой-то"}, headers=OWNER_HEADERS
    )
    assert resp.status_code == 422

    # правка сеанса: время и цены
    sid = body["sessions"][0]["id"]
    resp = await api_client.patch(
        f"/api/tattoo/works/{work_id}/sessions/{sid}",
        json={"session_date": _iso(3), "recommended_price": None, "cost": 45000},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    s = resp.json()
    assert s["cost"] == 45000
    assert s["recommended_price"] is None


# ---------------------------------------------------------------------------
# Фото сеансов (tattoo_files)
# ---------------------------------------------------------------------------


@pytest.fixture
def upload_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    return tmp_path


async def _create_work(
    api_client, db_session, *, telegram_id: int, session_date: str | None = None
) -> dict:
    client = await make_client(db_session, telegram_id=telegram_id)
    body = work_payload(client.id)
    if session_date:
        body["first_session"]["session_date"] = session_date
    resp = await api_client.post("/api/tattoo/works", json=body, headers=MASTER_HEADERS)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _upload_files():
    return {"file": ("IMG_0001.jpg", b"\xff\xd8\xfaKE-photo-bytes", "image/jpeg")}


@pytest.mark.asyncio
async def test_upload_and_get_file(
    api_client, db_session, business, master, upload_dir, monkeypatch
):
    work = await _create_work(api_client, db_session, telegram_id=117)
    sid = work["sessions"][0]["id"]

    resp = await api_client.post(
        f"/api/tattoo/works/{work['id']}/sessions/{sid}/files",
        data={"kind": "result"},
        files=_upload_files(),
        headers=MASTER_HEADERS,
    )
    assert resp.status_code == 201, resp.text
    out = resp.json()
    assert out["kind"] == "result"
    assert out["original_name"] == "IMG_0001.jpg"
    assert out["mime"] == "image/jpeg"
    assert out["file_url"] and "?t=" in out["file_url"]

    # Подписанная ссылка работает без заголовков авторизации
    resp = await api_client.get(out["file_url"])
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/jpeg")

    # По авторизации — тоже
    resp = await api_client.get(f"/api/tattoo/files/{out['id']}", headers=OWNER_HEADERS)
    assert resp.status_code == 200

    # Без токена и без авторизации — нельзя. Отключаем debug-фоллбэк
    # (в тестах DEBUG=true анонимный запрос сам становится владельцем),
    # после проверки возвращаем — остальным запросам теста нужен debug
    monkeypatch.setattr(settings, "debug", False)
    resp = await api_client.get(f"/api/tattoo/files/{out['id']}")
    assert resp.status_code == 401
    monkeypatch.setattr(settings, "debug", True)

    # Файл на диске в папке сеанса
    stored = next((upload_dir / "tattoo" / "sessions" / str(sid)).iterdir())
    assert stored.read_bytes() == b"\xff\xd8\xfaKE-photo-bytes"

    # Файл вернулся в ответе сеанса
    resp = await api_client.get(f"/api/tattoo/works/{work['id']}", headers=MASTER_HEADERS)
    files = resp.json()["sessions"][0]["files"]
    assert len(files) == 1
    assert files[0]["id"] == out["id"] and "?t=" in files[0]["file_url"]


@pytest.mark.asyncio
async def test_upload_validations(api_client, db_session, business, master, upload_dir):
    work = await _create_work(api_client, db_session, telegram_id=118)
    sid = work["sessions"][0]["id"]
    url = f"/api/tattoo/works/{work['id']}/sessions/{sid}/files"

    # не изображение
    resp = await api_client.post(
        url,
        data={"kind": "result"},
        files={"file": ("doc.pdf", b"%PDF", "application/pdf")},
        headers=MASTER_HEADERS,
    )
    assert resp.status_code == 400

    # пустой файл
    resp = await api_client.post(
        url,
        data={"kind": "result"},
        files={"file": ("x.jpg", b"", "image/jpeg")},
        headers=MASTER_HEADERS,
    )
    assert resp.status_code == 400

    # больше 10 МБ
    resp = await api_client.post(
        url,
        data={"kind": "result"},
        files={"file": ("big.jpg", b"x" * (10 * 1024 * 1024 + 1), "image/jpeg")},
        headers=MASTER_HEADERS,
    )
    assert resp.status_code == 400

    # неизвестный kind
    resp = await api_client.post(
        url, data={"kind": "unknown"}, files=_upload_files(), headers=MASTER_HEADERS
    )
    assert resp.status_code == 422

    # чужой сеанс недоступен мастеру
    work2 = await _create_work(
        api_client, db_session, telegram_id=119, session_date=_iso(5)
    )
    resp = await api_client.post(
        f"/api/tattoo/works/{work2['id']}/sessions/{work2['sessions'][0]['id']}/files",
        data={"kind": "result"},
        files=_upload_files(),
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 201  # владельцу можно


@pytest.mark.asyncio
async def test_delete_file_and_work_cleans_disk(
    api_client, db_session, business, master, upload_dir
):
    work = await _create_work(api_client, db_session, telegram_id=120)
    sid = work["sessions"][0]["id"]
    resp = await api_client.post(
        f"/api/tattoo/works/{work['id']}/sessions/{sid}/files",
        data={"kind": "sketch"},
        files=_upload_files(),
        headers=MASTER_HEADERS,
    )
    file_id = resp.json()["id"]
    session_dir = upload_dir / "tattoo" / "sessions" / str(sid)
    assert session_dir.exists()

    # удаление одного файла: строка и диск
    resp = await api_client.delete(f"/api/tattoo/files/{file_id}", headers=MASTER_HEADERS)
    assert resp.status_code == 204
    assert not list(session_dir.iterdir())

    # удаление работы стирает всю папку сеансов
    resp = await api_client.post(
        f"/api/tattoo/works/{work['id']}/sessions/{sid}/files",
        data={"kind": "result"},
        files=_upload_files(),
        headers=MASTER_HEADERS,
    )
    assert resp.status_code == 201
    resp = await api_client.delete(f"/api/tattoo/works/{work['id']}", headers=MASTER_HEADERS)
    assert resp.status_code == 204
    assert not session_dir.exists()
