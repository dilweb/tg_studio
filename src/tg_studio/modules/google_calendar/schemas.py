from typing import Literal

from pydantic import BaseModel, EmailStr


class GoogleCalendarConnectResponse(BaseModel):
    """Result of connecting Google Calendar via a service-account key."""
    email: str
    status: str = "connected"


class GoogleCalendarStatusResponse(BaseModel):
    """Current Google Calendar connection status."""
    connected: bool
    email: str | None = None
    share_email: str | None = None


class GoogleCalendarShareRequest(BaseModel):
    """Owner's personal Google address to share the calendars with."""
    email: EmailStr
    role: Literal["reader", "writer"] = "writer"


class GoogleCalendarSharedCalendar(BaseModel):
    """Per-calendar share result."""
    calendar_id: str
    ok: bool
    error: str | None = None


class GoogleCalendarShareResponse(BaseModel):
    """Result of sharing the business calendars with a personal address."""
    email: str
    shared: list[GoogleCalendarSharedCalendar]


class GoogleCalendarEvent(BaseModel):
    """Represents a calendar event to create/update."""
    summary: str
    description: str | None = None
    start_datetime: str  # ISO 8601
    end_datetime: str    # ISO 8601
    timezone: str = "Asia/Almaty"
    attendees: list[str] | None = None
