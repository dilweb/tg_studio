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
- отмена мастером — возврат по ступеням refund_tiers (≥24ч 100%, ≥12ч 50%, позже 0);
- отмена площадкой → всегда 100% возврат;
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
    # оплата картой не оставляет деньги на балансе: topup +2000, charge −2000
    assert await _balance(db_session, master.id) == Decimal("0")
    assert "оплачена" in await _events(db_session, b["id"])

    # Повторный вебхук — дедуп: проводки не дублируются
    resp2 = await api_client.post("/api/payments/webhook/apipay", content=body, headers=signed_headers(body))
    assert resp2.status_code == 200
    assert await _balance(db_session, master.id) == Decimal("0")
    entries = (await db_session.execute(select(LedgerEntry))).scalars().all()
    assert len([e for e in entries if e.kind == "topup"]) == 1
    assert len([e for e in entries if e.kind == "charge"]) == 1


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


def test_refund_tiers_default():
    """Дефолтные ступени: ≥24ч → 100%, ≥12ч → 50%, позже → 0%."""
    from types import SimpleNamespace

    from tg_studio.modules.coworking.service import coworking_settings

    cfg = coworking_settings(SimpleNamespace(coworking_settings={}))
    for hours, expected in ((30, "2000"), (24.1, "2000"), (18, "1000"), (12.1, "1000"), (6, "0")):
        booking = SimpleNamespace(
            price_snapshot={"final": 2000}, start=datetime.now(UTC) + timedelta(hours=hours)
        )
        assert money.cancel_refund_amount(booking, cfg, by_studio=False) == Decimal(expected), hours
        assert money.cancel_refund_amount(booking, cfg, by_studio=True) == Decimal("2000")


