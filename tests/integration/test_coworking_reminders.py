# ruff: noqa: F811  — фикстуры импортированы из test_coworking_money и подставляются pytest по имени
"""Напоминания мастерам в Telegram и подтверждение присутствия кнопкой.

- «оплатите до …» — один раз, когда до дедлайна оплаты ≤ payment_reminder_minutes;
- «скоро бронь» с кнопкой «Подтвердить» — один раз, когда до начала ≤ booking_reminder_minutes;
- вебхук Telegram: секрет, только мастер своей брони, окно [start − N, end), повтор — без изменений;
- отчёт «Мастера»: присутствие (начавшиеся брони и подтверждённые из них).
"""

from datetime import UTC, datetime, timedelta

import pytest

from tg_studio.config import settings
from tg_studio.db.models import Booking, BookingStatus
from tg_studio.modules import notify
from tg_studio.modules.coworking.reminders import send_due_reminders

from .test_coworking_money import (  # noqa: F401  (фикстуры)
    MASTER_HEADERS,
    MASTER_TG_ID,
    OWNER_HEADERS,
    _confirmed_booking,
    _create_booking,
    _tomorrow,
    business,
    master,
    seats,
)

TG_SECRET = "tg-webhook-secret"


@pytest.fixture
def telegram(monkeypatch):
    """Подмена Telegram без сети: журнал отправок и ответов на нажатия."""
    sent: list[tuple] = []

    async def fake_text(chat_id, text):
        sent.append(("text", chat_id, text))
        return 1

    async def fake_button(chat_id, text, button_text, callback_data):
        sent.append(("button", chat_id, text, callback_data))
        return 1

    async def fake_answer(callback_id, text, *, replace_message=None):
        sent.append(("answer", callback_id, text, replace_message))

    monkeypatch.setattr(notify, "send_text_to_telegram", fake_text)
    monkeypatch.setattr(notify, "send_text_with_button", fake_button)
    monkeypatch.setattr(notify, "answer_callback", fake_answer)
    monkeypatch.setattr(settings, "telegram_webhook_secret", TG_SECRET)
    return sent


async def _start_soon(db_session, booking_id: int, minutes: int) -> Booking:
    """Сдвинуть бронь так, чтобы она начиналась через minutes минут (1 час длиной)."""
    booking = await db_session.get(Booking, booking_id)
    booking.start = datetime.now(UTC) + timedelta(minutes=minutes)
    booking.end = booking.start + timedelta(hours=1)
    await db_session.commit()
    return booking


def _callback(booking_id: int, from_id: int = MASTER_TG_ID) -> dict:
    return {
        "update_id": 1,
        "callback_query": {
            "id": "cb-1",
            "from": {"id": from_id},
            "data": f"presence:{booking_id}",
            "message": {"message_id": 7, "chat": {"id": from_id}, "text": "Через 30 мин бронь"},
        },
    }


async def _press(api_client, booking_id: int, from_id: int = MASTER_TG_ID, secret: str = TG_SECRET):
    return await api_client.post(
        "/api/telegram/webhook",
        json=_callback(booking_id, from_id),
        headers={"X-Telegram-Bot-Api-Secret-Token": secret},
    )


@pytest.mark.asyncio
async def test_payment_reminder_sent_once(api_client, db_session, seats, master, telegram):
    b = await _create_booking(api_client, seats[0]["id"], _tomorrow(12), _tomorrow(14))
    booking = await db_session.get(Booking, b["id"])
    booking.hold_deadline = datetime.now(UTC) + timedelta(minutes=90)  # дефолт — за 120 мин
    await db_session.commit()

    assert await send_due_reminders(db_session) == {"payment": 1, "booking": 0}
    assert await send_due_reminders(db_session) == {"payment": 0, "booking": 0}
    [(kind, chat_id, text)] = telegram
    assert kind == "text" and chat_id == MASTER_TG_ID
    assert "не оплачена" in text and "Оплатите до" in text


@pytest.mark.asyncio
async def test_payment_reminder_not_due_yet(api_client, db_session, seats, master, telegram):
    b = await _create_booking(api_client, seats[0]["id"], _tomorrow(12), _tomorrow(14))
    booking = await db_session.get(Booking, b["id"])
    booking.hold_deadline = datetime.now(UTC) + timedelta(hours=5)
    await db_session.commit()
    assert await send_due_reminders(db_session) == {"payment": 0, "booking": 0}
    assert telegram == []


