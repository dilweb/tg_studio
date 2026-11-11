"""Интеграционные тесты ценовой политики коворкинга (docs/coworking/03).

Покрытие:
- CRUD ценовых правил и скидок: владелец — ок, мастер — 403 на запись;
  валидация (time_from < time_to, day_kind=дата требует specific_date);
- калькулятор POST /api/pricing/quote: разрезание интервала по границам
  правил, приоритет, правило места над «все места», особая дата поверх дня,
  выходные, округление до копеек, 409 когда интервал не покрыт ценой;
- скидки: резидент (%), min_hours, фиксированная сумма, discount ≤ base;
- интеграция с бронями: снимок в bookings.price_snapshot при создании,
  смена правил не трогает существующую бронь, перенос → пересчёт + события
  «перенесена» и «пересчитана_цена» в истории;
- права: мастер читает правила/скидки/quote, но не меняет их.
"""

from datetime import datetime, timedelta

import pytest

from tg_studio.config import AllowedUser, settings
from tg_studio.db.models import UserRole
from tg_studio.timeutil import STUDIO_TZ

from ..conftest import (
    make_business,
    make_init_data_headers,
    make_master,
    make_user,
)

OWNER_HEADERS = make_init_data_headers(99999)
MASTER_TG_ID = 88888
MASTER_HEADERS = make_init_data_headers(MASTER_TG_ID)


@pytest.fixture
async def business(db_session):
    return await make_business(db_session, owner_telegram_id=99999)


@pytest.fixture
async def master(db_session, business, monkeypatch):
    user = await make_user(
        db_session, telegram_id=MASTER_TG_ID, first_name="Мастер Тест", role=UserRole.master
    )
    m = await make_master(db_session, business_id=business.id, full_name="Мастер Тест")
    m.user_id = user.id
    await db_session.commit()
    monkeypatch.setattr(
        settings,
        "allowed_users",
        [*settings.allowed_users, AllowedUser(id=MASTER_TG_ID, role="master")],
    )
    return m


@pytest.fixture
async def seats(api_client, business):
    created = []
    for i in range(4):
        resp = await api_client.post(
            "/api/seats", json={"name": f"Место {i + 1}"}, headers=OWNER_HEADERS
        )
        assert resp.status_code == 201, resp.text
        created.append(resp.json())
    return created


def _at(hour: int, minute: int = 0, days: int = 0) -> str:
    """Завтра (+days) в hour:minute по студии (наивное время = Asia/Almaty)."""
    day = (datetime.now(STUDIO_TZ) + timedelta(days=1 + days)).date()
    return f"{day.isoformat()}T{hour:02d}:{minute:02d}:00"


def _next_weekday(weekday: int, hour: int = 12) -> str:
    """Ближайшая будущая дата с данным днём недели (0=пн…6=вс), naive-строка."""
    day = (datetime.now(STUDIO_TZ) + timedelta(days=1)).date()
    while day.weekday() != weekday:
        day += timedelta(days=1)
    return f"{day.isoformat()}T{hour:02d}:00:00"


