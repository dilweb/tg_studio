"""
Booking service — Google Calendar is the only store, no local Booking table.

Shared by the HTTP API (owner/master) and, later, an AI-agent tool wrapper —
both call these same functions so booking logic exists in exactly one place.
"""
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_studio.db.models import Business, Master, WorkSchedule
from tg_studio.modules.google_calendar.client import (
    cancel_event,
    create_event,
    get_busy_periods,
    list_events,
)

DEFAULT_DURATION_MINUTES = 60


class BookingError(Exception):
    """Booking validation failure — API layer maps this to an HTTP error."""


async def _get_master(session: AsyncSession, business_id: int, master_id: int) -> Master:
    master = await session.get(Master, master_id)
    if not master or master.business_id != business_id:
        raise BookingError("Мастер не найден")
    return master


async def _slot_duration_minutes(session: AsyncSession, master_id: int, start: datetime) -> int:
    result = await session.execute(
        select(WorkSchedule.slot_duration_minutes).where(
            WorkSchedule.master_id == master_id, WorkSchedule.weekday == start.weekday()
        )
    )
    return result.scalar_one_or_none() or DEFAULT_DURATION_MINUTES


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


async def create_booking(
    session: AsyncSession,
    business: Business,
    master_id: int,
    start_datetime: datetime,
    client_name: str,
    client_phone: str | None = None,
    service_name: str | None = None,
) -> dict:
    if not business.google_calendar_credentials_json:
        raise BookingError("Google Calendar не подключен для этого бизнеса")

    master = await _get_master(session, business.id, master_id)
    calendar_id = master.google_calendar_id or "primary"

    duration = await _slot_duration_minutes(session, master_id, start_datetime)
    end_datetime = start_datetime + timedelta(minutes=duration)

    busy = await get_busy_periods(
        business.google_calendar_credentials_json,
        start_datetime.isoformat(),
        end_datetime.isoformat(),
        calendar_id=calendar_id,
    )
    if any(b_start < end_datetime and b_end > start_datetime for b_start, b_end in busy):
        raise BookingError("Слот уже занят")

    event_id = await create_event(
        credentials_json=business.google_calendar_credentials_json,
        summary=f"{service_name} — {client_name}" if service_name else client_name,
        description=(
            (f"Услуга: {service_name}\n" if service_name else "")
            + f"Клиент: {client_name}\nТелефон: {client_phone or '—'}"
        ),
        start_datetime=start_datetime.isoformat(),
        end_datetime=end_datetime.isoformat(),
        calendar_id=calendar_id,
        extended_properties={
            "master_id": str(master_id),
            "client_name": client_name,
            "client_phone": client_phone or "",
            **({"service_name": service_name} if service_name else {}),
        },
    )
    if not event_id:
        raise BookingError("Не удалось создать событие в Google Calendar")

    return {
        "event_id": event_id,
        "master_id": master_id,
        "service_name": service_name,
        "client_name": client_name,
        "client_phone": client_phone,
        "starts_at": start_datetime.isoformat(),
        "ends_at": end_datetime.isoformat(),
        "summary": f"{service_name} — {client_name}" if service_name else client_name,
    }


async def list_bookings(
    session: AsyncSession,
    business: Business,
    master_id: int,
    from_date: datetime,
    to_date: datetime,
) -> list[dict]:
    master = await _get_master(session, business.id, master_id)
    if not business.google_calendar_credentials_json:
        return []

    events = await list_events(
        business.google_calendar_credentials_json,
        time_min=from_date.isoformat(),
        time_max=to_date.isoformat(),
        calendar_id=master.google_calendar_id or "primary",
    )
    return [_event_to_booking(e) for e in events]


async def cancel_booking(
    session: AsyncSession, business: Business, master_id: int, event_id: str
) -> None:
    master = await _get_master(session, business.id, master_id)
    if not business.google_calendar_credentials_json:
        raise BookingError("Google Calendar не подключен для этого бизнеса")

    ok = await cancel_event(
        business.google_calendar_credentials_json,
        event_id,
        calendar_id=master.google_calendar_id or "primary",
    )
    if not ok:
        raise BookingError("Не удалось отменить запись")
