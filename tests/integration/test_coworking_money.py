"""Интеграционные тесты денег коворкинга (docs/coworking/04-money-prepay.md).

Покрытие инвариантов:
- создание брони → счёт (Payment pending, booking_id) на 100% цены + бронь
  ожидает_оплаты; повторный create не плодит дубли счетов (идемпотентность);
- оплата с баланса: нужен остаток (topup), charge в пределах остатка,
  бронь → подтверждена, paid_amount == final, событие «оплачена»;
- недостаточно средств → 409, баланс не уходит в минус;
- вебхук apipay (HMAC + дедуп по external id + сверка суммы) → topup +
  бронь подтверждена; повторный вебхук — no-op, баланс не дублируется;
- вебхук с чужой суммой → статус не применяется;
- hold истёк → истекла_оплата + счёт expired, место свободно;
- отмена подтверждённой брони ≥24ч до начала → refund 100% на баланс;
- отмена позже 24ч → удержание no_show_hold_percent (дефолт 100% → refund 0);
- отмена площадкой → всегда 100% возврат;
- неявка → удержание по правилу;
- баланс = Σ проводок (вычисляется, не хранится): сходится с журналом;
- ручное пополнение (topup) и корректировка (adjust, основание обязательно);
- права: мастер видит только свой баланс/выписку; владелец — журнал всех.
"""

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from tg_studio.config import AllowedUser, settings
from tg_studio.db.models import (
    Booking,
    BookingEvent,
    BookingStatus,
    LedgerEntry,
    Payment,
    PaymentStatus,
    UserRole,
)
from tg_studio.modules.coworking import money
from tg_studio.modules.coworking.service import expire_booking_holds
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
WEBHOOK_SECRET = "whsec-cw-test"


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
    m.phone = "87771234567"
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
        [*settings.allowed_users, AllowedUser(id=OTHER_MASTER_TG_ID, role="master")],
    )
    return m


@pytest.fixture
async def seats(api_client, business):
    """4 места + базовый тариф 10:00–22:00 по 1000 ₸/ч."""
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


def _at(day_offset: int, hour: int, minute: int = 0) -> str:
    """Наивная ISO-строка времени студии (Asia/Almaty) через day_offset дней."""
    base = datetime.now(STUDIO_TZ).replace(hour=hour, minute=minute, second=0, microsecond=0)
    d = base + timedelta(days=day_offset)
    return d.strftime("%Y-%m-%dT%H:%M:%S")


def _tomorrow(h: int, m: int = 0) -> str:
    return _at(1, h, m)


