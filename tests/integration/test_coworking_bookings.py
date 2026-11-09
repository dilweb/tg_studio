"""Интеграционные тесты коворкинга: места, календари, брони, hold/TTL, права.

Покрытие инвариантов из docs/coworking/02-seats-bookings.md:
- 4 места с отдельными календарями (создание сверх лимита → 409);
- создание брони → ожидает_оплаты + hold_deadline, событие «создана»;
- пересечение на одном месте → 409 (бронь-бронь, бронь-блокировка, блокировка всей студии);
- параллельные запросы на одно место → одна бронь (check+insert в одной транзакции);
- мастер не держит две пересекающиеся брони; владелец может;
- границы: start<end, мин. длительность, шаг, часы работы студии;
- hold истёк → истекла_оплата (сервис expire_booking_holds), место снова свободно;
- права: мастер видит/правит только свои брони, 403 на чужие; владелец — все;
- история booking_events растёт на каждое изменение.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from tg_studio.config import AllowedUser, settings
from tg_studio.db.models import (
    ACTIVE_BOOKING_STATUSES,
    Booking,
    BookingEvent,
    BookingStatus,
    UserRole,
)
from tg_studio.modules.coworking import service as cw
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
OTHER_MASTER_TG_ID = 77777
OTHER_MASTER_HEADERS = make_init_data_headers(OTHER_MASTER_TG_ID)


@pytest.fixture
async def business(db_session):
    return await make_business(db_session, owner_telegram_id=99999)


@pytest.fixture
async def master(db_session, business, monkeypatch):
    """Мастер с привязанным User — чтобы ходить под его initData."""
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
async def other_master(db_session, business, monkeypatch):
    user = await make_user(
        db_session, telegram_id=OTHER_MASTER_TG_ID, first_name="Второй", role=UserRole.master
    )
    m = await make_master(db_session, business_id=business.id, full_name="Второй")
    m.user_id = user.id
    await db_session.commit()
    monkeypatch.setattr(
        settings,
        "allowed_users",
        [
            *settings.allowed_users,
            AllowedUser(id=OTHER_MASTER_TG_ID, role="master"),
        ],
    )
    return m


@pytest.fixture
async def seats(api_client, business):
    """4 дефолтных места + базовое ценовое правило (все дни 10:00–22:00) —
    чтобы брони в тестах 02 создавались (с 03 без цены на интервал — 409)."""
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


def booking_payload(seat_id: int, start: str, end: str, **extra):
    body = {"seat_id": seat_id, "start": start, "end": end}
    body.update(extra)
    return body


# Базовый интервал «завтра 12:00–14:00 по студии» (наивное время = Asia/Almaty)
def _tomorrow(hour: int, minute: int = 0) -> str:
    day = (datetime.now(STUDIO_TZ) + timedelta(days=1)).date()
    return f"{day.isoformat()}T{hour:02d}:{minute:02d}:00"


# ---------------------------------------------------------------------------
# Места
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seats_crud_and_limit(api_client, business, master):
    # Мастер не создаёт места
    resp = await api_client.post("/api/seats", json={"name": "хак"}, headers=MASTER_HEADERS)
    assert resp.status_code == 403

    seats = []
    for i in range(4):
        resp = await api_client.post(
            "/api/seats", json={"name": f"Место {i + 1}", "equipment": "лампа"}, headers=OWNER_HEADERS
        )
        assert resp.status_code == 201, resp.text
        seats.append(resp.json())
    # 5-е место сверх лимита (дефолт max_seats=4) → 409
    resp = await api_client.post("/api/seats", json={"name": "Пятое"}, headers=OWNER_HEADERS)
    assert resp.status_code == 409

    listed = await api_client.get("/api/seats", headers=MASTER_HEADERS)
    assert listed.status_code == 200
    assert len(listed.json()["seats"]) == 4

    patch = await api_client.patch(
        f"/api/seats/{seats[0]['id']}", json={"name": "Место А", "equipment": "кресло"},
        headers=OWNER_HEADERS,
    )
    assert patch.status_code == 200
    assert patch.json()["name"] == "Место А"
    # Мастер не правит
    assert (
        await api_client.patch(f"/api/seats/{seats[0]['id']}", json={"name": "хак"}, headers=MASTER_HEADERS)
    ).status_code == 403


@pytest.mark.asyncio
async def test_seat_cannot_be_deleted_with_active_bookings(api_client, business, seats, master):
    resp = await api_client.post(
        "/api/bookings",
        json=booking_payload(seats[0]["id"], _tomorrow(12), _tomorrow(14)),
        headers=MASTER_HEADERS,
    )
    assert resp.status_code == 201, resp.text
    resp = await api_client.delete(f"/api/seats/{seats[0]['id']}", headers=OWNER_HEADERS)
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Создание брони и hold
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_booking_pending_payment_with_hold(api_client, db_session, business, seats, master):
    resp = await api_client.post(
        "/api/bookings",
        json=booking_payload(seats[0]["id"], _tomorrow(12), _tomorrow(14)),
        headers=MASTER_HEADERS,
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["status"] == BookingStatus.pending_payment.value
    assert data["hold_deadline"] is not None
    assert data["seat_name"] == "Место 1"
    assert data["master_name"] == "Мастер Тест"
    assert float(data["paid_amount"]) == 0

    events = (
        await api_client.get(f"/api/bookings/{data['id']}/history", headers=MASTER_HEADERS)
    ).json()["events"]
    assert [e["kind"] for e in events] == ["создана"]


@pytest.mark.asyncio
async def test_booking_bounds(api_client, business, seats, master):
    sid = seats[0]["id"]
    # end <= start
    resp = await api_client.post(
        "/api/bookings", json=booking_payload(sid, _tomorrow(14), _tomorrow(12)), headers=MASTER_HEADERS
    )
    assert resp.status_code == 400
    # Меньше минимальной длительности (дефолт 60 мин)
    resp = await api_client.post(
        "/api/bookings",
        json=booking_payload(sid, _tomorrow(12), _tomorrow(12, 30)),
        headers=MASTER_HEADERS,
    )
    assert resp.status_code == 400
    # Не кратно шагу (дефолт 30 мин): 12:00–13:10
    resp = await api_client.post(
        "/api/bookings",
        json=booking_payload(sid, _tomorrow(12), _tomorrow(13, 10)),
        headers=MASTER_HEADERS,
    )
    assert resp.status_code == 400
    # Вне часов работы (дефолт 10:00–22:00): 08:00–09:00
    resp = await api_client.post(
        "/api/bookings",
        json=booking_payload(sid, _tomorrow(8), _tomorrow(9)),
        headers=MASTER_HEADERS,
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_booking_outside_studio_hours_custom(api_client, business, seats, master):
    """Свое расписание: студия 12:00–20:00 → бронь 10:00–11:00 отклоняется."""

    resp = await api_client.put(
        "/api/studio-hours",
        json={
            "entries": [
                {"weekday": wd, "open_time": "12:00", "close_time": "20:00"} for wd in range(7)
            ]
        },
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    resp = await api_client.post(
        "/api/bookings",
        json=booking_payload(seats[0]["id"], _tomorrow(10), _tomorrow(11)),
        headers=MASTER_HEADERS,
    )
    assert resp.status_code == 400
    # А внутри новых часов — ок
    resp = await api_client.post(
        "/api/bookings",
        json=booking_payload(seats[0]["id"], _tomorrow(12), _tomorrow(13)),
        headers=MASTER_HEADERS,
    )
    assert resp.status_code == 201, resp.text


# ---------------------------------------------------------------------------
# Пересечения и гонки
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_overlap_same_seat_by_other_master_409(api_client, business, seats, master, other_master):
    sid = seats[0]["id"]
    resp = await api_client.post(
        "/api/bookings", json=booking_payload(sid, _tomorrow(12), _tomorrow(14)), headers=MASTER_HEADERS
    )
    assert resp.status_code == 201
    # Второй мастер на то же место и пересекающийся интервал → 409
    resp = await api_client.post(
        "/api/bookings",
        json=booking_payload(sid, _tomorrow(13), _tomorrow(15)),
        headers=OTHER_MASTER_HEADERS,
    )
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["message"] == "Место занято на этом интервале"
    assert detail["conflicts"][0]["kind"] == "booking"
    # Соседний непересекающийся интервал на том же месте — ок
    resp = await api_client.post(
        "/api/bookings",
        json=booking_payload(sid, _tomorrow(14), _tomorrow(16)),
        headers=OTHER_MASTER_HEADERS,
    )
    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_master_cannot_hold_two_overlapping_bookings(api_client, business, seats, master, other_master):
    # Первая бронь на месте 1
    resp = await api_client.post(
        "/api/bookings", json=booking_payload(seats[0]["id"], _tomorrow(12), _tomorrow(14)),
        headers=MASTER_HEADERS,
    )
    assert resp.status_code == 201
    # Вторая на ДРУГОМ месте, но пересекающееся время → 409 (мастер один)
    resp = await api_client.post(
        "/api/bookings", json=booking_payload(seats[1]["id"], _tomorrow(13), _tomorrow(15)),
        headers=MASTER_HEADERS,
    )
    assert resp.status_code == 409
    # Владелец может создать пересекающуюся бронь на другое лицо (исключение)
    resp = await api_client.post(
        "/api/bookings",
        json=booking_payload(seats[1]["id"], _tomorrow(13), _tomorrow(15), master_id=master.id),
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_parallel_requests_create_single_booking(api_client, db_session, business, seats, master):
    """N запросов подряд на одно место и интервал → ровно 1 бронь.

    check+insert идёт в одной транзакции: первый запрос коммитит бронь,
    все последующие упираются в проверку пересечений и получают 409.
    На Postgres параллельность дополнительно сериализует pg_advisory_xact_lock
    по seat_id (db/locks.lock_seat_slot) — на SQLite (тесты) lock — no-op,
    но одна транзакция за раз даёт тот же результат.
    """
    sid = seats[0]["id"]
    payload = booking_payload(sid, _tomorrow(12), _tomorrow(14))
    responses = [
        await api_client.post("/api/bookings", json=payload, headers=MASTER_HEADERS)
        for _ in range(5)
    ]
    created = [r for r in responses if r.status_code == 201]
    conflicts = [r for r in responses if r.status_code == 409]
    assert len(created) == 1
    assert len(conflicts) == 4
    rows = (
        await db_session.execute(
            select(Booking).where(
                Booking.seat_id == sid, Booking.status.in_(ACTIVE_BOOKING_STATUSES)
            )
        )
    ).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_block_conflicts_with_booking(api_client, business, seats, master):
    sid = seats[0]["id"]
    # Бронь 12–14
    assert (
        await api_client.post(
            "/api/bookings", json=booking_payload(sid, _tomorrow(12), _tomorrow(14)), headers=MASTER_HEADERS
        )
    ).status_code == 201
    # Блокировка 13–15 на том же месте → 409 (пересекается с бронью)
    resp = await api_client.post(
        "/api/blocks",
        json={"seat_id": sid, "start": _tomorrow(13), "end": _tomorrow(15), "reason": "уборка"},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 409
    # Блокировка на свободном интервале — ок
    resp = await api_client.post(
        "/api/blocks",
        json={"seat_id": sid, "start": _tomorrow(15), "end": _tomorrow(16), "reason": "уборка"},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 201, resp.text
    # Бронь, пересекающая блокировку → 409
    resp = await api_client.post(
        "/api/bookings", json=booking_payload(sid, _tomorrow(14, 30), _tomorrow(16)), headers=MASTER_HEADERS
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_studio_wide_block(api_client, business, seats, master):
    """Блокировка всей студии (seat_id=None) закрывает все 4 места."""
    resp = await api_client.post(
        "/api/blocks",
        json={"seat_id": None, "start": _tomorrow(12), "end": _tomorrow(14), "reason": "ремонт"},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 201, resp.text
    for s in seats:
        resp = await api_client.post(
            "/api/bookings", json=booking_payload(s["id"], _tomorrow(12), _tomorrow(13)), headers=MASTER_HEADERS
        )
        assert resp.status_code == 409, f"место {s['id']} должно быть заблокировано"
    # Вне блокировки — можно
    resp = await api_client.post(
        "/api/bookings", json=booking_payload(seats[0]["id"], _tomorrow(14), _tomorrow(15)),
        headers=MASTER_HEADERS,
    )
    assert resp.status_code == 201, resp.text


# ---------------------------------------------------------------------------
# Календари и доступность
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seat_and_studio_calendars(api_client, business, seats, master):
    await api_client.post(
        "/api/bookings",
        json=booking_payload(seats[1]["id"], _tomorrow(12), _tomorrow(14)),
        headers=MASTER_HEADERS,
    )
    await api_client.post(
        "/api/blocks",
        json={"seat_id": seats[2]["id"], "start": _tomorrow(18), "end": _tomorrow(19), "reason": "уборка"},
        headers=OWNER_HEADERS,
    )
    day = (datetime.now(STUDIO_TZ) + timedelta(days=1)).date()
    from_s = f"{day.isoformat()}T00:00:00"
    to_s = f"{day.isoformat()}T23:59:59"

    # Календарь места 2 (индекс 1): одна бронь
    resp = await api_client.get(
        f"/api/seats/{seats[1]['id']}/calendar?from={from_s}&to={to_s}", headers=MASTER_HEADERS
    )
    assert resp.status_code == 200, resp.text
    entries = resp.json()["entries"]
    assert len(entries) == 1 and entries[0]["kind"] == "booking" and entries[0]["seat_id"] == seats[1]["id"]

    # Общий календарь: бронь + блокировка = 2 записи
    resp = await api_client.get(f"/api/calendar?from={from_s}&to={to_s}", headers=OWNER_HEADERS)
    entries = resp.json()["entries"]
    assert len(entries) == 2
    kinds = sorted(e["kind"] for e in entries)
    assert kinds == ["block", "booking"]

    # Фильтр общего календаря по месту
    resp = await api_client.get(
        f"/api/calendar?from={from_s}&to={to_s}&seat_id={seats[2]['id']}", headers=OWNER_HEADERS
    )
    entries = resp.json()["entries"]
    assert len(entries) == 1 and entries[0]["kind"] == "block"


@pytest.mark.asyncio
async def test_availability(api_client, business, seats, master):
    # Занимаем место 1 на 12–14
    await api_client.post(
        "/api/bookings",
        json=booking_payload(seats[0]["id"], _tomorrow(12), _tomorrow(14)),
        headers=MASTER_HEADERS,
    )
    resp = await api_client.get(
        f"/api/bookings/availability?start={_tomorrow(12)}&end={_tomorrow(14)}", headers=MASTER_HEADERS
    )
    assert resp.status_code == 200
    free_ids = [s["seat_id"] for s in resp.json()["free_seats"]]
    assert seats[0]["id"] not in free_ids
    assert seats[1]["id"] in free_ids
    # На другом интервале все свободны
    resp = await api_client.get(
        f"/api/bookings/availability?start={_tomorrow(15)}&end={_tomorrow(16)}", headers=MASTER_HEADERS
    )
    assert len(resp.json()["free_seats"]) == 4


# ---------------------------------------------------------------------------
# Перенос, отмена, отметки, история
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reschedule_and_cancel(api_client, business, seats, master):
    resp = await api_client.post(
        "/api/bookings",
        json=booking_payload(seats[0]["id"], _tomorrow(12), _tomorrow(14)),
        headers=MASTER_HEADERS,
    )
    bid = resp.json()["id"]
    # Перенос на 16–18 (свободно)
    resp = await api_client.patch(
        f"/api/bookings/{bid}",
        json={"start": _tomorrow(16), "end": _tomorrow(18)},
        headers=MASTER_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["start"].startswith(_tomorrow(16)[:16])
    # Перенос на занятый интервал другого места → сначала займём место 2
    await api_client.post(
        "/api/bookings",
        json=booking_payload(seats[1]["id"], _tomorrow(12), _tomorrow(14), master_id=master.id),
        headers=OWNER_HEADERS,
    )
    # Перенос нашей брони на место 1 в 12–14 — место 1 свободно (мы ушли), но у мастера уже есть бронь 12–14 → 409
    resp = await api_client.patch(
        f"/api/bookings/{bid}",
        json={"start": _tomorrow(12), "end": _tomorrow(14)},
        headers=MASTER_HEADERS,
    )
    assert resp.status_code == 409
    # Отмена
    resp = await api_client.post(
        f"/api/bookings/{bid}/cancel", json={"reason": "не смог"}, headers=MASTER_HEADERS
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == BookingStatus.cancelled_by_master.value
    # Повторная отмена → 409
    resp = await api_client.post(f"/api/bookings/{bid}/cancel", json={}, headers=MASTER_HEADERS)
    assert resp.status_code == 409
    # История: создана → перенесена → (409 не пишется) → отменена
    events = (
        await api_client.get(f"/api/bookings/{bid}/history", headers=MASTER_HEADERS)
    ).json()["events"]
    # перенос теперь добавляет событие «пересчитана_цена» (03)
    assert [e["kind"] for e in events] == ["создана", "перенесена", "пересчитана_цена", "отменена"]
    # Отменённая бронь освобождает место: тот же интервал снова доступен
    resp = await api_client.get(
        f"/api/bookings/availability?start={_tomorrow(16)}&end={_tomorrow(18)}", headers=MASTER_HEADERS
    )
    assert seats[0]["id"] in [s["seat_id"] for s in resp.json()["free_seats"]]


@pytest.mark.asyncio
async def test_owner_marks_start_complete_no_show(api_client, business, seats, master, db_session):
    resp = await api_client.post(
        "/api/bookings",
        json=booking_payload(seats[0]["id"], _tomorrow(12), _tomorrow(14)),
        headers=MASTER_HEADERS,
    )
    bid = resp.json()["id"]
    # Начать можно только подтверждённую — пока ожидает_оплаты → 409
    resp = await api_client.post(f"/api/bookings/{bid}/start", headers=OWNER_HEADERS)
    assert resp.status_code == 409
    # Подтвердим напрямую в БД (оплата придёт в 04)
    booking = (await db_session.execute(select(Booking).where(Booking.id == bid))).scalar_one()
    booking.status = BookingStatus.confirmed.value
    await db_session.commit()

    resp = await api_client.post(f"/api/bookings/{bid}/start", headers=OWNER_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["status"] == "начата"
    assert resp.json()["started_at"] is not None
    resp = await api_client.post(f"/api/bookings/{bid}/complete", headers=OWNER_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["status"] == "завершена"
    assert resp.json()["completed_at"] is not None

    # Неявка по другой брони
    resp = await api_client.post(
        "/api/bookings",
        json=booking_payload(seats[1]["id"], _tomorrow(15), _tomorrow(16), master_id=master.id),
        headers=OWNER_HEADERS,
    )
    bid2 = resp.json()["id"]
    resp = await api_client.post(f"/api/bookings/{bid2}/no-show", headers=OWNER_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["status"] == "неявка"
    # Мастер не может отмечать
    resp = await api_client.post(f"/api/bookings/{bid2}/start", headers=MASTER_HEADERS)
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Hold TTL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hold_expiry_frees_seat(api_client, db_session, business, seats, master, other_master):
    resp = await api_client.post(
        "/api/bookings",
        json=booking_payload(seats[0]["id"], _tomorrow(12), _tomorrow(14)),
        headers=MASTER_HEADERS,
    )
    bid = resp.json()["id"]
    # Имитируем истёкший hold
    booking = (await db_session.execute(select(Booking).where(Booking.id == bid))).scalar_one()
    booking.hold_deadline = datetime.now(UTC) - timedelta(minutes=1)
    await db_session.commit()

    expired = await cw.expire_booking_holds(db_session)
    await db_session.commit()
    assert expired == 1

    await db_session.refresh(booking)
    assert booking.status == BookingStatus.payment_expired.value
    events = (
        await db_session.execute(select(BookingEvent).where(BookingEvent.booking_id == bid))
    ).scalars().all()
    assert [e.kind for e in events] == ["создана", "истекла_оплата"]

    # Место снова свободно на этом интервале (другой мастер)
    resp = await api_client.post(
        "/api/bookings",
        json=booking_payload(seats[0]["id"], _tomorrow(12), _tomorrow(14)),
        headers=OTHER_MASTER_HEADERS,
    )
    assert resp.status_code == 201, resp.text


# ---------------------------------------------------------------------------
# Права
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_master_sees_only_own_bookings(api_client, business, seats, master, other_master):
    # Свои брони: master на месте 1, other_master на месте 2
    await api_client.post(
        "/api/bookings",
        json=booking_payload(seats[0]["id"], _tomorrow(12), _tomorrow(13)),
        headers=MASTER_HEADERS,
    )
    await api_client.post(
        "/api/bookings",
        json=booking_payload(seats[1]["id"], _tomorrow(15), _tomorrow(16)),
        headers=OTHER_MASTER_HEADERS,
    )
    mine = (await api_client.get("/api/bookings", headers=MASTER_HEADERS)).json()["bookings"]
    assert len(mine) == 1 and mine[0]["master_name"] == "Мастер Тест"
    all_b = (await api_client.get("/api/bookings", headers=OWNER_HEADERS)).json()["bookings"]
    assert len(all_b) == 2
    # Мастер не видит чужую бронь по id
    foreign_id = [b["id"] for b in all_b if b["master_name"] == "Второй"][0]
    assert (await api_client.get(f"/api/bookings/{foreign_id}", headers=MASTER_HEADERS)).status_code == 403
    assert (
        await api_client.patch(
            f"/api/bookings/{foreign_id}",
            json={"start": _tomorrow(17), "end": _tomorrow(18)},
            headers=MASTER_HEADERS,
        )
    ).status_code == 403
    assert (
        await api_client.post(f"/api/bookings/{foreign_id}/cancel", json={}, headers=MASTER_HEADERS)
    ).status_code == 403
    # Мастер не создаёт бронь за другого мастера (master_id игнорируется в пользу себя)
    resp = await api_client.post(
        "/api/bookings",
        json=booking_payload(seats[2]["id"], _tomorrow(18), _tomorrow(19), master_id=other_master.id),
        headers=MASTER_HEADERS,
    )
    assert resp.status_code == 201
    assert resp.json()["master_name"] == "Мастер Тест"


@pytest.mark.asyncio
async def test_settings_owner_only(api_client, business, master):
    resp = await api_client.get("/api/coworking/settings", headers=MASTER_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["max_seats"] == 4
    assert (
        await api_client.patch("/api/coworking/settings", json={"max_seats": 6}, headers=MASTER_HEADERS)
    ).status_code == 403
    resp = await api_client.patch(
        "/api/coworking/settings", json={"min_duration_minutes": 30, "step_minutes": 15},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["min_duration_minutes"] == 30
    assert resp.json()["step_minutes"] == 15
    # max_seats=2 меньше активных мест? мест пока 0 → ок; но 1 тоже ок
    resp = await api_client.patch("/api/coworking/settings", json={"max_seats": 2}, headers=OWNER_HEADERS)
    assert resp.status_code == 200
