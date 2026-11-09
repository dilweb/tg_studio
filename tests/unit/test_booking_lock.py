"""Booking agent: таймаут захвата per-client lock (зависший ход не блокирует вечно)."""

from types import SimpleNamespace

import pytest

from tg_studio.modules.ai import booking


@pytest.mark.asyncio
async def test_lock_acquire_timeout_returns_failure_reply(monkeypatch):
    """Lock держит «предыдущий ход» → новый ход не ждёт вечно, а отвечает FAILURE."""
    monkeypatch.setattr(booking, "LOCK_ACQUIRE_TIMEOUT_SECONDS", 0.05)
    lock = booking._lock_for(424242)
    await lock.acquire()
    try:
        reply = await booking.run_booking_turn(
            None, None, SimpleNamespace(id=424242), "привет"
        )
    finally:
        lock.release()
    assert reply == booking.FAILURE_CLIENT_REPLY
