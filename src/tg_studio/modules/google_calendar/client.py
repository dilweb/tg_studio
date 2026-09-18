"""
Google Calendar API client wrapper.

Handles OAuth2 token lifecycle and provides a thin wrapper
around the google-api-python-client for calendar operations.
"""
import json
from datetime import datetime
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from tg_studio.config import settings

# Full read/write access to Google Calendar
SCOPES = ["https://www.googleapis.com/auth/calendar"]

# Path to the OAuth 2.0 Client ID file downloaded from Google Cloud Console
_CREDENTIALS_FILE = Path(settings.google_calendar_credentials_path)


def _make_flow() -> Flow:
    """Create a Flow from the client secrets file with the configured redirect URI."""
    flow = Flow.from_client_secrets_file(
        str(_CREDENTIALS_FILE),
        scopes=SCOPES,
        redirect_uri=settings.google_calendar_redirect_uri,
    )
    return flow


def _load_credentials(credentials_json: str | None) -> Credentials | None:
    """Restore a Credentials object from its JSON representation."""
    if not credentials_json:
        return None
    try:
        return Credentials.from_authorized_user_info(json.loads(credentials_json), SCOPES)
    except (ValueError, KeyError):
        return None


def _credentials_to_json(creds: Credentials) -> str:
    """Serialise a Credentials object to JSON for storage."""
    data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes,
    }
    return json.dumps(data, ensure_ascii=False)


def get_auth_url(state: str | None = None) -> str:
    """
    Generate the Google OAuth2 consent URL.

    The owner must visit this URL, sign in, and grant access.
    After granting, Google redirects to the redirect_uri with an auth code.
    """
    flow = _make_flow()

    authorization_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state,
    )
    return authorization_url


def exchange_code(authorization_code: str) -> Credentials:
    """
    Exchange the OAuth2 authorization code for tokens.

    Returns a Credentials object that can be serialised and stored.
    """
    flow = _make_flow()
    flow.fetch_token(code=authorization_code)
    return flow.credentials


def refresh_if_expired(creds: Credentials) -> Credentials:
    """Refresh the access token if it has expired."""
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return creds


def get_calendar_email(creds: Credentials) -> str:
    """
    Retrieve the primary email address associated with the authorised account.
    Uses the Google People API or the token info endpoint.
    """
    refresh_if_expired(creds)
    service = build("oauth2", "v2", credentials=creds)
    user_info = service.userinfo().get().execute()
    return user_info.get("email", "")


async def create_event(
    credentials_json: str,
    summary: str,
    description: str | None,
    start_datetime: str,
    end_datetime: str,
    timezone: str = "Asia/Almaty",
    attendees: list[str] | None = None,
    calendar_id: str = "primary",
    extended_properties: dict[str, str] | None = None,
) -> str | None:
    """
    Create a calendar event and return its event ID.

    `extended_properties` is stored as private metadata on the event (not visible
    in the Google Calendar UI) — used to read structured booking data back via
    list_events without a local DB table.

    Returns None on failure.
    """
    creds = _load_credentials(credentials_json)
    if not creds:
        return None

    refresh_if_expired(creds)

    try:
        service = build("calendar", "v3", credentials=creds)

        event_body = {
            "summary": summary,
            "description": description or "",
            "start": {
                "dateTime": start_datetime,
                "timeZone": timezone,
            },
            "end": {
                "dateTime": end_datetime,
                "timeZone": timezone,
            },
        }

        if attendees:
            event_body["attendees"] = [{"email": a} for a in attendees]
        if extended_properties:
            event_body["extendedProperties"] = {"private": extended_properties}

        created_event = service.events().insert(calendarId=calendar_id, body=event_body).execute()
        return created_event.get("id")

    except HttpError:
        return None


async def update_event(
    credentials_json: str,
    event_id: str,
    summary: str,
    description: str | None,
    start_datetime: str,
    end_datetime: str,
    timezone: str = "Asia/Almaty",
    attendees: list[str] | None = None,
    calendar_id: str = "primary",
) -> bool:
    """Update an existing calendar event."""
    creds = _load_credentials(credentials_json)
    if not creds:
        return False

    refresh_if_expired(creds)

    try:
        service = build("calendar", "v3", credentials=creds)

        event_body = {
            "summary": summary,
            "description": description or "",
            "start": {
                "dateTime": start_datetime,
                "timeZone": timezone,
            },
            "end": {
                "dateTime": end_datetime,
                "timeZone": timezone,
            },
        }

        if attendees:
            event_body["attendees"] = [{"email": a} for a in attendees]

        service.events().update(calendarId=calendar_id, eventId=event_id, body=event_body).execute()
        return True

    except HttpError:
        return False


async def cancel_event(credentials_json: str, event_id: str, calendar_id: str = "primary") -> bool:
    """Delete a calendar event by its ID."""
    creds = _load_credentials(credentials_json)
    if not creds:
        return False

    refresh_if_expired(creds)

    try:
        service = build("calendar", "v3", credentials=creds)
        service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
        return True
    except HttpError:
        return False


async def get_busy_periods(
    credentials_json: str,
    time_min: str,
    time_max: str,
    calendar_id: str = "primary",
) -> list[tuple[datetime, datetime]]:
    """
    Query Google Calendar free/busy for the given calendar in [time_min, time_max].

    Returns [] if there are no credentials or the API call fails — callers should
    treat that as "no known busy periods", not as an error.
    """
    creds = _load_credentials(credentials_json)
    if not creds:
        return []

    refresh_if_expired(creds)

    try:
        service = build("calendar", "v3", credentials=creds)
        result = service.freebusy().query(
            body={
                "timeMin": time_min,
                "timeMax": time_max,
                "items": [{"id": calendar_id}],
            }
        ).execute()
        busy = result["calendars"][calendar_id]["busy"]
        return [
            (datetime.fromisoformat(b["start"]), datetime.fromisoformat(b["end"]))
            for b in busy
        ]
    except HttpError:
        return []


async def list_events(
    credentials_json: str,
    time_min: str,
    time_max: str,
    calendar_id: str = "primary",
) -> list[dict]:
    """
    List events on a calendar in [time_min, time_max]. Returns [] on failure —
    callers should treat that as "no known bookings", not as an error.
    """
    creds = _load_credentials(credentials_json)
    if not creds:
        return []

    refresh_if_expired(creds)

    try:
        service = build("calendar", "v3", credentials=creds)
        result = service.events().list(
            calendarId=calendar_id,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
        ).execute()
        return result.get("items", [])
    except HttpError:
        return []


async def create_calendar(credentials_json: str, summary: str) -> str | None:
    """
    Create a new secondary calendar in the connected account and return its ID.

    Returns None on failure.
    """
    creds = _load_credentials(credentials_json)
    if not creds:
        return None

    refresh_if_expired(creds)

    try:
        service = build("calendar", "v3", credentials=creds)
        created = service.calendars().insert(body={"summary": summary}).execute()
        return created.get("id")
    except HttpError:
        return None


def credentials_to_storage_json(creds: Credentials) -> str:
    """Convert Credentials to JSON string for DB storage."""
    return _credentials_to_json(creds)