async def _base_rule(api_client, hourly_rate=1000, **extra) -> dict:
    body = {
        "seat_id": None,
        "name": "Базовый тариф",
        "day_kind": "все",
        "time_from": "10:00:00",
        "time_to": "22:00:00",
        "hourly_rate": hourly_rate,
        "priority": 0,
    }
    body.update(extra)
    resp = await api_client.post("/api/price-rules", json=body, headers=OWNER_HEADERS)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# CRUD правил и права
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_price_rules_crud_and_rights(api_client, business, seats, master):
    # Мастер не создаёт/не меняет правила — 403
    resp = await api_client.post(
        "/api/price-rules",
        json={
            "name": "хак",
            "day_kind": "все",
            "time_from": "10:00:00",
            "time_to": "22:00:00",
            "hourly_rate": 1,
        },
        headers=MASTER_HEADERS,
    )
    assert resp.status_code == 403

    rule = await _base_rule(api_client, hourly_rate=1500)
    assert rule["hourly_rate"] == 1500
    assert rule["seat_id"] is None

    # Валидация: окно времени
    resp = await api_client.post(
        "/api/price-rules",
        json={
            "name": "кривое",
            "day_kind": "все",
            "time_from": "22:00:00",
            "time_to": "10:00:00",
            "hourly_rate": 100,
        },
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 422

    # day_kind=дата без даты — 422
    resp = await api_client.post(
        "/api/price-rules",
        json={
            "name": "праздник",
            "day_kind": "дата",
            "time_from": "10:00:00",
            "time_to": "22:00:00",
            "hourly_rate": 100,
        },
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 422

    # PATCH: смена ставки и деактивация
    resp = await api_client.patch(
        f"/api/price-rules/{rule['id']}", json={"hourly_rate": 2000, "is_active": False},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["hourly_rate"] == 2000
    assert resp.json()["is_active"] is False

    # Неактивное правило скрыто по умолчанию, видно с include_inactive
    resp = await api_client.get("/api/price-rules", headers=MASTER_HEADERS)
    assert all(r["id"] != rule["id"] for r in resp.json()["rules"])
    resp = await api_client.get("/api/price-rules?include_inactive=true", headers=MASTER_HEADERS)
    assert any(r["id"] == rule["id"] for r in resp.json()["rules"])

    # DELETE — мастер 403, владелец 204
    resp = await api_client.delete(f"/api/price-rules/{rule['id']}", headers=MASTER_HEADERS)
    assert resp.status_code == 403
    resp = await api_client.delete(f"/api/price-rules/{rule['id']}", headers=OWNER_HEADERS)
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_price_rule_on_specific_seat(api_client, business, seats):
    resp = await api_client.post(
        "/api/price-rules",
        json={
            "seat_id": seats[1]["id"],
            "name": "Только место 2",
            "day_kind": "все",
            "time_from": "10:00:00",
            "time_to": "22:00:00",
            "hourly_rate": 3000,
        },
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 201, resp.text
    # Чужое/несуществующее место — 404
    resp = await api_client.post(
        "/api/price-rules",
        json={
            "seat_id": 99999,
            "name": "хак",
            "day_kind": "все",
            "time_from": "10:00:00",
            "time_to": "22:00:00",
            "hourly_rate": 1,
        },
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Калькулятор (quote)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_quote_simple(api_client, business, seats, master):
    await _base_rule(api_client, hourly_rate=1000)
    resp = await api_client.post(
        "/api/pricing/quote",
        json={"seat_id": seats[0]["id"], "start": _at(12), "end": _at(14)},
        headers=MASTER_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    q = resp.json()
    assert q["base"] == 2000
    assert q["discount"] == 0
    assert q["final"] == 2000
    assert q["currency"] == "KZT"
    assert len(q["parts"]) == 1
    assert q["parts"][0]["minutes"] == 120
    assert q["parts"][0]["hourly_rate"] == 1000


@pytest.mark.asyncio
async def test_quote_splits_by_rule_boundaries(api_client, business, seats, master):
    """Утро 1500 (10–18), вечер 2500 (18–22): интервал 10–20 режется на 10–18 и 18–20."""
    await _base_rule(api_client, hourly_rate=1500, name="Утро", time_to="18:00:00")
    resp = await api_client.post(
        "/api/price-rules",
        json={
            "name": "Вечер",
            "day_kind": "все",
            "time_from": "18:00:00",
            "time_to": "22:00:00",
            "hourly_rate": 2500,
        },
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 201, resp.text
    resp = await api_client.post(
        "/api/pricing/quote",
        json={"seat_id": seats[0]["id"], "start": _at(10), "end": _at(20)},
        headers=MASTER_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    q = resp.json()
    assert len(q["parts"]) == 2
    assert q["parts"][0]["rule_name"] == "Утро" and q["parts"][0]["amount"] == 8 * 1500
    assert q["parts"][1]["rule_name"] == "Вечер" and q["parts"][1]["amount"] == 2 * 2500
    assert q["base"] == 8 * 1500 + 2 * 2500
    assert q["final"] == q["base"]


@pytest.mark.asyncio
async def test_quote_priority_and_seat_specific_rule(api_client, business, seats, master):
    """Два пересекающихся правила: выше priority побеждает; при равном —
    правило конкретного места над «все места»."""
    await _base_rule(api_client, hourly_rate=1000, name="Все места, весь день")
    # Пересекающееся окно с высоким приоритетом — на все места
    await _base_rule(api_client, hourly_rate=5000, name="Дорогое окно", time_from="12:00:00",
                     time_to="14:00:00", priority=5)
    # То же окно на конкретное место с меньшим priority — проигрывает правилу выше
    resp = await api_client.post(
        "/api/price-rules",
        json={
            "seat_id": seats[0]["id"],
            "name": "Место 1, то же окно",
            "day_kind": "все",
            "time_from": "12:00:00",
            "time_to": "14:00:00",
            "hourly_rate": 9000,
            "priority": 5,
        },
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 201
    resp = await api_client.post(
        "/api/pricing/quote",
        json={"seat_id": seats[0]["id"], "start": _at(11), "end": _at(15)},
        headers=MASTER_HEADERS,
    )
    q = resp.json()
    # 11–12 по 1000, 12–14 — при равном priority правило конкретного места
    # выигрывает у «все места» (задокументировано), 14–15 по 1000
    assert len(q["parts"]) == 3
    assert q["parts"][1]["rule_name"] == "Место 1, то же окно"
    assert q["base"] == 1000 + 2 * 9000 + 1000

    # На другом месте правило места не действует — только «все места»
    resp = await api_client.post(
        "/api/pricing/quote",
        json={"seat_id": seats[1]["id"], "start": _at(13), "end": _at(14)},
        headers=MASTER_HEADERS,
    )
    q = resp.json()
    assert q["parts"][0]["rule_name"] == "Дорогое окно"  # правило "все места" с priority 5
    assert q["base"] == 5000

    # А на seat 0 в окне 12–14 выигрывает правило места (при равном priority)
    resp = await api_client.post(
        "/api/pricing/quote",
        json={"seat_id": seats[0]["id"], "start": _at(13), "end": _at(14)},
        headers=MASTER_HEADERS,
    )
    assert resp.json()["parts"][0]["rule_name"] == "Место 1, то же окно"


@pytest.mark.asyncio
async def test_quote_specific_date_overrides_day(api_client, business, seats, master):
    """Особая дата (праздник) с высоким priority перекрывает обычную цену дня."""
    await _base_rule(api_client, hourly_rate=1000, name="Обычный день")
    holiday = (datetime.now(STUDIO_TZ) + timedelta(days=1)).date().isoformat()
    resp = await api_client.post(
        "/api/price-rules",
        json={
            "name": "Праздник",
            "day_kind": "дата",
            "specific_date": holiday,
            "time_from": "10:00:00",
            "time_to": "22:00:00",
            "hourly_rate": 9999,
            "priority": 10,
        },
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 201, resp.text
    resp = await api_client.post(
        "/api/pricing/quote",
        json={"seat_id": seats[0]["id"], "start": _at(12), "end": _at(13)},
        headers=MASTER_HEADERS,
    )
    q = resp.json()
    assert q["base"] == 9999
    assert q["parts"][0]["rule_name"] == "Праздник"


@pytest.mark.asyncio
async def test_quote_weekdays_weekends(api_client, business, seats, master):
    """Будни/выходные: разные ставки на разные дни недели."""
    await _base_rule(api_client, hourly_rate=1000, name="Будни", day_kind="будни")
    await _base_rule(api_client, hourly_rate=3000, name="Выходные", day_kind="выходные")
    # Ближайшая суббота (5) и понедельник (0)
    sat = _next_weekday(5, hour=12)
    mon = _next_weekday(0, hour=12)
    resp = await api_client.post(
        "/api/pricing/quote",
        json={"seat_id": seats[0]["id"], "start": sat, "end": sat[:11] + "14:00:00"},
        headers=MASTER_HEADERS,
    )
    assert resp.json()["final"] == 2 * 3000
    resp = await api_client.post(
        "/api/pricing/quote",
        json={"seat_id": seats[0]["id"], "start": mon, "end": mon[:11] + "14:00:00"},
        headers=MASTER_HEADERS,
    )
    assert resp.json()["final"] == 2 * 1000


@pytest.mark.asyncio
async def test_quote_uncovered_interval_409(api_client, business, seats, master):
    """Нет правила на часть интервала → 409 с описанием непокрытых сегментов."""
    # Правило только 10:00–14:00
    await _base_rule(api_client, hourly_rate=1000, time_to="14:00:00")
    resp = await api_client.post(
        "/api/pricing/quote",
        json={"seat_id": seats[0]["id"], "start": _at(12), "end": _at(16)},
        headers=MASTER_HEADERS,
    )
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert "uncovered" in detail
    # Непокрытый сегмент начинается в 14:00 студийного времени
    assert any("T14:00:00" in u["start"] for u in detail["uncovered"])


@pytest.mark.asyncio
async def test_quote_rounding(api_client, business, seats, master):
    """Округление до копеек: 999 ₸/ч × 30 мин = 499.50."""
    await _base_rule(api_client, hourly_rate=999)
    resp = await api_client.post(
        "/api/pricing/quote",
        json={"seat_id": seats[0]["id"], "start": _at(12), "end": _at(12, 30)},
        headers=MASTER_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["final"] == 499.50


# ---------------------------------------------------------------------------
# Скидки
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resident_discount_not_supported(api_client, business, seats, master):
    """Скидок резидентам нет: kind=resident отклоняется, тариф резидента владелец задаёт сам."""
    resp = await api_client.post(
        "/api/discount-rules",
        json={"name": "Резиденты", "kind": "resident", "percent": 0.10},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_discount_min_hours_and_fixed(api_client, business, seats, master):
    await _base_rule(api_client, hourly_rate=1000)
    resp = await api_client.post(
        "/api/discount-rules",
        json={"name": "От 4 часов — 500 ₸", "kind": "min_hours", "min_hours": 4, "fixed_amount": 500},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 201, resp.text
    # 2 часа — порог не достигнут
    resp = await api_client.post(
        "/api/pricing/quote",
        json={"seat_id": seats[0]["id"], "start": _at(12), "end": _at(14)},
        headers=MASTER_HEADERS,
    )
    assert resp.json()["final"] == 2000
    # 4 часа — скидка 500
    resp = await api_client.post(
        "/api/pricing/quote",
        json={"seat_id": seats[0]["id"], "start": _at(12), "end": _at(16)},
        headers=MASTER_HEADERS,
    )
    q = resp.json()
    assert q["base"] == 4000
    assert q["discount"] == 500
    assert q["final"] == 3500


@pytest.mark.asyncio
async def test_discount_capped_by_base(api_client, business, seats, master):
    """Скидка не может быть больше base (final ≥ 0)."""
    await _base_rule(api_client, hourly_rate=100)
    await api_client.post(
        "/api/discount-rules",
        json={"name": "−1000 ₸", "kind": "min_hours", "min_hours": 1, "fixed_amount": 1000},
        headers=OWNER_HEADERS,
    )
    resp = await api_client.post(
        "/api/pricing/quote",
        json={"seat_id": seats[0]["id"], "start": _at(12), "end": _at(13)},
        headers=MASTER_HEADERS,
    )
    q = resp.json()
    assert q["base"] == 100
    assert q["discount"] == 100
    assert q["final"] == 0


@pytest.mark.asyncio
async def test_discount_tiers_pick_highest_reached(api_client, business, seats, master):
    """Ступени не складываются: действует с наибольшим достигнутым порогом."""
    await _base_rule(api_client, hourly_rate=1000)
    for hours, pct in ((2, 0.05), (4, 0.10)):
        resp = await api_client.post(
            "/api/discount-rules",
            json={"name": f"от {hours}", "kind": "min_hours", "min_hours": hours, "percent": pct},
            headers=OWNER_HEADERS,
        )
        assert resp.status_code == 201, resp.text
    for end_h, expected in ((13, 0), (14, 100), (16, 400)):
        resp = await api_client.post(
            "/api/pricing/quote",
            json={"seat_id": seats[0]["id"], "start": _at(12), "end": _at(end_h)},
            headers=MASTER_HEADERS,
        )
        assert resp.json()["discount"] == expected, end_h


@pytest.mark.asyncio
async def test_discount_validation(api_client, business, seats, master):
    # без порога / оба размера сразу — 422
    resp = await api_client.post(
        "/api/discount-rules",
        json={"name": "кривая", "kind": "min_hours", "percent": 0.1},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 422
    resp = await api_client.post(
        "/api/discount-rules",
        json={"name": "кривая2", "kind": "min_hours", "min_hours": 2, "percent": 0.1, "fixed_amount": 100},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 422
    # мастер не создаёт скидки
    resp = await api_client.post(
        "/api/discount-rules",
        json={"name": "хак", "kind": "min_hours", "min_hours": 2, "percent": 0.5},
        headers=MASTER_HEADERS,
    )
    assert resp.status_code == 403
    # PATCH с неизвестным kind
    ok = await api_client.post(
        "/api/discount-rules",
        json={"name": "ok", "min_hours": 2, "percent": 0.1},
        headers=OWNER_HEADERS,
    )
    assert ok.status_code == 201
    resp = await api_client.patch(
        f"/api/discount-rules/{ok.json()['id']}", json={"kind": "resident"}, headers=OWNER_HEADERS
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_price_rule_until_midnight_and_patch_validation(api_client, business, seats, master):
    """Окно до 00:00 (полночь) допустимо; кривой day_kind в PATCH — 422; квота длиннее 7 суток — 400."""
    resp = await api_client.post(
        "/api/price-rules",
        json={"name": "Вечер", "day_kind": "все", "time_from": "00:00:00", "time_to": "00:00:00", "hourly_rate": 1000},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 201, resp.text
    rule_id = resp.json()["id"]
    resp = await api_client.post(
        "/api/pricing/quote",
        json={"seat_id": seats[0]["id"], "start": _at(22), "end": _at(23)},
        headers=MASTER_HEADERS,
    )
    assert resp.status_code == 200 and resp.json()["final"] == 1000
    # через полночь: окно «весь день» на обе даты — две части
    resp = await api_client.post(
        "/api/pricing/quote",
        json={"seat_id": seats[0]["id"], "start": _at(22), "end": _at(23, days=1)},
        headers=MASTER_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    resp = await api_client.patch(
        f"/api/price-rules/{rule_id}", json={"day_kind": "каждый"}, headers=OWNER_HEADERS
    )
    assert resp.status_code == 422
    resp = await api_client.post(
        "/api/pricing/quote",
        json={"seat_id": seats[0]["id"], "start": _at(12), "end": _at(12, days=30)},
        headers=MASTER_HEADERS,
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Интеграция с бронями
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_booking_created_with_price_snapshot(api_client, business, seats, master):
    """Бронь создаётся со снимком цены; событие «создана» хранит итог."""
    await _base_rule(api_client, hourly_rate=1000)
    resp = await api_client.post(
        "/api/bookings",
        json={"seat_id": seats[0]["id"], "start": _at(12), "end": _at(14)},
        headers=MASTER_HEADERS,
    )
    assert resp.status_code == 201, resp.text
    b = resp.json()
    snap = b["price_snapshot"]
    assert snap is not None
    assert snap["final"] == 2000
    assert snap["base"] == 2000
    assert snap["currency"] == "KZT"
    assert len(snap["parts"]) == 1

    hist = (await api_client.get(f"/api/bookings/{b['id']}/history", headers=MASTER_HEADERS)).json()
    created_ev = [e for e in hist["events"] if e["kind"] == "создана"]
    assert created_ev and created_ev[0]["details"]["price_final"] == 2000


@pytest.mark.asyncio
async def test_booking_without_price_409(api_client, business, seats, master):
    """Нет ценового правила на интервал → бронь не создаётся (409)."""
    resp = await api_client.post(
        "/api/bookings",
        json={"seat_id": seats[0]["id"], "start": _at(12), "end": _at(14)},
        headers=MASTER_HEADERS,
    )
    assert resp.status_code == 409
    assert "uncovered" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_changing_rules_does_not_affect_existing_booking(
    api_client, db_session, business, seats, master
):
    """Смена тарифа не пересчитывает существующую бронь — снимок фиксирован."""
    rule = await _base_rule(api_client, hourly_rate=1000)
    resp = await api_client.post(
        "/api/bookings",
        json={"seat_id": seats[0]["id"], "start": _at(12), "end": _at(14)},
        headers=MASTER_HEADERS,
    )
    assert resp.status_code == 201, resp.text
    bid = resp.json()["id"]
    assert resp.json()["price_snapshot"]["final"] == 2000

    # Владелец поднял цену в 5 раз
    resp = await api_client.patch(
        f"/api/price-rules/{rule['id']}", json={"hourly_rate": 5000}, headers=OWNER_HEADERS
    )
    assert resp.status_code == 200

    # Существующая бронь — по старому снимку
    b = (await api_client.get(f"/api/bookings/{bid}", headers=MASTER_HEADERS)).json()
    assert b["price_snapshot"]["final"] == 2000
    assert b["price_snapshot"]["parts"][0]["hourly_rate"] == 1000

    # А новая бронь (другое место, другое время — тот же мастер не держит
    # две пересекающиеся брони) — по новой цене
    resp = await api_client.post(
        "/api/bookings",
        json={"seat_id": seats[1]["id"], "start": _at(15), "end": _at(17)},
        headers=MASTER_HEADERS,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["price_snapshot"]["final"] == 10000


@pytest.mark.asyncio
async def test_reschedule_recalculates_price(api_client, business, seats, master):
    """Перенос на другое время → новый расчёт по актуальным правилам + события."""
    await _base_rule(api_client, hourly_rate=1000, name="День", time_to="18:00:00")
    await _base_rule(api_client, hourly_rate=3000, name="Вечер", time_from="18:00:00")
    resp = await api_client.post(
        "/api/bookings",
        json={"seat_id": seats[0]["id"], "start": _at(12), "end": _at(14)},
        headers=MASTER_HEADERS,
    )
    assert resp.status_code == 201, resp.text
    bid = resp.json()["id"]
    assert resp.json()["price_snapshot"]["final"] == 2000

    # Перенос на вечер 18:00–20:00 → 2 × 3000 = 6000
    resp = await api_client.patch(
        f"/api/bookings/{bid}",
        json={"start": _at(18), "end": _at(20)},
        headers=MASTER_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    b = resp.json()
    assert b["price_snapshot"]["final"] == 6000
    assert b["price_snapshot"]["parts"][0]["rule_name"] == "Вечер"

    hist = (await api_client.get(f"/api/bookings/{bid}/history", headers=MASTER_HEADERS)).json()
    kinds = [e["kind"] for e in hist["events"]]
    assert "перенесена" in kinds
    assert "пересчитана_цена" in kinds
    resch = next(e for e in hist["events"] if e["kind"] == "перенесена")
    assert resch["details"]["old_price_snapshot"]["final"] == 2000
    assert resch["details"]["new_price_snapshot"]["final"] == 6000


@pytest.mark.asyncio
async def test_owner_quote_with_master_id(
    api_client, business, seats, master
):
    """Владелец считает quote для конкретного мастера (master_id попадает в снимок)."""
    await _base_rule(api_client, hourly_rate=1000)
    await api_client.post(
        "/api/discount-rules",
        json={"name": "От 2 ч −50%", "kind": "min_hours", "min_hours": 2, "percent": 0.5},
        headers=OWNER_HEADERS,
    )
    resp = await api_client.post(
        "/api/pricing/quote",
        json={"seat_id": seats[0]["id"], "start": _at(12), "end": _at(14), "master_id": master.id},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    q = resp.json()
    assert q["master_id"] == master.id
    assert q["final"] == 1000
