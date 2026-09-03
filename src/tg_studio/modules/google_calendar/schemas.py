from pydantic import BaseModel


class GoogleCalendarAuthUrlResponse(BaseModel):
    """URL for the owner to authorize Google Calendar access."""
    auth_url: str


class GoogleCalendarTokenResponse(BaseModel):
    """Result after exchanging the authorization code."""
    email: str
    status: str = "connected"


class GoogleCalendarStatusResponse(BaseModel):
    """Current Google Calendar connection status."""
    connected: bool
    email: str | None = None


class GoogleCalendarEvent(BaseModel):
    """Represents a calendar event to create/update."""
    summary: str
    description: str | None = None
    start_datetime: str  # ISO 8601
    end_datetime: str    # ISO 8601
    timezone: str = "Asia/Almaty"
    attendees: list[str] | None = None