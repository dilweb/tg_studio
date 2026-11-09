"""Google Calendar client: fail-open при сбоях и таймаут вызовов (без сети)."""

import time

import pytest

from tg_studio.modules.google_calendar import client as gcal


@pytest.mark.asyncio
async def test_create_event_fail_open_on_unexpected_error(monkeypatch):
    """Любой сбой вызова Google (не только HttpError) → None, без исключения."""

    def boom(**kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(gcal, "_load_credentials", lambda _json: object())
    monkeypatch.setattr(gcal, "_create_event_sync", boom)

    event_id = await gcal.create_event(
        credentials_json="{}",
        summary="Сеанс",
        description=None,
        start_datetime="2026-10-26T10:00:00",
        end_datetime="2026-10-26T13:00:00",
    )
    assert event_id is None


@pytest.mark.asyncio
async def test_create_event_timeout_is_fail_open(monkeypatch):
    """Зависший вызов Google → таймаут → fail-open None (а не зависший event loop)."""

    def slow(**kwargs):
        time.sleep(1)
        return "late"

    monkeypatch.setattr(gcal, "GOOGLE_API_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(gcal, "_load_credentials", lambda _json: object())
    monkeypatch.setattr(gcal, "_create_event_sync", slow)

    event_id = await gcal.create_event(
        credentials_json="{}",
        summary="Сеанс",
        description=None,
        start_datetime="2026-10-26T10:00:00",
        end_datetime="2026-10-26T13:00:00",
    )
    assert event_id is None


@pytest.mark.asyncio
async def test_cancel_event_fail_open_on_error(monkeypatch):
    def boom(**kwargs):
        raise OSError("connection reset")

    monkeypatch.setattr(gcal, "_load_credentials", lambda _json: object())
    monkeypatch.setattr(gcal, "_cancel_event_sync", boom)

    assert await gcal.cancel_event(credentials_json="{}", event_id="ev-1") is False


@pytest.mark.asyncio
async def test_no_credentials_short_circuit():
    """Без credentials — сразу fail-open, без вызовов Google."""
    assert await gcal.cancel_event(credentials_json="", event_id="ev-1") is False
    assert (
        await gcal.list_events(
            credentials_json=None,
            time_min="2026-10-26T00:00:00",
            time_max="2026-10-27T00:00:00",
        )
        == []
    )
    assert (
        await gcal.create_event(
            credentials_json="",
            summary="s",
            description=None,
            start_datetime="2026-10-26T10:00:00",
            end_datetime="2026-10-26T13:00:00",
        )
        is None
    )
