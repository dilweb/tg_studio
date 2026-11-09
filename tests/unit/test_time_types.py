"""Семантика времени: БД хранит UTC, студия живёт в Asia/Almaty.

UTCDateTime — декоратор naive-колонок (db/types.py): чтение возвращает
aware UTC, запись aware-значений приводит к UTC wall-clock. Без этого API
отдавал наивный ISO, а фронтенд парсил его как локальное время — в Алматы
часы уезжали на 5 часов назад.
"""

from datetime import UTC, datetime

from tg_studio.db.types import UTCDateTime
from tg_studio.timeutil import STUDIO_TZ, studio_today, to_studio_local


def test_result_naive_becomes_utc_aware():
    dt = UTCDateTime().process_result_value(datetime(2026, 10, 26, 7, 47), None)
    assert dt == datetime(2026, 10, 26, 7, 47, tzinfo=UTC)


def test_result_aware_passthrough():
    aware = datetime(2026, 10, 26, 12, 47, tzinfo=STUDIO_TZ)
    assert UTCDateTime().process_result_value(aware, None) is aware


def test_result_none_passthrough():
    assert UTCDateTime().process_result_value(None, None) is None


def test_bind_aware_stored_as_utc_wallclock():
    aware = datetime(2026, 10, 26, 12, 47, tzinfo=STUDIO_TZ)  # 12:47 Almaty = 07:47 UTC
    bound = UTCDateTime().process_bind_param(aware, None)
    assert bound == datetime(2026, 10, 26, 7, 47)  # naive UTC wall-clock


def test_bind_naive_passthrough():
    naive = datetime(2026, 10, 26, 7, 47)
    assert UTCDateTime().process_bind_param(naive, None) is naive


def test_to_studio_local_converts_utc():
    utc_dt = datetime(2026, 10, 26, 7, 47, tzinfo=UTC)
    assert to_studio_local(utc_dt) == datetime(2026, 10, 26, 12, 47, tzinfo=STUDIO_TZ)


def test_to_studio_local_naive_assumed_utc():
    assert to_studio_local(datetime(2026, 10, 26, 7, 47)) == datetime(
        2026, 10, 26, 12, 47, tzinfo=STUDIO_TZ
    )


def test_studio_today_is_date():
    assert studio_today() == datetime.now(STUDIO_TZ).date()
