"""
Google Calendar OAuth2 and sync API endpoints.

Owners can:
1. GET /api/admin/google-calendar/auth-url — get the OAuth consent URL
2. GET /api/admin/google-calendar/callback — exchange auth code for tokens
3. GET /api/admin/google-calendar/status — check connection status
4. DELETE /api/admin/google-calendar/disconnect — remove Google Calendar access
"""
from fastapi import APIRouter, HTTPException, Query

from tg_studio.api.admin_deps import OwnerBusinessDep
from tg_studio.api.deps import SessionDep
from tg_studio.modules.google_calendar.client import (
    exchange_code,
    get_auth_url,
    get_calendar_email,
    credentials_to_storage_json,
)
from tg_studio.modules.google_calendar.schemas import (
    GoogleCalendarAuthUrlResponse,
    GoogleCalendarStatusResponse,
    GoogleCalendarTokenResponse,
)

router = APIRouter(prefix="/admin/google-calendar", tags=["admin • google-calendar"])


@router.get("/auth-url", response_model=GoogleCalendarAuthUrlResponse)
async def get_google_auth_url(business: OwnerBusinessDep):
    """
    Step 1: Get the Google OAuth2 consent URL.

    The owner must visit this URL in a browser, sign in to their Google account,
    and grant calendar access. After authorisation, Google redirects to the
    callback URL with an authorisation code.
    """
    state = str(business.id)
    auth_url = get_auth_url(state=state)
    return GoogleCalendarAuthUrlResponse(auth_url=auth_url)


@router.get("/callback", response_model=GoogleCalendarTokenResponse)
async def google_callback(
    session: SessionDep,
    business: OwnerBusinessDep,
    code: str = Query(...),
    state: str | None = Query(None),
):
    """
    Step 2: Exchange the authorisation code for tokens.

    Google redirects here after the owner grants access.
    The tokens are stored on the Business record for future use.
    """
    if state and str(business.id) != state:
        raise HTTPException(status_code=400, detail="State mismatch — possible CSRF attack")

    try:
        creds = exchange_code(code)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to exchange auth code: {exc}")

    email = get_calendar_email(creds)
    credentials_json = credentials_to_storage_json(creds)

    # Store on the business record
    business.google_calendar_credentials_json = credentials_json
    business.google_calendar_email = email
    await session.commit()

    return GoogleCalendarTokenResponse(email=email, status="connected")


@router.get("/status", response_model=GoogleCalendarStatusResponse)
async def google_calendar_status(business: OwnerBusinessDep):
    """Check whether Google Calendar is connected for this business."""
    if business.google_calendar_credentials_json:
        return GoogleCalendarStatusResponse(
            connected=True,
            email=business.google_calendar_email,
        )
    return GoogleCalendarStatusResponse(connected=False)


@router.delete("/disconnect", status_code=204)
async def disconnect_google_calendar(
    session: SessionDep,
    business: OwnerBusinessDep,
):
    """Remove stored Google Calendar credentials."""
    business.google_calendar_credentials_json = None
    business.google_calendar_email = None
    await session.commit()