@pytest.mark.asyncio
async def test_booking_reminder_with_confirm_button(api_client, db_session, seats, master, telegram):
    b = await _confirmed_booking(api_client, db_session, seats, master)
    await _start_soon(db_session, b["id"], 30)

    assert await send_due_reminders(db_session) == {"payment": 0, "booking": 1}
    assert await send_due_reminders(db_session) == {"payment": 0, "booking": 0}
    [(kind, chat_id, text, data)] = telegram
    assert kind == "button" and chat_id == MASTER_TG_ID
    assert data == f"presence:{b['id']}"
    assert "Через" in text


@pytest.mark.asyncio
async def test_presence_confirmed_by_button(api_client, db_session, seats, master, telegram):
    b = await _confirmed_booking(api_client, db_session, seats, master)
    await _start_soon(db_session, b["id"], 30)

    resp = await _press(api_client, b["id"])
    assert resp.status_code == 200 and resp.json()["confirmed"] is True
    booking = await db_session.get(Booking, b["id"])
    assert booking.presence_confirmed_at is not None
    answer = telegram[-1]
    assert answer[0] == "answer" and answer[3][:2] == (MASTER_TG_ID, 7)  # кнопка убрана из сообщения

    # повторное нажатие — без изменений
    first = booking.presence_confirmed_at
    resp = await _press(api_client, b["id"])
    assert resp.json()["confirmed"] is True
    await db_session.refresh(booking)
    assert booking.presence_confirmed_at == first

    events = (await api_client.get(f"/api/bookings/{b['id']}/history", headers=MASTER_HEADERS)).json()["events"]
    assert [e["kind"] for e in events].count("присутствие_подтверждено") == 1


@pytest.mark.asyncio
async def test_presence_rejected_for_other_user_early_or_bad_secret(api_client, db_session, seats, master, telegram):
    b = await _confirmed_booking(api_client, db_session, seats, master)

    resp = await _press(api_client, b["id"], secret="wrong")
    assert resp.status_code == 403

    await _start_soon(db_session, b["id"], 30)
    resp = await _press(api_client, b["id"], from_id=12345)  # чужой telegram
    assert resp.json()["confirmed"] is False

    await _start_soon(db_session, b["id"], 180)  # раньше окна напоминания (60 мин)
    resp = await _press(api_client, b["id"])
    assert resp.json()["confirmed"] is False

    booking = await db_session.get(Booking, b["id"])
    await db_session.refresh(booking)
    assert booking.presence_confirmed_at is None


@pytest.mark.asyncio
async def test_presence_in_masters_report(api_client, db_session, seats, master, telegram):
    b = await _confirmed_booking(api_client, db_session, seats, master)
    await _start_soon(db_session, b["id"], 30)
    assert (await _press(api_client, b["id"])).json()["confirmed"] is True
    booking = await db_session.get(Booking, b["id"])
    booking.start = datetime.now(UTC) - timedelta(minutes=10)  # уже началась
    await db_session.commit()

    now = datetime.now(UTC)
    resp = await api_client.get(
        "/api/reports/masters",
        params={"from": (now - timedelta(days=1)).isoformat(), "to": (now + timedelta(days=1)).isoformat()},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["presence_expected"] == 1 and body["presence_confirmed"] == 1
    row = next(r for r in body["masters"] if r["master_id"] == master.id)
    assert (row["presence_expected"], row["presence_confirmed"]) == (1, 1)


@pytest.mark.asyncio
async def test_unpaid_booking_has_no_presence_or_booking_reminder(api_client, db_session, seats, master, telegram):
    b = await _create_booking(api_client, seats[0]["id"], _tomorrow(12), _tomorrow(14))
    booking = await _start_soon(db_session, b["id"], 30)
    booking.hold_deadline = booking.start
    await db_session.commit()
    sent = await send_due_reminders(db_session)
    assert sent["booking"] == 0
    resp = await _press(api_client, b["id"])
    assert resp.json()["confirmed"] is False
    assert (await db_session.get(Booking, b["id"])).status == BookingStatus.pending_payment.value
