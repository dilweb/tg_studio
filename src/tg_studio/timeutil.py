"""Единая семантика времени в приложении.

База хранит UTC: колонки без таймзоны пинятся к UTC-сессии Postgres
(db/session.py) и читаются как aware UTC (db/types.UTCDateTime), поэтому
API отдаёт ISO со смещением, а фронтенд конвертирует его в локальное время
зрителя. Студия живёт в Asia/Almaty (UTC+5, с 2024-03 Казахстан весь на UTC+5):
«сегодня» для промптов и человекочитаемые времена считаем в ней.
"""

from datetime import UTC, date, datetime
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
