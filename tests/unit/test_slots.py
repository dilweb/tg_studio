"""
Unit tests for slot generation logic (modules/scheduling/service.py).

Only pure-Python helpers are tested here — no database needed.
"""

from datetime import date, time, timedelta
from zoneinfo import ZoneInfo

from tg_studio.db.models import WorkSchedule
from tg_studio.modules.scheduling.service import _generate_day_slots, _parse_time

TZ = ZoneInfo("Asia/Almaty")
MONDAY = date(2026, 4, 6)  # known Monday used in slot tests


def _make_schedule(
    start_time: str = "10:00",
    end_time: str = "18:00",
    slot_duration_minutes: int = 60,
    master_id: int = 1,
    weekday: int = 0,
) -> WorkSchedule:
    """Instantiate a WorkSchedule without touching the database."""
    s = WorkSchedule()
    s.id = 1
    s.master_id = master_id
    s.weekday = weekday
    s.start_time = start_time
    s.end_time = end_time
    s.slot_duration_minutes = slot_duration_minutes
    return s


class TestParseTime:
    def test_standard_hour(self):
        assert _parse_time("10:00") == time(10, 0)

    def test_midnight(self):
        assert _parse_time("00:00") == time(0, 0)

    def test_end_of_day(self):
        assert _parse_time("23:59") == time(23, 59)

    def test_leading_zero_minutes(self):
        assert _parse_time("09:05") == time(9, 5)

    def test_half_hour(self):
        assert _parse_time("14:30") == time(14, 30)


class TestGenerateDaySlots:
    def test_8h_workday_60min_slots_gives_8_slots(self):
        schedule = _make_schedule("10:00", "18:00", 60)
        slots = _generate_day_slots(1, MONDAY, schedule)
        assert len(slots) == 8

    def test_first_slot_starts_at_work_start(self):
        schedule = _make_schedule("09:00", "17:00", 60)
        slots = _generate_day_slots(1, MONDAY, schedule)
        starts_at, _ = slots[0]
        assert starts_at.hour == 9
        assert starts_at.minute == 0

    def test_last_slot_ends_exactly_at_end_time(self):
        schedule = _make_schedule("10:00", "12:00", 60)
        slots = _generate_day_slots(1, MONDAY, schedule)
        assert len(slots) == 2
        _, last_end = slots[-1]
        assert last_end.hour == 12
        assert last_end.minute == 0

    def test_30min_slots_produces_double_count(self):
        schedule = _make_schedule("10:00", "12:00", 30)
        slots = _generate_day_slots(1, MONDAY, schedule)
        assert len(slots) == 4

    def test_slot_longer_than_workday_returns_empty(self):
        schedule = _make_schedule("10:00", "10:30", 60)
        slots = _generate_day_slots(1, MONDAY, schedule)
        assert slots == []

    def test_non_divisible_day_no_partial_slot(self):
        # 90-minute window, 60-minute slots → only 1 slot; last 30 min is discarded
        schedule = _make_schedule("10:00", "11:30", 60)
        slots = _generate_day_slots(1, MONDAY, schedule)
        assert len(slots) == 1

    def test_slots_carry_correct_timezone(self):
        schedule = _make_schedule("09:00", "10:00", 60)
        slots = _generate_day_slots(1, MONDAY, schedule)
        starts_at, ends_at = slots[0]
        assert starts_at.tzinfo is not None
        assert ends_at.tzinfo is not None

    def test_consecutive_slots_are_adjacent(self):
        schedule = _make_schedule("10:00", "13:00", 60)
        slots = _generate_day_slots(1, MONDAY, schedule)
        for i in range(len(slots) - 1):
            _, prev_end = slots[i]
            next_start, _ = slots[i + 1]
            assert next_start == prev_end

    def test_slot_duration_matches_schedule(self):
        schedule = _make_schedule("10:00", "18:00", 90)
        slots = _generate_day_slots(1, MONDAY, schedule)
        for starts_at, ends_at in slots:
            duration = ends_at - starts_at
            assert duration == timedelta(minutes=90)
