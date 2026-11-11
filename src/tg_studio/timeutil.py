"""Единая семантика времени в приложении.

База хранит UTC: колонки без таймзоны пинятся к UTC-сессии Postgres
(db/session.py) и читаются как aware UTC (db/types.UTCDateTime), поэтому
API отдаёт ISO со смещением, а фронтенд конвертирует его в локальное время
зрителя. Студия живёт в Asia/Almaty (UTC+5, с 2024-03 Казахстан весь на UTC+5):
«сегодня» для промптов и человекочитаемые времена считаем в ней.
"""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

STUDIO_TZ = ZoneInfo("Asia/Almaty")


def studio_now() -> datetime:
    return datetime.now(STUDIO_TZ)


def studio_today() -> date:
    """«Сегодня» по студии — не date.today(): UTC-сервер съедет календарный день."""
    return datetime.now(STUDIO_TZ).date()


def to_studio_local(dt: datetime) -> datetime:
    """В студийное время; naive считаем UTC (так хранится в БД)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(STUDIO_TZ)


def interval_bounds(day: date, open_t: time, close_t: time) -> tuple[datetime, datetime]:
    """Границы интервала времени суток в студийном времени. Конец 00:00 = до полуночи
    (в time нельзя записать 24:00)."""
    start = datetime.combine(day, open_t, tzinfo=STUDIO_TZ)
    end = datetime.combine(day, close_t, tzinfo=STUDIO_TZ)
    return start, end + timedelta(days=1) if close_t == time(0, 0) else end


def is_valid_window(time_from: time, time_to: time) -> bool:
    """Окно [from, to) в пределах суток: to=00:00 — до полуночи, иначе from < to."""
    return time_to == time(0, 0) or time_from < time_to
