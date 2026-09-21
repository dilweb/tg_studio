"""
Booking service — Google Calendar is the only store, no local Booking table.

Shared by the HTTP API (owner/master) and, later, an AI-agent tool wrapper —
both call these same functions so booking logic exists in exactly one place.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_studio.db.models import Business, Master
from tg_studio.modules.google_calendar.client import (
    cancel_event,
    create_event,
    get_busy_periods,
    list_events,
)

DEFAULT_DURATION_MINUTES = 60

# часовой пояс студии — все календарные времена в нём (Алматы, UTC+6)
TZ = ZoneInfo("Asia/Almaty")


class BookingError(Exception):
    """Booking validation failure — API layer maps this to an HTTP error."""


async def _get_master(session: AsyncSession, business_id: int, master_id: int) -> Master:
    master = await session.get(Master, master_id)
    if not master or master.business_id != business_id:
        raise BookingError("Мастер не найден")
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


async def create_booking(
    session: AsyncSession,
    business: Business,
    master_id: int,
    start_datetime: datetime,
    client_name: str,
    client_phone: str | None = None,
    service_name: str | None = None,
    duration_minutes: int | None = None,
) -> dict:
    """Длительность события: явно переданная > дефолт мастера > 60 минут."""
    if not business.google_calendar_credentials_json:
        raise BookingError("Google Calendar не подключен для этого бизнеса")

    master = await _get_master(session, business.id, master_id)
    calendar_id = master.google_calendar_id or "primary"

    duration = duration_minutes or master.default_duration_minutes or DEFAULT_DURATION_MINUTES
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
