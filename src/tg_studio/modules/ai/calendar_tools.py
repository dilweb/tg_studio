"""
Structured, filter-based calendar tool for the agent.

Bookings live only in Google Calendar (no local Booking table), so the agent
cannot reach the schedule via `execute_analytics_sql`. This tool is a thin
wrapper over `list_bookings` (below): the model sends only optional
filters (master name, date window) and gets back the BUSY intervals with
labels — free time is inferred by the model.
"""

import logging
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from tg_studio.db.models import Business, Master
from tg_studio.db.session import async_session_factory
from tg_studio.modules.google_calendar.client import list_events

logger = logging.getLogger(__name__)

# часовой пояс студии — все календарные времена в нём (Алматы, UTC+6)
TZ = ZoneInfo("Asia/Almaty")

DEFAULT_WINDOW_DAYS = 14
MAX_WINDOW_DAYS = 31  # инклюзивно, календарных дней — защита контекста от «весь год»
MAX_BOOKINGS = 50
MAX_SUMMARY_LEN = 80


class WindowRangeError(ValueError):
    """date_to раньше date_from."""


def _today() -> date:
    """«Сегодня» в таймзоне студии (не date.today() — UTC-сервер съедет утром)."""
    return datetime.now(TZ).date()


def _parse_date(value: str, param: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{param} must be YYYY-MM-DD, got {value!r}") from exc


def _resolve_window(
    date_from: str | None, date_to: str | None, today: date | None = None
) -> tuple[date, date, bool]:
    """Опциональные даты → (от, до, clamped). Отсутствие дат = ближайшие 2 недели."""
    today = today or _today()
    frm = today if date_from is None else _parse_date(date_from, "date_from")
    to = (
        frm + timedelta(days=DEFAULT_WINDOW_DAYS)
        if date_to is None
        else _parse_date(date_to, "date_to")
    )
    if to < frm:
        raise WindowRangeError("date_to must be >= date_from")
    clamped = False
    if (to - frm).days + 1 > MAX_WINDOW_DAYS:
        to, clamped = frm + timedelta(days=MAX_WINDOW_DAYS - 1), True
    return frm, to, clamped


def _master_brief(master: Master) -> dict:
    return {"id": master.id, "name": master.full_name}


async def _load_business(session, business_id: int) -> Business | None:
    return await session.get(Business, business_id)


async def _find_masters(session, business_id: int, name: str) -> list[Master]:
    result = await session.execute(
        select(Master).where(
            Master.business_id == business_id,
            Master.full_name.ilike(f"%{name}%"),
        )
    )
    return list(result.scalars().all())


async def _all_active_masters(session, business_id: int) -> list[Master]:
    result = await session.execute(
        select(Master).where(
            Master.business_id == business_id,
            Master.is_active.is_(True),
        )
    )
    return list(result.scalars().all())


async def _get_master(session, business_id: int, master_id: int) -> Master:
    master = await session.get(Master, master_id)
    if not master or master.business_id != business_id:
        raise ValueError("Мастер не найден")
    return master


def _event_to_booking(event: dict) -> dict:
    props = (event.get("extendedProperties") or {}).get("private", {})
    return {
        "event_id": event["id"],
        "master_id": int(props["master_id"]) if "master_id" in props else None,
        "service_name": props.get("service_name") or None,
        "client_name": props.get("client_name") or None,
        "client_phone": props.get("client_phone") or None,
        "starts_at": event["start"].get("dateTime", event["start"].get("date")),
        "ends_at": event["end"].get("dateTime", event["end"].get("date")),
        "summary": event.get("summary"),
    }


async def list_bookings(
    session,
    business: Business,
    master_id: int | None,
    from_date: datetime,
    to_date: datetime,
) -> list[dict]:
    """Записи одного мастера (master_id задан) или все записи бизнеса (None).

    События читаются из календаря каждого мастера; у мастеров без своего
    календаря — из primary, там их размечает extendedProperties.master_id.
    """
    if not business.google_calendar_credentials_json:
        return []

    if master_id is not None:
        master = await _get_master(session, business.id, master_id)
        masters = [master]
    else:
        result = await session.execute(
            select(Master).where(
                Master.business_id == business.id,
                Master.is_active.is_(True),
            )
        )
        masters = list(result.scalars().all())
        if not masters:
            return []

    by_event: dict[str, dict] = {}
    for m in masters:
        events = await list_events(
            business.google_calendar_credentials_json,
            time_min=from_date.isoformat(),
            time_max=to_date.isoformat(),
            calendar_id=m.google_calendar_id or "primary",
        )
        for e in events:
            booking = _event_to_booking(e)
            # событие в общем primary не размечено — считаем его записью мастера,
            # в чей календарь смотрим; повторный заход (тот же primary у другого
            # мастера) только дозаполняет разметку, дублей по event_id нет
            if booking["master_id"] is None:
                booking["master_id"] = m.id
            existing = by_event.get(booking["event_id"])
            if existing is None:
                by_event[booking["event_id"]] = booking
            elif existing["master_id"] is None:
                existing["master_id"] = booking["master_id"]

    names = {m.id: m.full_name for m in masters}
    bookings = list(by_event.values())
    for b in bookings:
        b["master_name"] = names.get(b["master_id"]) if b["master_id"] is not None else None
    return sorted(bookings, key=lambda b: b["starts_at"])


async def get_bookings(
    business_id: int,
    master: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict:
    """Записи бизнеса из Google Calendar за окно дат, опционально одного мастера.

    Все параметры — опциональные фильтры: без них окно = ближайшие 2 недели,
    мастера = все активные. Ожидаемые проблемы отдаёт как {"error": ...},
    чтобы модель исправилась сама.
    """
    try:
        frm, to, clamped = _resolve_window(date_from, date_to)
    except WindowRangeError:
        return {"error": "date_range_invalid", "hint": "date_to must be >= date_from"}
    except ValueError as exc:
        return {"error": "bad_date_format", "hint": str(exc)}

    async with async_session_factory() as session:
        business = await _load_business(session, business_id)
        if business is None:
            return {"error": "business_not_found"}
        if not business.google_calendar_credentials_json:
            return {
                "error": "calendar_not_connected",
                "hint": "Google Calendar is not connected for this business",
            }

        master_id: int | None = None
        master_info: dict | None = None
        if master and master.strip():
            matches = await _find_masters(session, business_id, master.strip())
            if not matches:
                everyone = await _all_active_masters(session, business_id)
                return {
                    "error": "master_not_found",
                    "available_masters": [_master_brief(m) for m in everyone],
                }
            if len(matches) > 1:
                return {
                    "error": "ambiguous_master",
                    "candidates": [_master_brief(m) for m in matches],
                }
            master_id = matches[0].id
            master_info = _master_brief(matches[0])

        # верхняя граница end-exclusive — чтобы день date_to покрыт целиком
        bookings = await list_bookings(
            session,
            business,
            master_id,
            datetime.combine(frm, time.min, tzinfo=TZ),
            datetime.combine(to + timedelta(days=1), time.min, tzinfo=TZ),
        )

    truncated = len(bookings) > MAX_BOOKINGS
    logger.info(
        "AI get_bookings — business_id=%s master=%r window=%s..%s count=%s",
        business_id,
        master,
        frm,
        to,
        len(bookings),
    )
    return {
        "window": {"from": frm.isoformat(), "to": to.isoformat(), "clamped": clamped},
        "master": master_info,
        "booking_count": len(bookings),
        "truncated": truncated,
        "bookings": [
            {
                "master_name": b.get("master_name"),
                "starts_at": b["starts_at"],
                "ends_at": b["ends_at"],
                "service_name": b.get("service_name"),
                "client_name": b.get("client_name"),
                "summary": (b.get("summary") or "")[:MAX_SUMMARY_LEN] or None,
            }
            for b in bookings[:MAX_BOOKINGS]
        ],
    }
