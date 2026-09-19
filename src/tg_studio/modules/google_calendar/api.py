"""
Google Calendar service-account connect and sync API endpoints.

Owners can:
1. POST /api/admin/google-calendar/connect — connect via the service-account key
2. GET /api/admin/google-calendar/status — check connection status
3. POST/DELETE /api/admin/google-calendar/share — share the calendars with the
   owner's personal Google address (ACL), so bookings are visible there
4. DELETE /api/admin/google-calendar/disconnect — remove Google Calendar access
"""
import json

from fastapi import APIRouter, HTTPException
from googleapiclient.errors import HttpError
from sqlalchemy import select

from tg_studio.api.admin_deps import OwnerBusinessDep
from tg_studio.api.deps import SessionDep
from tg_studio.db.models import Master
from tg_studio.modules.google_calendar.client import (
    create_calendar,
    read_service_account_key,
    share_calendar,
    unshare_calendar,
    verify_service_account,
)
from tg_studio.modules.google_calendar.schemas import (
    GoogleCalendarConnectResponse,
    GoogleCalendarSharedCalendar,
    GoogleCalendarShareRequest,
    GoogleCalendarShareResponse,
    GoogleCalendarStatusResponse,
)

router = APIRouter(prefix="/admin/google-calendar", tags=["admin • google-calendar"])


@router.post("/connect", response_model=GoogleCalendarConnectResponse)
async def connect_service_account(
    session: SessionDep,
    business: OwnerBusinessDep,
):
    """
    Connect Google Calendar via the service-account key.

    The key file is read on the server, validated with a live Calendar API call
    (fails clearly if the key is rejected or the Calendar API is disabled for
    the project), then stored on the Business record.
    """
    try:
        key = read_service_account_key()
    except FileNotFoundError:
        raise HTTPException(
            status_code=500, detail="Файл ключа сервисного аккаунта не найден на сервере"
        ) from None
    except ValueError:
        raise HTTPException(
            status_code=500, detail="Файл ключа сервисного аккаунта повреждён"
        ) from None

    try:
        verify_service_account(key)
    except HttpError as exc:
        raise HTTPException(status_code=502, detail=f"Google отклонил ключ: {exc.reason}") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Не удалось проверить ключ: {exc}") from exc

    email = key.get("client_email", "")
    business.google_calendar_credentials_json = json.dumps(key, ensure_ascii=False)
    business.google_calendar_email = email
    await session.commit()

    return GoogleCalendarConnectResponse(email=email, status="connected")


@router.get("/status", response_model=GoogleCalendarStatusResponse)
async def google_calendar_status(business: OwnerBusinessDep):
    """Check whether Google Calendar is connected for this business."""
    if business.google_calendar_credentials_json:
        return GoogleCalendarStatusResponse(
            connected=True,
            email=business.google_calendar_email,
            share_email=business.google_share_email,
        )
    return GoogleCalendarStatusResponse(connected=False)


async def _business_master_calendars(session, business_id: int) -> list[str]:
    """Master calendar IDs — the only calendars that can be shared (an SA
    primary calendar cannot be shared at all: Google returns 403)."""
    result = await session.execute(
        select(Master.google_calendar_id).where(
            Master.business_id == business_id,
            Master.google_calendar_id.is_not(None),
        )
    )
    return list(result.scalars().all())


@router.post("/share", response_model=GoogleCalendarShareResponse)
async def share_google_calendar(
    body: GoogleCalendarShareRequest,
    session: SessionDep,
    business: OwnerBusinessDep,
):
    """
    Share every master calendar with the owner's personal Google address.
    The address is remembered so future master calendars are shared
    automatically.
    """
    if not business.google_calendar_credentials_json:
        raise HTTPException(status_code=400, detail="Google Calendar не подключен для этого бизнеса")

    email = str(body.email).lower()
    calendar_ids = await _business_master_calendars(session, business.id)
    if not calendar_ids:
        raise HTTPException(
            status_code=400,
            detail=(
                "Нет календарей мастеров — создайте их в разделе «Мастера». "
                "Записи мастеров без календаря попадают в primary сервисного "
                "аккаунта, который Google расшарить не позволяет."
            ),
        )

    shared = []
    for calendar_id in calendar_ids:
        try:
            await share_calendar(
                business.google_calendar_credentials_json, calendar_id, email, body.role
            )
            shared.append(GoogleCalendarSharedCalendar(calendar_id=calendar_id, ok=True))
        except HttpError as exc:
            shared.append(
                GoogleCalendarSharedCalendar(calendar_id=calendar_id, ok=False, error=exc.reason)
            )
        except Exception as exc:  # сеть и прочие сбои — не валим весь запрос
            shared.append(
                GoogleCalendarSharedCalendar(calendar_id=calendar_id, ok=False, error=str(exc))
            )

    if not any(item.ok for item in shared):
        raise HTTPException(
            status_code=502, detail=f"Google не принял доступ: {shared[0].error}"
        ) from None

    business.google_share_email = email
    await session.commit()
    return GoogleCalendarShareResponse(email=email, shared=shared)


@router.delete("/share", status_code=204)
async def unshare_google_calendar(
    session: SessionDep,
    business: OwnerBusinessDep,
):
    """Revoke the personal address's access to all business calendars."""
    if not business.google_calendar_credentials_json or not business.google_share_email:
        return

    calendar_ids = await _business_master_calendars(session, business.id)
    for calendar_id in calendar_ids:
        try:
            await unshare_calendar(business.google_calendar_credentials_json, calendar_id)
        except HttpError:
            continue  # best-effort: правило могло быть уже снято вручную

    business.google_share_email = None
    await session.commit()


@router.delete("/disconnect", status_code=204)
async def disconnect_google_calendar(
    session: SessionDep,
    business: OwnerBusinessDep,
):
    """Remove stored Google Calendar credentials and the personal share."""
    if business.google_calendar_credentials_json and business.google_share_email:
        calendar_ids = await _business_master_calendars(session, business.id)
        for calendar_id in calendar_ids:
            try:
                await unshare_calendar(business.google_calendar_credentials_json, calendar_id)
            except HttpError:
                continue  # best-effort: ключ мог стать невалидным

    business.google_calendar_credentials_json = None
    business.google_calendar_email = None
    business.google_share_email = None
    await session.commit()


@router.post("/masters/{master_id}/calendar", status_code=201)
async def create_master_calendar(
    master_id: int,
    session: SessionDep,
    business: OwnerBusinessDep,
):
    """
    Create a dedicated secondary calendar for a master, within the connected
    service account, and store its ID on the master.

    Requires the business to already be connected (POST .../connect first).
    """
    if not business.google_calendar_credentials_json:
        raise HTTPException(status_code=400, detail="Google Calendar не подключен для этого бизнеса")

    master = await session.get(Master, master_id)
    if master is None or master.business_id != business.id:
        raise HTTPException(status_code=404, detail="Мастер не найден")

    calendar_id = await create_calendar(
        business.google_calendar_credentials_json, summary=master.full_name
    )
    if not calendar_id:
        raise HTTPException(status_code=502, detail="Не удалось создать календарь в Google")

    master.google_calendar_id = calendar_id

    # Новый календарь сразу виден владельцу, если шаринг уже настроен
    share_error = None
    if business.google_share_email:
        try:
            await share_calendar(
                business.google_calendar_credentials_json,
                calendar_id,
                business.google_share_email,
            )
        except Exception as exc:
            share_error = str(exc)

    await session.commit()
    return {
        "master_id": master.id,
        "google_calendar_id": calendar_id,
        "share_error": share_error,
    }
