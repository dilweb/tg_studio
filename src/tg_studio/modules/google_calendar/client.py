"""
Google Calendar API client wrapper.

Google Calendar is accessed with a service account key (bnztattoo project):
the key JSON is stored on the Business record and credentials self-refresh
via JWT — no OAuth consent flow involved.
"""
import json
from datetime import datetime
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from tg_studio.config import settings

# Full read/write access to Google Calendar
SCOPES = ["https://www.googleapis.com/auth/calendar"]

# Path to the service-account key file downloaded from Google Cloud Console
_CREDENTIALS_FILE = Path(settings.google_calendar_credentials_path)


def read_service_account_key() -> dict:
    """Read the service-account key file (called once at connect time)."""
    return json.loads(_CREDENTIALS_FILE.read_text(encoding="utf-8"))


def verify_service_account(key: dict) -> None:
    """
    Check the key against Google before storing it: key is valid and the
    Calendar API is enabled for its project. Raises HttpError otherwise.
    """
    creds = service_account.Credentials.from_service_account_info(key, scopes=SCOPES)
    service = build("calendar", "v3", credentials=creds)
    service.calendarList().list(maxResults=1).execute()


def _load_credentials(credentials_json: str | None) -> Credentials | None:
    """
    Restore a Credentials object from its JSON representation.

    Stored JSON is normally a service-account key; the authorized-user branch
    stays for graceful reading of legacy OAuth-token rows.
    """
    if not credentials_json:
        return None
    try:
        info = json.loads(credentials_json)
        if info.get("type") == "service_account":
            return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
        return Credentials.from_authorized_user_info(info, SCOPES)
    except (ValueError, KeyError):
        return None


def refresh_if_expired(creds: Credentials) -> Credentials:
    """Refresh the access token if it has expired (SA keys re-sign the JWT)."""
    if creds and creds.expired:
        creds.refresh(Request())
    return creds


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


async def share_calendar(
    credentials_json: str,
    calendar_id: str,
    email: str,
    role: str = "writer",
) -> None:
    """
    Grant a personal address access to `calendar_id` (ACL rule), so bookings
    made inside the service account become visible in the owner's own Google
    Calendar. Raises HttpError on failure.

    Any previous personal rule is replaced, so the calendar is shared with
    exactly one address. Ownership rules (the SA's own access) are never touched.
    """
    creds = refresh_if_expired(_load_credentials(credentials_json))
    service = build("calendar", "v3", credentials=creds)

    rules = service.acl().list(calendarId=calendar_id).execute().get("items", [])
    for rule in rules:
        scope = rule.get("scope", {})
        if rule.get("role") == "owner":
            continue  # владение SA снимать нельзя — потеряем управление календарём
        if scope.get("type") == "user" and scope.get("value", "").lower() != email.lower():
            service.acl().delete(calendarId=calendar_id, ruleId=rule["id"]).execute()

    service.acl().insert(
        calendarId=calendar_id,
        body={"role": role, "scope": {"type": "user", "value": email}},
        sendNotifications=True,
    ).execute()


async def unshare_calendar(credentials_json: str, calendar_id: str) -> None:
    """
    Revoke all personal ACL rules from `calendar_id` (ownership rules are kept).
    Raises HttpError on failure.
    """
    creds = refresh_if_expired(_load_credentials(credentials_json))
    service = build("calendar", "v3", credentials=creds)

    rules = service.acl().list(calendarId=calendar_id).execute().get("items", [])
    for rule in rules:
        scope = rule.get("scope", {})
        if scope.get("type") == "user" and rule.get("role") != "owner":
            service.acl().delete(calendarId=calendar_id, ruleId=rule["id"]).execute()