async def _create_booking(api_client, seat_id: int, start: str, end: str, headers=MASTER_HEADERS, **extra):
    body = {"seat_id": seat_id, "start": start, "end": end}
    body.update(extra)
    resp = await api_client.post("/api/bookings", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _events(db_session, booking_id: int) -> list[str]:
    rows = (
    await db_session.execute(
            select(BookingEvent).where(BookingEvent.booking_id == booking_id).order_by(BookingEvent.id)
    )
    ).scalars().all()
    return [e.kind for e in rows]


async def _balance(db_session, master_id: int) -> Decimal:
    return await money.master_balance(db_session, master_id)


def signed_headers(body: bytes, secret: str = WEBHOOK_SECRET) -> dict:
    return {"X-Webhook-Signature": "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()}


def webhook_body(invoice_id: str, status: str = "paid", amount=None) -> bytes:
    invoice = {"id": invoice_id, "status": status}
    if amount is not None:
        invoice["amount"] = amount
    return json.dumps({"event": "invoice.status_changed", "invoice": invoice}).encode()


@pytest.fixture
def apipay_on(monkeypatch):
    """Apipay «подключён»: ключ + секрет вебхука. Провайдер НЕ вызываем
    (create_invoice ходит в сеть) — для вебхук-тестов счета создаём вручную."""
    monkeypatch.setattr(settings, "apipay_api_key", "test-key")
    monkeypatch.setattr(settings, "apipay_webhook_secret", WEBHOOK_SECRET)


# ---------------------------------------------------------------------------
# Создание брони → счёт на 100% цены
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_booking_creates_invoice(api_client, db_session, seats, master):
    """POST /bookings → бронь ожидает_оплаты + Payment pending на final."""
    b = await _create_booking(api_client, seats[0]["id"], _tomorrow(12), _tomorrow(14))
    assert b["status"] == "ожидает_оплаты"
    assert b["price_snapshot"]["final"] == 2000  # 2ч × 1000
    assert b["payment"] is not None
    assert b["payment"]["amount"] == 2000
    assert b["payment"]["status"] == "pending"
    # В БД счёт привязан к брони
    payment = (
    await db_session.execute(select(Payment).where(Payment.booking_id == b["id"]))
    ).scalar_one()
    assert payment.status == PaymentStatus.pending.value
    assert Decimal(str(payment.amount)) == Decimal("2000")


@pytest.mark.asyncio
async def test_booking_invoice_idempotent(api_client, db_session, seats, master):
    """Повторный вызов create_booking_invoice не плодит дубли (сервис)."""
    b = await _create_booking(api_client, seats[0]["id"], _tomorrow(12), _tomorrow(14))
    booking = await db_session.get(Booking, b["id"])
    business = booking.business if hasattr(booking, "business") else None
    from tg_studio.db.models import Business

    business = (
    await db_session.execute(select(Business).where(Business.id == booking.business_id))
    ).scalar_one()
    user = booking.created_by_user_id
    again = await money.create_booking_invoice(db_session, business, booking, user)
    count = (
    await db_session.execute(
            select(Payment).where(Payment.booking_id == booking.id, Payment.status == "pending")
    )
    ).scalars().all()
    assert again is not None
    assert len(count) == 1


# ---------------------------------------------------------------------------
# Оплата с баланса
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pay_from_balance_confirms_booking(api_client, db_session, seats, master):
    """topup → pay balance → charge + бронь подтверждена, paid_amount=final."""
    # пополнение баланса владельцем
    resp = await api_client.post(
    f"/api/masters/{master.id}/topup",
        json={"amount": 5000, "note": "тест"},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["balance"] == 5000

    b = await _create_booking(api_client, seats[0]["id"], _tomorrow(12), _tomorrow(14))
    resp = await api_client.post(f"/api/bookings/{b['id']}/pay", json={"method": "balance"}, headers=MASTER_HEADERS)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "подтверждена"
    assert body["paid_amount"] == 2000
    assert body["price"] == 2000
    assert body["balance"] == 3000  # 5000 − 2000

    await db_session.refresh(booking := await db_session.get(Booking, b["id"]))
    assert booking.status == BookingStatus.confirmed.value
    assert float(booking.paid_amount) == 2000
    assert booking.hold_deadline is None
    assert "оплачена" in await _events(db_session, b["id"])
    # проводки: topup +5000, charge −2000
    entries = (await db_session.execute(select(LedgerEntry).order_by(LedgerEntry.id))).scalars().all()
    kinds = [(e.kind, Decimal(str(e.amount))) for e in entries]
    assert ("topup", Decimal("5000")) in kinds
    assert ("charge", Decimal("-2000")) in kinds
    assert await _balance(db_session, master.id) == Decimal("3000")


@pytest.mark.asyncio
async def test_pay_from_balance_insufficient_funds_409(api_client, db_session, seats, master):
    """Недостаточно средств → 409, баланс не уходит в минус, бронь ждёт оплаты."""
    b = await _create_booking(api_client, seats[0]["id"], _tomorrow(12), _tomorrow(14))
    resp = await api_client.post(f"/api/bookings/{b['id']}/pay", json={"method": "balance"}, headers=MASTER_HEADERS)
    assert resp.status_code == 409
    assert resp.json()["detail"]["balance"] == 0
    assert resp.json()["detail"]["required"] == 2000
    booking = await db_session.get(Booking, b["id"])
    assert booking.status == BookingStatus.pending_payment.value
    assert await _balance(db_session, master.id) == Decimal("0")


@pytest.mark.asyncio
async def test_pay_card_without_apipay_409(api_client, seats, master):
    """Без подключённого apipay method=card → 409 (счёт ручной)."""
    b = await _create_booking(api_client, seats[0]["id"], _tomorrow(12), _tomorrow(14))
    resp = await api_client.post(f"/api/bookings/{b['id']}/pay", json={"method": "card"}, headers=MASTER_HEADERS)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_pay_only_own_booking(api_client, seats, master, other_master):
    """Мастер не может оплатить чужую бронь — 403."""
    b = await _create_booking(api_client, seats[0]["id"], _tomorrow(12), _tomorrow(14), headers=OTHER_MASTER_HEADERS)
    resp = await api_client.post(f"/api/bookings/{b['id']}/pay", json={"method": "balance"}, headers=MASTER_HEADERS)
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Вебхук apipay: дедуп, сверка суммы, подтверждение брони
# ---------------------------------------------------------------------------


async def _make_apipay_booking_payment(db_session, business, master, booking_id: int, amount: str, external_id: str) -> Payment:
    user = (await db_session.execute(select(__import__('tg_studio.db.models', fromlist=['User']).User).where(
    __import__('tg_studio.db.models', fromlist=['User']).User.id == master.user_id
    ))).scalar_one()
    payment = Payment(
    business_id=business.id,
        booking_id=booking_id,
        amount=Decimal(amount),
        kind="prepay",
        status="pending",
        provider="apipay",
        external_invoice_id=external_id,
        payment_url="https://kaspi.test/pay/" + external_id,
        created_by_user_id=user.id,
        master_id=master.id,
    )
    db_session.add(payment)
    await db_session.commit()
    return payment


@pytest.mark.asyncio
async def test_webhook_paid_confirms_booking_and_dedup(api_client, db_session, business, seats, master, apipay_on):
    """Вебхук paid → topup + бронь подтверждена; повторный вебхук — no-op."""
    b = await _create_booking(api_client, seats[0]["id"], _tomorrow(12), _tomorrow(14))
    await _make_apipay_booking_payment(db_session, business, master, b["id"], "2000", "inv-1")

    body = webhook_body("inv-1", "paid", amount=2000)
    resp = await api_client.post("/api/payments/webhook/apipay", content=body, headers=signed_headers(body))
    assert resp.status_code == 200, resp.text
    assert resp.json()["changed"] is True

    booking = await db_session.get(Booking, b["id"])
    assert booking.status == BookingStatus.confirmed.value
    assert float(booking.paid_amount) == 2000
    assert await _balance(db_session, master.id) == Decimal("2000")
    assert "оплачена" in await _events(db_session, b["id"])

    # Повторный вебхук — дедуп: баланс не дублируется
    resp2 = await api_client.post("/api/payments/webhook/apipay", content=body, headers=signed_headers(body))
    assert resp2.status_code == 200
    assert await _balance(db_session, master.id) == Decimal("2000")
    entries = (await db_session.execute(select(LedgerEntry))).scalars().all()
    assert len([e for e in entries if e.kind == "topup"]) == 1


@pytest.mark.asyncio
async def test_webhook_amount_mismatch_not_applied(api_client, db_session, business, seats, master, apipay_on):
    """Сумма вебхука не совпала со счётом → статус paid НЕ применяется."""
    b = await _create_booking(api_client, seats[0]["id"], _tomorrow(12), _tomorrow(14))
    await _make_apipay_booking_payment(db_session, business, master, b["id"], "2000", "inv-2")

    body = webhook_body("inv-2", "paid", amount=999)  # чужая сумма
    resp = await api_client.post("/api/payments/webhook/apipay", content=body, headers=signed_headers(body))
    assert resp.status_code == 200
    assert resp.json()["ignored"] == "amount_mismatch"
    booking = await db_session.get(Booking, b["id"])
    assert booking.status == BookingStatus.pending_payment.value
    assert await _balance(db_session, master.id) == Decimal("0")


@pytest.mark.asyncio
async def test_webhook_bad_signature_403(api_client, db_session, business, seats, master, apipay_on):
    b = await _create_booking(api_client, seats[0]["id"], _tomorrow(12), _tomorrow(14))
    await _make_apipay_booking_payment(db_session, business, master, b["id"], "2000", "inv-3")
    body = webhook_body("inv-3", "paid", amount=2000)
    resp = await api_client.post(
    "/api/payments/webhook/apipay", content=body, headers=signed_headers(body, "wrong-secret")
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Hold истёк → истекла_оплата + счёт expired
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expired_hold_expires_invoice_and_frees_seat(api_client, db_session, business, seats, master):
    b = await _create_booking(api_client, seats[0]["id"], _tomorrow(12), _tomorrow(14))
    booking = await db_session.get(Booking, b["id"])
    booking.hold_deadline = datetime.now(UTC) - timedelta(minutes=1)
    await db_session.commit()

    n = await expire_booking_holds(db_session)
    await db_session.commit()
    assert n == 1
    booking = await db_session.get(Booking, b["id"])
    assert booking.status == BookingStatus.payment_expired.value
    payment = (
    await db_session.execute(select(Payment).where(Payment.booking_id == b["id"]))
    ).scalar_one()
    assert payment.status == PaymentStatus.expired.value
    assert "истекла_оплата" in await _events(db_session, b["id"])
    # место снова свободно
    resp = await api_client.get(
    f"/api/bookings/availability?start={_tomorrow(12)}&end={_tomorrow(14)}", headers=MASTER_HEADERS
    )
    assert resp.status_code == 200
    assert seats[0]["id"] in [s["seat_id"] for s in resp.json()["free_seats"]]


# ---------------------------------------------------------------------------
# Отмена: правило 24ч / удержание / площадка
# ---------------------------------------------------------------------------


async def _confirmed_booking(api_client, db_session, seats, master) -> dict:
    """Бронь на завтра 12–14, подтверждённая оплатой с баланса (нужен topup)."""
    await api_client.post(
        f"/api/masters/{master.id}/topup", json={"amount": 100000}, headers=OWNER_HEADERS
    )
    b = await _create_booking(api_client, seats[0]["id"], _tomorrow(12), _tomorrow(14))
    resp = await api_client.post(f"/api/bookings/{b['id']}/pay", json={"method": "balance"}, headers=MASTER_HEADERS)
    assert resp.status_code == 200, resp.text
    return b


@pytest.mark.asyncio
async def test_cancel_confirmed_more_than_24h_full_refund(api_client, db_session, seats, master):
    """Отмена подтверждённой брони раньше чем за 24ч до начала → refund 100%."""
    # бронь на послезавтра — до начала заведомо больше 24ч при любом текущем времени
    await api_client.post(
        f"/api/masters/{master.id}/topup", json={"amount": 100000}, headers=OWNER_HEADERS
    )
    b = await _create_booking(api_client, seats[0]["id"], _at(2, 12), _at(2, 14))
    resp = await api_client.post(f"/api/bookings/{b['id']}/pay", json={"method": "balance"}, headers=MASTER_HEADERS)
    assert resp.status_code == 200, resp.text
    balance_before = await _balance(db_session, master.id)  # 100000 − 2000
    resp = await api_client.post(f"/api/bookings/{b['id']}/cancel", json={"reason": "не смог"}, headers=MASTER_HEADERS)
    assert resp.status_code == 200, resp.text
    booking = await db_session.get(Booking, b["id"])
    assert booking.status == BookingStatus.cancelled_by_master.value
    assert await _balance(db_session, master.id) == balance_before + Decimal("2000")
    assert "возвращена" in await _events(db_session, b["id"])
    entries = (await db_session.execute(select(LedgerEntry).where(LedgerEntry.booking_id == b["id"]))).scalars().all()
    refunds = [e for e in entries if e.kind == "refund"]
    assert len(refunds) == 1 and Decimal(str(refunds[0].amount)) == Decimal("2000")


@pytest.mark.asyncio
async def test_cancel_confirmed_less_than_24h_holds_percent(api_client, db_session, business, seats, master):
    """Отмена позже чем за 24ч → удержание no_show_hold_percent (дефолт 100% → refund 0)."""
    # бронь через 2 часа (меньше 24ч)
    now_local = datetime.now(STUDIO_TZ)
    start = (now_local + timedelta(hours=2)).replace(minute=0, second=0, microsecond=0)
    if start.hour < 10:  # вне часов студии — сдвинем на завтра утро, но тогда >24ч; используем настройку
        start = start + timedelta(days=1)
    # проще: поставим правило cancel_full_refund_hours=0 → всегда удержание
    resp = await api_client.patch(
    "/api/coworking/settings",
        json={"cancel_full_refund_hours": 0, "no_show_hold_percent": 50},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 200, resp.text

    b = await _confirmed_booking(api_client, db_session, seats, master)
    balance_before = await _balance(db_session, master.id)
    resp = await api_client.post(f"/api/bookings/{b['id']}/cancel", json={"reason": None}, headers=MASTER_HEADERS)
    assert resp.status_code == 200
        # удержание 50% от 2000 → refund 1000
    assert await _balance(db_session, master.id) == balance_before + Decimal("1000")
    entries = (await db_session.execute(select(LedgerEntry).where(LedgerEntry.booking_id == b["id"]))).scalars().all()
    refunds = [e for e in entries if e.kind == "refund"]
    assert len(refunds) == 1 and Decimal(str(refunds[0].amount)) == Decimal("1000")


@pytest.mark.asyncio
async def test_cancel_by_studio_always_full_refund(api_client, db_session, business, seats, master):
    """Отмена площадкой (владелец) → всегда 100% возврат, даже в последний час."""
    resp = await api_client.patch(
    "/api/coworking/settings",
        json={"cancel_full_refund_hours": 0, "no_show_hold_percent": 100},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 200
    b = await _confirmed_booking(api_client, db_session, seats, master)
    balance_before = await _balance(db_session, master.id)
    resp = await api_client.post(f"/api/bookings/{b['id']}/cancel", json={"reason": "ремонт"}, headers=OWNER_HEADERS)
    assert resp.status_code == 200
    booking = await db_session.get(Booking, b["id"])
    assert booking.status == BookingStatus.cancelled_by_studio.value
    assert await _balance(db_session, master.id) == balance_before + Decimal("2000")


@pytest.mark.asyncio
async def test_no_show_holds_percent(api_client, db_session, business, seats, master):
    """Неявка → удержание no_show_hold_percent от оплаты (дефолт 100%)."""
    b = await _confirmed_booking(api_client, db_session, seats, master)
    balance_before = await _balance(db_session, master.id)
    resp = await api_client.post(f"/api/bookings/{b['id']}/no-show", headers=OWNER_HEADERS)
    assert resp.status_code == 200, resp.text
    booking = await db_session.get(Booking, b["id"])
    assert booking.status == BookingStatus.no_show.value
    # дефолт 100% удержание → возврата нет
    assert await _balance(db_session, master.id) == balance_before
    assert "неявка" in await _events(db_session, b["id"])


@pytest.mark.asyncio
async def test_reschedule_paid_booking_forbidden(api_client, db_session, seats, master):
    """Оплаченную бронь переносить нельзя (инвариант 100% оплаты) — 409."""
    b = await _confirmed_booking(api_client, db_session, seats, master)
    resp = await api_client.patch(
    f"/api/bookings/{b['id']}",
        json={"start": _tomorrow(15), "end": _tomorrow(17)},
        headers=MASTER_HEADERS,
    )
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Баланс = Σ проводок; журнал; права; корректировка
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_balance_is_sum_of_ledger(api_client, db_session, master):
    """Остаток сходится с журналом после topup + adjust."""
    await api_client.post(f"/api/masters/{master.id}/topup", json={"amount": 3000}, headers=OWNER_HEADERS)
    await api_client.post(
        f"/api/masters/{master.id}/ledger/adjust",
        json={"amount": -500, "reason": "ошибка в наличных"},
        headers=OWNER_HEADERS,
    )
    resp = await api_client.get("/api/me/balance", headers=MASTER_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["balance"] == 2500
    ledger = await api_client.get("/api/me/ledger", headers=MASTER_HEADERS)
    total = sum(e["amount"] for e in ledger.json()["entries"])
    assert total == 2500


@pytest.mark.asyncio
async def test_adjust_requires_reason(api_client, db_session, master):
    resp = await api_client.post(
    f"/api/masters/{master.id}/ledger/adjust",
        json={"amount": 100, "reason": ""},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code in (400, 422)


@pytest.mark.asyncio
async def test_master_sees_only_own_ledger(api_client, db_session, seats, master, other_master):
    """Мастер видит только свою выписку; чужой баланс/журнал — 403/404."""
    await api_client.post(f"/api/masters/{master.id}/topup", json={"amount": 1000}, headers=OWNER_HEADERS)
    await api_client.post(f"/api/masters/{other_master.id}/topup", json={"amount": 7000}, headers=OWNER_HEADERS)

    resp = await api_client.get("/api/me/ledger", headers=MASTER_HEADERS)
    assert resp.status_code == 200
    assert all(e["master_id"] == master.id for e in resp.json()["entries"])

    # владелец видит весь журнал
    resp = await api_client.get("/api/ledger", headers=OWNER_HEADERS)
    assert resp.status_code == 200
    master_ids = {e["master_id"] for e in resp.json()["entries"]}
    assert master_ids == {master.id, other_master.id}

    # мастер не может смотреть чужой баланс
    resp = await api_client.get(f"/api/masters/{other_master.id}/balance", headers=MASTER_HEADERS)
    assert resp.status_code in (403, 404)

    # мастер не может пополнять/корректировать (только владелец)
    resp = await api_client.post(
    f"/api/masters/{master.id}/topup", json={"amount": 100}, headers=MASTER_HEADERS
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_owner_balance_endpoint(api_client, db_session, master):
    await api_client.post(f"/api/masters/{master.id}/topup", json={"amount": 4200}, headers=OWNER_HEADERS)
    resp = await api_client.get(f"/api/masters/{master.id}/balance", headers=OWNER_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["balance"] == 4200
    assert resp.json()["currency"] == "KZT"