@pytest.mark.asyncio
async def test_cancel_refund_by_configured_tier(api_client, db_session, business, seats, master):
    """Ступень из настроек владельца: отмена в любой момент до начала → 50%."""
    resp = await api_client.patch(
        "/api/coworking/settings",
        json={"refund_tiers": [{"hours_before": 0, "percent": 50}]},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["refund_tiers"] == [{"hours_before": 0, "percent": 50}]

    b = await _confirmed_booking(api_client, db_session, seats, master)
    balance_before = await _balance(db_session, master.id)
    resp = await api_client.post(f"/api/bookings/{b['id']}/cancel", json={"reason": None}, headers=MASTER_HEADERS)
    assert resp.status_code == 200
    assert await _balance(db_session, master.id) == balance_before + Decimal("1000")
    entries = (await db_session.execute(select(LedgerEntry).where(LedgerEntry.booking_id == b["id"]))).scalars().all()
    refunds = [e for e in entries if e.kind == "refund"]
    assert len(refunds) == 1 and Decimal(str(refunds[0].amount)) == Decimal("1000")


@pytest.mark.asyncio
async def test_refund_tiers_validation(api_client, business):
    for tiers in (
        [{"hours_before": 24, "percent": 50}, {"hours_before": 12, "percent": 100}],  # позже — больше
        [{"hours_before": 24, "percent": 100}, {"hours_before": 24, "percent": 50}],  # повтор часов
    ):
        resp = await api_client.patch("/api/coworking/settings", json={"refund_tiers": tiers}, headers=OWNER_HEADERS)
        assert resp.status_code == 422, tiers


@pytest.mark.asyncio
async def test_cancel_by_studio_always_full_refund(api_client, db_session, business, seats, master):
    """Отмена площадкой (владелец) → всегда 100% возврат, даже в последний час."""
    resp = await api_client.patch(
    "/api/coworking/settings",
        json={"refund_tiers": []},  # мастеру — 0% в любой момент
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


# ---------------------------------------------------------------------------
# Счета брони следуют за её состоянием (01: долга и висящих счетов нет)
# ---------------------------------------------------------------------------


async def _booking_payments(db_session, booking_id: int) -> list[Payment]:
    return list(
        (
            await db_session.execute(
                select(Payment).where(Payment.booking_id == booking_id).order_by(Payment.id)
            )
        ).scalars()
    )


@pytest.mark.asyncio
async def test_reschedule_pending_reissues_invoice(api_client, db_session, seats, master):
    master_id = master.id  # после expire_all атрибут не перечитать вне greenlet
    b = await _create_booking(api_client, seats[0]["id"], _tomorrow(12), _tomorrow(14))
    resp = await api_client.patch(
        f"/api/bookings/{b['id']}",
        json={"start": _tomorrow(12), "end": _tomorrow(16)},
        headers=MASTER_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    new_price = Decimal(str(resp.json()["price_snapshot"]["final"]))
    assert new_price > Decimal(str(b["price_snapshot"]["final"]))

    db_session.expire_all()
    old, new = await _booking_payments(db_session, b["id"])
    assert old.status == "cancelled"
    assert new.status == "pending" and Decimal(str(new.amount)) == new_price

    # Запоздалая оплата старого (меньшего) счёта бронь не подтверждает: деньги на балансе
    assert await money.apply_payment_paid(db_session, old) is True
    await db_session.commit()
    booking = await db_session.get(Booking, b["id"])
    assert booking.status == BookingStatus.pending_payment.value
    assert await _balance(db_session, master_id) == Decimal(str(old.amount))


@pytest.mark.asyncio
async def test_cancel_pending_closes_invoice(api_client, db_session, seats, master):
    b = await _create_booking(api_client, seats[0]["id"], _tomorrow(12), _tomorrow(14))
    resp = await api_client.post(
        f"/api/bookings/{b['id']}/cancel", json={"reason": "передумала"}, headers=MASTER_HEADERS
    )
    assert resp.status_code == 200, resp.text
    db_session.expire_all()
    assert [p.status for p in await _booking_payments(db_session, b["id"])] == ["cancelled"]


@pytest.mark.asyncio
async def test_pay_from_balance_closes_invoice(api_client, db_session, seats, master):
    b = await _confirmed_booking(api_client, db_session, seats, master)
    db_session.expire_all()
    assert [p.status for p in await _booking_payments(db_session, b["id"])] == ["cancelled"]


# ---------------------------------------------------------------------------
# Пополнение баланса картой, уникальность topup, поздняя оплата, пагинация
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_my_topup_requires_apipay(api_client, master):
    resp = await api_client.post("/api/me/topups", json={"amount": 1000}, headers=MASTER_HEADERS)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_my_topup_invoice_and_webhook_credits_balance(
    api_client, db_session, master, apipay_on, monkeypatch
):
    from tg_studio.modules.payments.providers import ApipayProvider, CreatedInvoice

    async def fake_create(self, payment, ctx):
        return CreatedInvoice(external_id="inv-top-1", payment_url="https://kaspi.test/pay/top-1")

    monkeypatch.setattr(ApipayProvider, "create_invoice", fake_create)

    resp = await api_client.post("/api/me/topups", json={"amount": 1500.5}, headers=MASTER_HEADERS)
    assert resp.status_code == 400  # Kaspi — только целые тенге

    resp = await api_client.post("/api/me/topups", json={"amount": 1500}, headers=MASTER_HEADERS)
    assert resp.status_code == 201, resp.text
    assert resp.json()["payment_url"] == "https://kaspi.test/pay/top-1"
    assert await _balance(db_session, master.id) == Decimal("0")

    body = webhook_body("inv-top-1", "paid", amount=1500)
    for _ in range(2):  # повторный вебхук — дедуп
        r = await api_client.post("/api/payments/webhook/apipay", content=body, headers=signed_headers(body))
        assert r.status_code == 200, r.text
    assert await _balance(db_session, master.id) == Decimal("1500")


@pytest.mark.asyncio
async def test_ledger_unique_topup_per_payment(db_session, business, master):
    from sqlalchemy.exc import IntegrityError

    from tg_studio.modules.coworking import money

    payment = await money.manual_topup(db_session, business, master, Decimal("100"), master.user_id, None)
    await db_session.commit()
    db_session.add(
        LedgerEntry(
            business_id=business.id, master_id=master.id, kind="topup", amount=100, payment_id=payment.id
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_late_payment_after_expiry_lands_on_balance_with_note(
    api_client, db_session, business, seats, master, apipay_on
):
    b = await _create_booking(api_client, seats[0]["id"], _tomorrow(12), _tomorrow(14))
    await _make_apipay_booking_payment(db_session, business, master, b["id"], "2000", "inv-late")
    booking = await db_session.get(Booking, b["id"])
    booking.status = BookingStatus.payment_expired.value
    await db_session.commit()

    body = webhook_body("inv-late", "paid", amount=2000)
    resp = await api_client.post("/api/payments/webhook/apipay", content=body, headers=signed_headers(body))
    assert resp.status_code == 200
    assert await _balance(db_session, master.id) == Decimal("2000")
    topup = (
        await db_session.execute(select(LedgerEntry).where(LedgerEntry.kind == "topup"))
    ).scalar_one()
    assert "уже" in topup.note and "баланс" in topup.note


@pytest.mark.asyncio
async def test_ledger_total_is_full_count(api_client, master):
    for _ in range(3):
        await api_client.post(f"/api/masters/{master.id}/topup", json={"amount": 100}, headers=OWNER_HEADERS)
    resp = await api_client.get("/api/me/ledger?limit=2&offset=1", headers=MASTER_HEADERS)
    assert len(resp.json()["entries"]) == 2
    assert resp.json()["total"] == 3
    resp = await api_client.get("/api/ledger?limit=1", headers=OWNER_HEADERS)
    assert resp.json()["total"] == 3 and len(resp.json()["entries"]) == 1


@pytest.mark.asyncio
async def test_expired_hold_before_sweep_frees_seat_and_blocks_late_payment(
    api_client, db_session, seats, master, other_master
):
    await api_client.post(f"/api/masters/{master.id}/topup", json={"amount": 100000}, headers=OWNER_HEADERS)
    b = await _create_booking(api_client, seats[0]["id"], _tomorrow(12), _tomorrow(14))
    booking = await db_session.get(Booking, b["id"])
    booking.hold_deadline = datetime.now(UTC) - timedelta(minutes=1)  # свип ещё не отработал
    await db_session.commit()
    # другой мастер уже может занять место
    taken = await api_client.post(
        "/api/bookings",
        json={"seat_id": seats[0]["id"], "start": _tomorrow(12), "end": _tomorrow(14)},
        headers=OTHER_MASTER_HEADERS,
    )
    assert taken.status_code == 201, taken.text
    # а первый — оплатить просроченную бронь больше не может
    late = await api_client.post(f"/api/bookings/{b['id']}/pay", json={"method": "balance"}, headers=MASTER_HEADERS)
    assert late.status_code == 409


# ---------------------------------------------------------------------------
# Надёжность: ApiPay после commit, перенос под блокировкой, цена 0, свип hold'ов
# ---------------------------------------------------------------------------


def _fake_apipay(monkeypatch, *, fail_create: bool = False) -> list[str]:
    """Подмена ApiPay без сети; возвращает журнал вызовов."""
    from tg_studio.modules.payments.providers import ApipayProvider, CreatedInvoice, PaymentProviderError

    calls: list[str] = []

    async def fake_create(self, payment, ctx):
        calls.append(f"create:{payment.id}")
        if fail_create:
            raise PaymentProviderError("ApiPay недоступен")
        return CreatedInvoice(external_id=f"inv-b{payment.id}", payment_url=f"https://kaspi.test/{payment.id}")

    async def fake_cancel(self, payment):
        calls.append(f"cancel:{payment.external_invoice_id}")

    monkeypatch.setattr(ApipayProvider, "create_invoice", fake_create)
    monkeypatch.setattr(ApipayProvider, "cancel_invoice", fake_cancel)
    return calls


@pytest.mark.asyncio
async def test_kaspi_invoice_attached_after_commit_and_cancelled_with_booking(
    api_client, db_session, seats, master, apipay_on, monkeypatch
):
    calls = _fake_apipay(monkeypatch)
    b = await _create_booking(api_client, seats[0]["id"], _tomorrow(12), _tomorrow(14))
    pid = b["payment"]["id"]
    assert b["payment"]["provider"] == "apipay"
    assert b["payment"]["payment_url"] == f"https://kaspi.test/{pid}"

    resp = await api_client.post(f"/api/bookings/{b['id']}/cancel", json={"reason": "x"}, headers=MASTER_HEADERS)
    assert resp.status_code == 200, resp.text
    assert calls == [f"create:{pid}", f"cancel:inv-b{pid}"]
    payment = await db_session.get(Payment, pid)
    assert payment.status == PaymentStatus.cancelled.value


@pytest.mark.asyncio
async def test_kaspi_failure_leaves_manual_invoice(api_client, db_session, seats, master, apipay_on, monkeypatch):
    _fake_apipay(monkeypatch, fail_create=True)
    b = await _create_booking(api_client, seats[0]["id"], _tomorrow(12), _tomorrow(14))
    assert b["status"] == "ожидает_оплаты"
    assert b["payment"]["provider"] == "manual"
    assert b["payment"]["payment_url"] is None


@pytest.mark.asyncio
async def test_kaspi_invoice_for_closed_payment_is_cancelled(db_session, business, seats, master, monkeypatch):
    """Счёт закрыли (перенос/оплата) раньше, чем ApiPay ответил — внешний счёт отменяется."""
    calls = _fake_apipay(monkeypatch)
    from tg_studio.modules.payments.providers import InvoiceContext

    payment = Payment(
        business_id=business.id, amount=Decimal("1000"), kind="prepay", status="cancelled",
        provider="manual", master_id=master.id, created_by_user_id=master.user_id,
    )
    db_session.add(payment)
    await db_session.commit()
    money._defer_provider_op(db_session, ("create", payment.id, InvoiceContext("87771234567", "М", "x")))
    await money.run_provider_ops(db_session)
    assert calls == [f"create:{payment.id}", f"cancel:inv-b{payment.id}"]
    await db_session.refresh(payment)
    assert payment.provider == "manual" and payment.external_invoice_id is None


@pytest.mark.asyncio
async def test_reschedule_checks_status_after_lock(api_client, db_session, business, seats, master):
    """Сервис переноса сам отказывает оплаченной брони (гонка «оплата ↔ перенос»)."""
    from tg_studio.modules.coworking import service as cw

    resp = await api_client.post(
        f"/api/masters/{master.id}/topup", json={"amount": 5000, "note": "т"}, headers=OWNER_HEADERS
    )
    assert resp.status_code == 201
    b = await _create_booking(api_client, seats[0]["id"], _tomorrow(12), _tomorrow(14))
    booking = await db_session.get(Booking, b["id"])
    # API-проверка статуса прошла бы раньше этой оплаты — вызываем сервис напрямую
    await money.pay_booking_from_balance(db_session, business, booking, master.user_id)
    await db_session.commit()
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await cw.reschedule_booking(
            db_session, business, booking,
            datetime.fromisoformat(_tomorrow(15)), datetime.fromisoformat(_tomorrow(17)), master.user_id,
        )
    assert exc.value.status_code == 409
    await db_session.rollback()
    booking = await db_session.get(Booking, b["id"])
    assert Decimal(str(booking.paid_amount)) == Decimal(str(booking.price_snapshot["final"]))


@pytest.mark.asyncio
async def test_zero_price_booking_rejected(api_client, db_session, business, seats, master):
    from datetime import time as dtime

    from tg_studio.db.models import PriceRule

    db_session.add(PriceRule(
        business_id=business.id, name="бесплатно", day_kind="все",
        time_from=dtime(10, 0), time_to=dtime(22, 0), hourly_rate=0, priority=10,
    ))
    await db_session.commit()
    resp = await api_client.post(
        "/api/bookings",
        json={"seat_id": seats[0]["id"], "start": _tomorrow(12), "end": _tomorrow(14)},
        headers=MASTER_HEADERS,
    )
    assert resp.status_code == 409, resp.text


@pytest.mark.asyncio
async def test_expire_holds_commits_per_booking(api_client, db_session, seats, master):
    ids = []
    for seat in seats[:2]:
        b = await _create_booking(api_client, seat["id"], _tomorrow(12), _tomorrow(13), headers=OWNER_HEADERS, master_id=master.id)
        ids.append(b["id"])
    for bid in ids:
        (await db_session.get(Booking, bid)).hold_deadline = datetime.now(UTC) - timedelta(minutes=1)
    await db_session.commit()

    assert await expire_booking_holds(db_session) == 2
    await db_session.rollback()  # сервис уже закоммитил сам — откат вызывающего ничего не теряет
    for bid in ids:
        booking = await db_session.get(Booking, bid)
        assert booking.status == BookingStatus.payment_expired.value
