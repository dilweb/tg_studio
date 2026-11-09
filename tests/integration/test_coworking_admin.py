"""Интеграционные тесты коворкинга 05: роль admin, администраторы, смены/покрытие,
посещения по броням, апсейлы и выплаты, операционные исключения.

Покрытие docs/coworking/05-admin-upsells.md:
- роль admin резолвится из БД (StudioAdmin по telegram_id), видит студию целиком;
- владелец назначает/снимает админов (POST/DELETE /api/admins), повтор — 409;
- админ создаёт брони за мастера, отмечает начало/завершение/неявку и визиты;
- бронь вне покрытия смен → requires_confirmation, владелец подтверждает, /ops/exceptions;
- смены: план/факт/утверждение часов, админ — только свои;
- апсейл создаётся с автором-админом; доначисление по правилу % от апсейлов за период
  или разовое с основанием; статусы accrued → paid;
- админ подтверждает ручные оплаты (mark-paid) счетов за брони, видит все платежи студии.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from tg_studio.db.models import (
    Booking,
    BookingEvent,
    BookingStatus,
    LedgerEntry,
    LedgerEntryKind,
    Payment,
    PaymentStatus,
    Payout,
    PayoutStatus,
    Shift,
    StudioAdmin,
    Upsell,
    UserRole,
)
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
ADMIN_TG_ID = 66666
ADMIN_HEADERS = make_init_data_headers(ADMIN_TG_ID)
OTHER_ADMIN_TG_ID = 55555


def _at(days_ahead: int, hour: int, minute: int = 0) -> str:
    """Наивное локальное (студийное) время через N дней — как шлёт фронтенд."""
    d = datetime.now(STUDIO_TZ) + timedelta(days=days_ahead)
    return f"{d.date().isoformat()}T{hour:02d}:{minute:02d}:00"


@pytest.fixture
async def business(db_session):
    return await make_business(db_session, owner_telegram_id=99999)


@pytest.fixture
async def master(db_session, business):
    user = await make_user(
        db_session, telegram_id=MASTER_TG_ID, first_name="Мастер Тест", role=UserRole.master
    )
    m = await make_master(
        db_session, business_id=business.id, full_name="Мастер Тест", telegram_id=MASTER_TG_ID
    )
    m.user_id = user.id
    await db_session.commit()
    return m


@pytest.fixture
async def admin(db_session, business):
    """Администратор студии: User(role=admin) + StudioAdmin — вход по initData
    резолвится в роль admin через resolve_admin_role (без правок env)."""
    user = await make_user(
        db_session, telegram_id=ADMIN_TG_ID, first_name="Админ Тест", role=UserRole.admin
    )
    a = StudioAdmin(
        business_id=business.id,
        user_id=user.id,
        telegram_id=ADMIN_TG_ID,
        full_name="Админ Тест",
    )
    db_session.add(a)
    await db_session.commit()
    return a


@pytest.fixture
async def seats(api_client, business):
    """4 места + базовое ценовое правило 10:00–22:00 @1000/ч."""
    created = []
    for i in range(4):
        resp = await api_client.post(
            "/api/seats", json={"name": f"Место {i + 1}"}, headers=OWNER_HEADERS
        )
        assert resp.status_code == 201, resp.text
        created.append(resp.json())
    resp = await api_client.post(
        "/api/price-rules",
        json={
            "seat_id": None,
            "name": "Базовый тариф",
            "day_kind": "все",
            "time_from": "10:00:00",
            "time_to": "22:00:00",
            "hourly_rate": 1000,
            "priority": 0,
        },
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 201, resp.text
    return created


async def _make_confirmed_booking(api_client, seats, master, seat_idx: int = 0, start=None, end=None) -> dict:
    """Бронь → пополнение баланса мастера → оплата с баланса → подтверждена.

    Возвращает dict брони (GET после оплаты — у pay-ответа нет полей брони)."""
    start = start or _at(1, 12)
    end = end or _at(1, 14)
    resp = await api_client.post(
        "/api/bookings",
        json={"seat_id": seats[seat_idx]["id"], "start": start, "end": end, "master_id": master.id},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 201, resp.text
    b = resp.json()
    resp = await api_client.post(
        f"/api/masters/{master.id}/topup", json={"amount": 100000}, headers=OWNER_HEADERS
    )
    assert resp.status_code == 201, resp.text
    resp = await api_client.post(
        f"/api/bookings/{b['id']}/pay", json={"method": "balance"}, headers=MASTER_HEADERS
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == BookingStatus.confirmed.value
    r = await api_client.get(f"/api/bookings/{b['id']}", headers=OWNER_HEADERS)
    assert r.status_code == 200
    return r.json()


# ---------------------------------------------------------------------------
# Роль и права
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_role_and_visibility(api_client, db_session, business, seats, master, admin):
    # Админ видит студию целиком: места, все брони, журнал, балансы мастеров
    assert (await api_client.get("/api/seats", headers=ADMIN_HEADERS)).status_code == 200
    assert (await api_client.get("/api/bookings", headers=ADMIN_HEADERS)).status_code == 200
    assert (await api_client.get("/api/ledger", headers=ADMIN_HEADERS)).status_code == 200
    r = await api_client.get(f"/api/masters/{master.id}/balance", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    assert r.json()["balance"] == 0

    # ...но не управляет студией: места/админы/настройки — только владелец
    assert (await api_client.post("/api/seats", json={"name": "хак"}, headers=ADMIN_HEADERS)).status_code == 403
    assert (await api_client.get("/api/admins", headers=ADMIN_HEADERS)).status_code == 403
    assert (
        await api_client.patch("/api/coworking/settings", json={"max_seats": 2}, headers=ADMIN_HEADERS)
    ).status_code == 403
    # Мастер не видит админские разделы
    assert (await api_client.get("/api/ops/exceptions", headers=MASTER_HEADERS)).status_code == 403
    assert (await api_client.get("/api/shifts", headers=MASTER_HEADERS)).status_code == 403
    assert (await api_client.get("/api/upsells", headers=MASTER_HEADERS)).status_code == 403


@pytest.mark.asyncio
async def test_admins_crud_by_owner(api_client, db_session, business, admin):
    # Повторный админ с тем же telegram_id → 409
    resp = await api_client.post(
        "/api/admins",
        json={"telegram_id": ADMIN_TG_ID, "full_name": "Другой"},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 409

    # Новый админ: создаётся StudioAdmin + User с ролью admin
    resp = await api_client.post(
        "/api/admins",
        json={"telegram_id": OTHER_ADMIN_TG_ID, "full_name": "Второй Админ"},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["telegram_id"] == OTHER_ADMIN_TG_ID
    user = (
        await db_session.execute(
            select(__import__("tg_studio.db.models", fromlist=["User"]).User).where(
                __import__("tg_studio.db.models", fromlist=["User"]).User.telegram_id == OTHER_ADMIN_TG_ID
            )
        )
    ).scalar_one()
    assert user.role == UserRole.admin

    # Вход по initData этого tg → роль admin, доступ в панель
    headers = make_init_data_headers(OTHER_ADMIN_TG_ID, first_name="Второй Админ")
    r = await api_client.get("/api/seats", headers=headers)
    assert r.status_code == 200

    # Список и снятие
    admins_list = (await api_client.get("/api/admins", headers=OWNER_HEADERS)).json()["admins"]
    assert len(admins_list) == 2
    resp = await api_client.delete(f"/api/admins/{created['id']}", headers=OWNER_HEADERS)
    assert resp.status_code == 204
    # После снятия initData больше не резолвится в admin → 403
    r = await api_client.get("/api/seats", headers=headers)
    assert r.status_code == 403
    # Мастер не может назначать админов
    resp = await api_client.post(
        "/api/admins", json={"telegram_id": 1, "full_name": "x"}, headers=MASTER_HEADERS
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Брони и отметки админа
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_creates_booking_for_master_and_marks_lifecycle(
    api_client, db_session, business, seats, master, admin
):
    # Админ создаёт бронь за мастера (master_id обязателен)
    resp = await api_client.post(
        "/api/bookings",
        json={
            "seat_id": seats[0]["id"],
            "start": _at(1, 12),
            "end": _at(1, 14),
            "master_id": master.id,
        },
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 201, resp.text
    b = resp.json()
    # Без master_id админ не создаёт
    resp = await api_client.post(
        "/api/bookings",
        json={"seat_id": seats[0]["id"], "start": _at(2, 12), "end": _at(2, 14)},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 400

    # Оплата с баланса мастером → подтверждена
    await api_client.post(
        f"/api/masters/{master.id}/topup", json={"amount": 10000}, headers=OWNER_HEADERS
    )
    resp = await api_client.post(
        f"/api/bookings/{b['id']}/pay", json={"method": "balance"}, headers=MASTER_HEADERS
    )
    assert resp.status_code == 200

    # Админ отмечает начало → визиты → завершение
    resp = await api_client.post(f"/api/bookings/{b['id']}/start", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["status"] == BookingStatus.started.value
    assert resp.json()["started_at"]

    resp = await api_client.post(f"/api/bookings/{b['id']}/visit/start", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["visit_started_at"]
    # Повторный приход — 409
    resp = await api_client.post(f"/api/bookings/{b['id']}/visit/start", headers=ADMIN_HEADERS)
    assert resp.status_code == 409

    resp = await api_client.post(f"/api/bookings/{b['id']}/visit/end", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["visit_ended_at"]

    resp = await api_client.post(f"/api/bookings/{b['id']}/complete", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["status"] == BookingStatus.completed.value

    # История: события визитов с автором-админом
    events = (
        await api_client.get(f"/api/bookings/{b['id']}/history", headers=ADMIN_HEADERS)
    ).json()["events"]
    kinds = [e["kind"] for e in events]
    assert "пришёл" in kinds and "ушёл" in kinds and "начата" in kinds and "завершена" in kinds
    visit_events = [e for e in events if e["kind"] == "пришёл"]
    assert visit_events[0]["actor_user_id"] == admin.user_id


@pytest.mark.asyncio
async def test_visit_marks_validation(api_client, db_session, business, seats, master, admin):
    resp = await api_client.post(
        "/api/bookings",
        json={
            "seat_id": seats[0]["id"],
            "start": _at(1, 12),
            "end": _at(1, 14),
            "master_id": master.id,
        },
        headers=OWNER_HEADERS,
    )
    b = resp.json()
    # Приход по неоплаченной брони — 409
    resp = await api_client.post(f"/api/bookings/{b['id']}/visit/start", headers=ADMIN_HEADERS)
    assert resp.status_code == 409

    # уход без прихода — 409 (на другой подтверждённой брони, другое место и время)
    b2 = await _make_confirmed_booking(
        api_client, seats, master, seat_idx=1, start=_at(1, 16), end=_at(1, 18)
    )
    resp = await api_client.post(f"/api/bookings/{b2['id']}/visit/end", headers=ADMIN_HEADERS)
    assert resp.status_code == 409

    # Неявка админом
    resp = await api_client.post(f"/api/bookings/{b2['id']}/no-show", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["status"] == BookingStatus.no_show.value


# ---------------------------------------------------------------------------
# Смены и покрытие
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shifts_flow_and_roles(api_client, db_session, business, admin):
    # Админ создаёт смену на себя
    resp = await api_client.post(
        "/api/shifts",
        json={
            "admin_id": admin.id,
            "planned_start": _at(1, 10),
            "planned_end": _at(1, 18),
            "note": "дневная",
        },
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 201, resp.text
    s = resp.json()
    assert s["admin_id"] == admin.id

    # Смена на другого админа — 403 ( создаём второго админа владельцем )
    resp = await api_client.post(
        "/api/admins",
        json={"telegram_id": OTHER_ADMIN_TG_ID, "full_name": "Второй"},
        headers=OWNER_HEADERS,
    )
    other_admin_id = resp.json()["id"]
    resp = await api_client.post(
        "/api/shifts",
        json={"admin_id": other_admin_id, "planned_start": _at(1, 10), "planned_end": _at(1, 12)},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 403

    # Владелец создаёт смену на админа
    resp = await api_client.post(
        "/api/shifts",
        json={"admin_id": admin.id, "planned_start": _at(2, 10), "planned_end": _at(2, 14)},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 201

    # Факт: start → end; повторы — 409
    resp = await api_client.post(f"/api/shifts/{s['id']}/start", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["started_at"]
    assert (await api_client.post(f"/api/shifts/{s['id']}/start", headers=ADMIN_HEADERS)).status_code == 409
    # end до start — 409 (на второй смене, ещё не начатой)
    my_shifts = (await api_client.get("/api/shifts", headers=ADMIN_HEADERS)).json()["shifts"]
    s2 = next(x for x in my_shifts if x["id"] != s["id"])
    assert (await api_client.post(f"/api/shifts/{s2['id']}/end", headers=ADMIN_HEADERS)).status_code == 409
    resp = await api_client.post(f"/api/shifts/{s['id']}/end", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["ended_at"]

    # Утверждение часов — только владелец
    assert (
        await api_client.patch(f"/api/shifts/{s['id']}/approve", json={"approved_hours": 7.5}, headers=ADMIN_HEADERS)
    ).status_code == 403
    resp = await api_client.patch(
        f"/api/shifts/{s['id']}/approve", json={"approved_hours": 7.5}, headers=OWNER_HEADERS
    )
    assert resp.status_code == 200
    assert resp.json()["approved_hours"] == 7.5

    # Списки: админ видит только свои смены, владелец — все
    admin_shifts = (await api_client.get("/api/shifts", headers=ADMIN_HEADERS)).json()["shifts"]
    assert all(x["admin_id"] == admin.id for x in admin_shifts)
    owner_shifts = (await api_client.get("/api/shifts", headers=OWNER_HEADERS)).json()["shifts"]
    assert len(owner_shifts) >= 2


@pytest.mark.asyncio
async def test_coverage_and_out_of_coverage_flag(api_client, db_session, business, seats, master, admin):
    # Смена админа завтра 10:00–14:00
    resp = await api_client.post(
        "/api/shifts",
        json={"admin_id": admin.id, "planned_start": _at(1, 10), "planned_end": _at(1, 14)},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 201
    shift_id = resp.json()["id"]
    await api_client.post(f"/api/shifts/{shift_id}/start", headers=ADMIN_HEADERS)

    # Покрытие на завтра: 4 часа
    day = (datetime.now(STUDIO_TZ) + timedelta(days=1)).date().isoformat()
    r = await api_client.get(
        "/api/coverage", params={"from": f"{day}T00:00:00", "to": f"{day}T23:59:00"}, headers=ADMIN_HEADERS
    )
    assert r.status_code == 200
    assert r.json()["hours"] == 4.0
    assert len(r.json()["intervals"]) == 1

    # Бронь внутри покрытия → без флага; вне покрытия → requires_confirmation
    resp = await api_client.post(
        "/api/bookings",
        json={"seat_id": seats[0]["id"], "start": _at(1, 11), "end": _at(1, 12), "master_id": master.id},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 201
    assert resp.json()["requires_confirmation"] is False

    resp = await api_client.post(
        "/api/bookings",
        json={"seat_id": seats[0]["id"], "start": _at(1, 15), "end": _at(1, 16), "master_id": master.id},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 201
    b_out = resp.json()
    assert b_out["requires_confirmation"] is True

    # Подтверждает только владелец
    assert (
        await api_client.post(f"/api/bookings/{b_out['id']}/confirm", headers=ADMIN_HEADERS)
    ).status_code == 403
    resp = await api_client.post(f"/api/bookings/{b_out['id']}/confirm", headers=OWNER_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["confirmed_by_user_id"] is not None
    # Повтор — 409
    assert (await api_client.post(f"/api/bookings/{b_out['id']}/confirm", headers=OWNER_HEADERS)).status_code == 409

    # Исключение висит в /ops/exceptions, пока не подтверждено
    resp = await api_client.post(
        "/api/bookings",
        json={"seat_id": seats[1]["id"], "start": _at(1, 15), "end": _at(1, 16), "master_id": master.id},
        headers=OWNER_HEADERS,
    )
    b_out2 = resp.json()
    ex = (await api_client.get("/api/ops/exceptions", headers=ADMIN_HEADERS)).json()["exceptions"]
    kinds = {(x["kind"], x["booking_id"]) for x in ex}
    assert ("out_of_coverage_unconfirmed", b_out2["id"]) in kinds
    assert ("out_of_coverage_unconfirmed", b_out["id"]) not in kinds  # уже подтверждена


# ---------------------------------------------------------------------------
# Апсейлы и выплаты
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsells_created_with_admin_author(api_client, db_session, business, seats, master, admin):
    # Админ фиксирует апсейл — автор он сам
    resp = await api_client.post(
        "/api/upsells",
        json={"amount": 2000, "buyer_name": "Гость", "buyer_phone": "+7 777 000 00 00", "note": "Gloves 2 шт"},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 201, resp.text
    u = resp.json()
    assert u["admin_id"] == admin.id
    assert u["admin_name"] == "Админ Тест"
    assert u["amount"] == 2000

    # Апсейл мастеру
    resp = await api_client.post(
        "/api/upsells", json={"amount": 1500, "master_id": master.id}, headers=ADMIN_HEADERS
    )
    assert resp.status_code == 201

    # Владелец фиксирует апсейл на админа (с явным admin_id)
    resp = await api_client.post(
        "/api/upsells", json={"admin_id": admin.id, "amount": 500, "note": "доп. позиция"},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 201
    # Владелец без admin_id — 400
    resp = await api_client.post("/api/upsells", json={"amount": 100}, headers=OWNER_HEADERS)
    assert resp.status_code == 400
    # Мастер не создаёт апсейлы — 403
    resp = await api_client.post("/api/upsells", json={"amount": 100}, headers=MASTER_HEADERS)
    assert resp.status_code == 403

    # Списки: админ видит только свои, владелец — все
    mine = (await api_client.get("/api/upsells", headers=ADMIN_HEADERS)).json()["upsells"]
    assert all(x["admin_id"] == admin.id for x in mine)
    assert len(mine) == 3  # все три апсейла — на этого админа
    allu = (await api_client.get("/api/upsells", headers=OWNER_HEADERS)).json()["upsells"]
    assert len(allu) == 3

    # Сводка по админам за период (владелец)
    day_from = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    day_to = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    r = await api_client.get(
        "/api/upsells/summary", params={"from": day_from, "to": day_to}, headers=OWNER_HEADERS
    )
    assert r.status_code == 200, r.text
    assert r.json()[0]["admin_id"] == admin.id
    assert r.json()[0]["total_amount"] == 4000
    assert r.json()[0]["count"] == 3
    # Сводку админ не видит (владелец-only)
    assert (
        await api_client.get(
            "/api/upsells/summary", params={"from": day_from, "to": day_to}, headers=ADMIN_HEADERS
        )
    ).status_code == 403


@pytest.mark.asyncio
async def test_payouts_rule_percent_and_mark_paid(api_client, db_session, business, admin):
    # Два апсейла по 2000 → 10% за период = 400
    for _ in range(2):
        resp = await api_client.post("/api/upsells", json={"amount": 2000}, headers=ADMIN_HEADERS)
        assert resp.status_code == 201

    day_from = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    day_to = (datetime.now(UTC) + timedelta(days=1)).isoformat()

    # Ровно одно из amount/percent
    resp = await api_client.post(
        "/api/payouts",
        json={"admin_id": admin.id, "amount": 100, "percent": 0.1, "from": day_from, "to": day_to, "reason": "оба"},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 422
    # percent без периода — 422
    resp = await api_client.post(
        "/api/payouts", json={"admin_id": admin.id, "percent": 0.1, "reason": "нет периода"},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 422
    # Основание обязательно
    resp = await api_client.post(
        "/api/payouts", json={"admin_id": admin.id, "amount": 100, "reason": " "}, headers=OWNER_HEADERS
    )
    assert resp.status_code == 422

    # Доначисление по правилу % от апсейлов за период
    resp = await api_client.post(
        "/api/payouts",
        json={"admin_id": admin.id, "percent": 0.1, "from": day_from, "to": day_to, "reason": "10% за сегодня"},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 201, resp.text
    p = resp.json()
    assert p["status"] == PayoutStatus.accrued.value
    assert p["amount"] == 400.0
    assert p["percent"] == 0.1

    # Разовое доначисление с основанием
    resp = await api_client.post(
        "/api/payouts", json={"admin_id": admin.id, "amount": 500, "reason": "разовый бонус"},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 201
    assert resp.json()["amount"] == 500.0

    # Админ не создаёт доначисления, но видит свои
    assert (
        await api_client.post("/api/payouts", json={"admin_id": admin.id, "amount": 1, "reason": "x"}, headers=ADMIN_HEADERS)
    ).status_code == 403
    mine = (await api_client.get("/api/payouts", headers=ADMIN_HEADERS)).json()["payouts"]
    assert len(mine) == 2
    owner_all = (await api_client.get("/api/payouts", headers=OWNER_HEADERS)).json()["payouts"]
    assert len(owner_all) == 2

    # Отметка выплаты — владелец; повтор — 409
    resp = await api_client.post(f"/api/payouts/{p['id']}/mark-paid", headers=OWNER_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["status"] == PayoutStatus.paid.value
    assert (await api_client.post(f"/api/payouts/{p['id']}/mark-paid", headers=OWNER_HEADERS)).status_code == 409
    assert (
        await api_client.post(f"/api/payouts/{p['id']}/mark-paid", headers=ADMIN_HEADERS)
    ).status_code == 403

    # Фильтр по статусу
    paid = (await api_client.get("/api/payouts?status=paid", headers=OWNER_HEADERS)).json()["payouts"]
    assert len(paid) == 1 and paid[0]["id"] == p["id"]


# ---------------------------------------------------------------------------
# Оплаты: админ принимает ручные платежи за брони
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_marks_booking_invoice_paid(api_client, db_session, business, seats, master, admin):
    resp = await api_client.post(
        "/api/bookings",
        json={"seat_id": seats[0]["id"], "start": _at(1, 12), "end": _at(1, 14), "master_id": master.id},
        headers=OWNER_HEADERS,
    )
    b = resp.json()
    payment_id = b["payment"]["id"]
    assert b["payment"]["status"] == PaymentStatus.pending.value

    # Мастер другого бронирования не трогает чужой счёт — создадим второго мастера
    from ..conftest import make_user as _mk

    other_user = await _mk(db_session, telegram_id=77777, first_name="Чужой", role=UserRole.master)
    other_master = await make_master(
        db_session, business_id=business.id, full_name="Чужой", telegram_id=77777
    )
    other_master.user_id = other_user.id
    await db_session.commit()
    other_headers = make_init_data_headers(77777)
    resp = await api_client.post(f"/api/payments/{payment_id}/mark-paid", headers=other_headers)
    assert resp.status_code == 404

    # Админ подтверждает ручную оплату → бронь подтверждена, проводка topup
    resp = await api_client.post(f"/api/payments/{payment_id}/mark-paid", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["status"] == PaymentStatus.paid.value

    await db_session.refresh(b) if False else None
    booking = (await db_session.execute(select(Booking).where(Booking.id == b["id"]))).scalar_one()
    assert booking.status == BookingStatus.confirmed.value
    assert booking.paid_amount == 2000
    entry = (
        await db_session.execute(
            select(LedgerEntry).where(
                LedgerEntry.payment_id == payment_id, LedgerEntry.kind == LedgerEntryKind.topup.value
            )
        )
    ).scalar_one()
    assert entry.actor_user_id == admin.user_id

    # Админ видит все платежи студии в списке
    r = await api_client.get("/api/payments", headers=ADMIN_HEADERS)
    assert r.status_code == 200
    assert any(p["id"] == payment_id for p in r.json()["payments"])


@pytest.mark.asyncio
async def test_ops_exceptions_lists_no_show(api_client, db_session, business, seats, master, admin):
    b = await _make_confirmed_booking(api_client, seats, master)
    resp = await api_client.post(f"/api/bookings/{b['id']}/no-show", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    ex = (await api_client.get("/api/ops/exceptions", headers=ADMIN_HEADERS)).json()["exceptions"]
    assert any(x["kind"] == "no_show" and x["booking_id"] == b["id"] for x in ex)
    # Мастер не видит ops-исключения
    assert (await api_client.get("/api/ops/exceptions", headers=MASTER_HEADERS)).status_code == 403
