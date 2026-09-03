"""
Google Calendar sync service.

Responsible for pushing booking events to Google Calendar
and cancelling them when bookings are cancelled.
"""
from tg_studio.db.models import Booking
from tg_studio.modules.google_calendar.client import (
    cancel_event,
    create_event,
    update_event,
)


async def sync_booking_to_calendar(
    credentials_json: str,
    booking: Booking,
    master_name: str,
    client_name: str,
    service_name: str,
    business_name: str,
) -> str | None:
    """
    Create or update a Google Calendar event for a confirmed booking.

    Returns the Google Calendar event ID, or None on failure.
    """
    if not booking.slot:
        return None

    summary = f"{service_name} — {client_name} @ {master_name}"
    description = (
        f"Бронь #{booking.id}\n"
        f"Студия: {business_name}\n"
        f"Мастер: {master_name}\n"
        f"Клиент: {client_name}\n"
        f"Услуга: {service_name}\n"
        f"Сумма: {booking.total_amount} KZT"
    )

    start_dt = booking.slot.starts_at.isoformat()
    end_dt = booking.slot.ends_at.isoformat()

    if booking.google_event_id:
        # Update existing event
        success = await update_event(
            credentials_json=credentials_json,
            event_id=booking.google_event_id,
            summary=summary,
            description=description,
            start_datetime=start_dt,
            end_datetime=end_dt,
        )
        return booking.google_event_id if success else None
    else:
        # Create new event
        event_id = await create_event(
            credentials_json=credentials_json,
            summary=summary,
            description=description,
            start_datetime=start_dt,
            end_datetime=end_dt,
        )
        return event_id


async def cancel_booking_in_calendar(
    credentials_json: str,
    google_event_id: str | None,
) -> bool:
    """Remove a booking's calendar event."""
    if not google_event_id:
        return True  # Nothing to cancel
    return await cancel_event(credentials_json, google_event_id)