"""Юнит-тесты тула get_bookings: окно дат, фильтр мастера, обрезки. Без БД и сети."""

from datetime import date, datetime, time, timedelta

import pytest

from tg_studio.db.models import Business, Master
from tg_studio.modules.ai import calendar_tools as ct

TZ = ct.TZ

TODAY = date(2026, 9, 21)


class _FakeSessionCM:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *exc):
        return False


def _fake_session_factory():
    return _FakeSessionCM()


def _bookings(count: int):
    return [
        {
            "event_id": f"ev{i}",
            "master_id": 1,
            "master_name": "Eugin",
            "service_name": "tattoo",
            "client_name": f"Client {i}",
            "client_phone": "+7 700 000 0000",
            "starts_at": "2026-09-21T10:00:00+06:00",
            "ends_at": "2026-09-21T12:00:00+06:00",
            "summary": f"tattoo — Client {i}" * 10,  # заведомо длиннее 80
        }
        for i in range(count)
    ]


class TestResolveWindow:
    def test_defaults_two_weeks_from_today(self, monkeypatch):
        monkeypatch.setattr(ct, "_today", lambda: TODAY)
        assert ct._resolve_window(None, None) == (TODAY, TODAY + timedelta(days=14), False)

    def test_explicit_dates_pass_through(self, monkeypatch):
        monkeypatch.setattr(ct, "_today", lambda: TODAY)
        assert ct._resolve_window("2026-09-01", "2026-09-07") == (
            date(2026, 9, 1),
            date(2026, 9, 7),
            False,
        )

    def test_only_date_from_extends_by_default(self, monkeypatch):
        monkeypatch.setattr(ct, "_today", lambda: TODAY)
        frm, to, clamped = ct._resolve_window("2026-09-10", None)
        assert (frm, to, clamped) == (date(2026, 9, 10), date(2026, 9, 24), False)

    def test_inverted_range_raises(self):
        with pytest.raises(ct.WindowRangeError):
            ct._resolve_window("2026-09-10", "2026-09-01", today=TODAY)

    def test_bad_format_raises_value_error(self):
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            ct._resolve_window("2026/09/21", None, today=TODAY)

    def test_long_window_clamped_to_31_days(self):
        frm, to, clamped = ct._resolve_window("2026-09-01", "2026-12-31", today=TODAY)
        assert clamped is True
        assert to == frm + timedelta(days=30)


class TestGetBookings:
    @pytest.fixture
    def capture(self, monkeypatch):
        captured = {}

        async def fake_list_bookings(session, business, master_id, from_date, to_date):
            captured.update(
                master_id=master_id,
                from_date=from_date,
                to_date=to_date,
                business=business,
            )
            return captured.pop("bookings_fixture", [])

        monkeypatch.setattr(ct, "async_session_factory", _fake_session_factory)
        monkeypatch.setattr(ct, "list_bookings", fake_list_bookings)
        monkeypatch.setattr(
            ct,
            "_load_business",
            lambda *a, **kw: _async_return(
                captured.pop("business", Business(google_calendar_credentials_json="{}"))
            ),
        )
        monkeypatch.setattr(ct, "_find_masters", _fake_find_masters(captured))
        monkeypatch.setattr(
            ct, "_all_active_masters", lambda *a, **kw: _async_return(captured.pop("all_masters", []))
        )
        return captured

    async def test_no_master_filter_means_all_masters(self, capture):
        result = await ct.get_bookings(business_id=1)
        assert capture["master_id"] is None
        assert result["master"] is None
        assert result["booking_count"] == 0

    async def test_unique_master_resolved(self, capture):
        capture["masters"] = [Master(id=7, full_name="Eugin")]
        result = await ct.get_bookings(business_id=1, master="eugin")
        assert capture["master_id"] == 7
        assert result["master"] == {"id": 7, "name": "Eugin"}

    async def test_unknown_master_returns_available_list(self, capture):
        capture["masters"] = []
        capture["all_masters"] = [Master(id=1, full_name="Anna Master")]
        result = await ct.get_bookings(business_id=1, master="Маша")
        assert result["error"] == "master_not_found"
        assert result["available_masters"] == [{"id": 1, "name": "Anna Master"}]
        assert "bookings" not in result

    async def test_ambiguous_master_returns_candidates(self, capture):
        capture["masters"] = [
            Master(id=1, full_name="Anna Master"),
            Master(id=2, full_name="Anna Second"),
        ]
        result = await ct.get_bookings(business_id=1, master="Anna")
        assert result["error"] == "ambiguous_master"
        assert [c["id"] for c in result["candidates"]] == [1, 2]
        assert "bookings" not in result

    async def test_calendar_not_connected(self, capture):
        capture["business"] = Business(google_calendar_credentials_json=None)
        result = await ct.get_bookings(business_id=1)
        assert result["error"] == "calendar_not_connected"
        assert "master_id" not in capture

    async def test_window_bounds_cover_date_to_fully(self, capture):
        result = await ct.get_bookings(business_id=1, date_from="2026-09-01", date_to="2026-09-07")
        assert result["window"] == {"from": "2026-09-01", "to": "2026-09-07", "clamped": False}
        assert capture["from_date"] == datetime.combine(
            date(2026, 9, 1), time.min, tzinfo=TZ
        )
        # верхняя граница end-exclusive: полночь СЛЕДУЮЩЕГО дня
        assert capture["to_date"] == datetime.combine(
            date(2026, 9, 8), time.min, tzinfo=TZ
        )

    async def test_truncation_and_fields(self, capture):
        capture["bookings_fixture"] = _bookings(55)
        result = await ct.get_bookings(business_id=1)
        assert result["booking_count"] == 55
        assert result["truncated"] is True
        assert len(result["bookings"]) == 50
        first = result["bookings"][0]
        assert len(first["summary"]) <= 80
        assert "client_phone" not in first
        assert "event_id" not in first


def _async_return(value):
    """Готовая корутина, всегда возвращающая value (для monkeypatch await-функций)."""

    async def _coro():
        return value

    return _coro()


def _fake_find_masters(captured):
    async def _inner(session, business_id, name):
        return captured.pop("masters", [])

    return _inner
