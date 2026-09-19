from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from tg_studio.api.admin_deps import OwnerBusinessDep
from tg_studio.api.deps import SessionDep
from tg_studio.db.models import Master, WorkSchedule
from tg_studio.modules.google_calendar.client import get_busy_periods
from tg_studio.modules.scheduling.schemas import WEEKDAY_NAMES, ScheduleEntry, ScheduleResponse
from tg_studio.modules.scheduling.service import TZ, get_available_slots

router = APIRouter()

MAX_DAYS_RANGE = 60


async def _fetch_busy_ranges(
    session: SessionDep, master_id: int, from_date: date, to_date: date
) -> list[tuple[datetime, datetime]]:
    """Занятые периоды мастера — из Google Calendar его студии."""
    result = await session.execute(
        select(Master).where(Master.id == master_id).options(selectinload(Master.business))
    )
    master = result.scalar_one_or_none()
    if not master or not master.business.google_calendar_credentials_json:
        return []

    time_min = datetime.combine(from_date, time.min, tzinfo=TZ).isoformat()
    time_max = datetime.combine(to_date + timedelta(days=1), time.min, tzinfo=TZ).isoformat()
    return await get_busy_periods(
        master.business.google_calendar_credentials_json,
        time_min,
        time_max,
        calendar_id=master.google_calendar_id or "primary",
    )

# ── Public: slots ─────────────────────────────────────────────────────────────

_public = APIRouter(prefix="/slots", tags=["slots"])


@_public.get("/masters")
async def list_masters(session: SessionDep):
    result = await session.execute(select(Master).where(Master.is_active.is_(True)))
    return [{"id": m.id, "full_name": m.full_name, "description": m.description} for m in result.scalars().all()]


@_public.get("/masters/{master_id}/schedule")
async def get_master_schedule(master_id: int, session: SessionDep):
    result = await session.execute(select(WorkSchedule).where(WorkSchedule.master_id == master_id))
    return [
        {
            "weekday": s.weekday,
            "weekday_name": WEEKDAY_NAMES[s.weekday],
            "start_time": s.start_time,
            "end_time": s.end_time,
            "slot_duration_minutes": s.slot_duration_minutes,
        }
        for s in sorted(result.scalars().all(), key=lambda x: x.weekday)
    ]


@_public.get("/available")
async def list_available_slots(
    session: SessionDep,
    master_id: int = Query(...),
    from_date: date = Query(...),
    to_date: date = Query(None),
):
    if to_date is None:
        to_date = from_date
    if to_date < from_date:
        raise HTTPException(status_code=400, detail="to_date must be >= from_date")
    if (to_date - from_date).days > MAX_DAYS_RANGE:
        raise HTTPException(status_code=400, detail=f"Date range too large, max {MAX_DAYS_RANGE} days")
    busy_ranges = await _fetch_busy_ranges(session, master_id, from_date, to_date)
    return await get_available_slots(session, master_id, from_date, to_date, busy_ranges)


@_public.get("/available/month")
async def list_available_days_in_month(
    session: SessionDep,
    master_id: int = Query(...),
    year: int = Query(...),
    month: int = Query(..., ge=1, le=12),
):
    from_date = date(year, month, 1)
    to_date = date(year + 1, 1, 1) - timedelta(days=1) if month == 12 else date(year, month + 1, 1) - timedelta(days=1)
    busy_ranges = await _fetch_busy_ranges(session, master_id, from_date, to_date)
    slots = await get_available_slots(session, master_id, from_date, to_date, busy_ranges)
    return {"available_days": sorted({s["starts_at"][:10] for s in slots})}


# ── Admin: schedule ───────────────────────────────────────────────────────────

_admin = APIRouter(prefix="/admin/masters/{master_id}/schedule", tags=["admin • schedule"])


def _schedule_to_response(s: WorkSchedule) -> ScheduleResponse:
    return ScheduleResponse(
        weekday=s.weekday,
        weekday_name=WEEKDAY_NAMES[s.weekday],
        start_time=s.start_time,
        end_time=s.end_time,
        slot_duration_minutes=s.slot_duration_minutes,
    )


async def _get_own_master(session, master_id: int, business_id: int):
    from tg_studio.db.models import Master
    master = await session.get(Master, master_id)
    if master is None or master.business_id != business_id:
        raise HTTPException(status_code=404, detail="Мастер не найден")
    return master


@_admin.get("", response_model=list[ScheduleResponse])
async def get_schedule(master_id: int, session: SessionDep, business: OwnerBusinessDep):
    await _get_own_master(session, master_id, business.id)
    result = await session.execute(
        select(WorkSchedule).where(WorkSchedule.master_id == master_id).order_by(WorkSchedule.weekday)
    )
    return [_schedule_to_response(s) for s in result.scalars().all()]


@_admin.put("", response_model=list[ScheduleResponse])
async def set_schedule(master_id: int, body: list[ScheduleEntry], session: SessionDep, business: OwnerBusinessDep):
    await _get_own_master(session, master_id, business.id)
    weekdays = [e.weekday for e in body]
    if len(weekdays) != len(set(weekdays)):
        raise HTTPException(status_code=400, detail="Дублирующиеся дни недели в запросе")
    for entry in body:
        if entry.start_time >= entry.end_time:
            raise HTTPException(status_code=400, detail=f"{WEEKDAY_NAMES[entry.weekday]}: start_time должен быть раньше end_time")
    old = await session.execute(select(WorkSchedule).where(WorkSchedule.master_id == master_id))
    for row in old.scalars().all():
        await session.delete(row)
        await session.flush()
    new_entries = [
        WorkSchedule(
            master_id=master_id,
            weekday=e.weekday,
            start_time=e.start_time,
            end_time=e.end_time,
            slot_duration_minutes=e.slot_duration_minutes,
        )
        for e in body
    ]
    session.add_all(new_entries)
    await session.commit()
    return [_schedule_to_response(e) for e in sorted(new_entries, key=lambda x: x.weekday)]


@_admin.delete("/{weekday}", status_code=204)
async def remove_day(master_id: int, weekday: int, session: SessionDep, business: OwnerBusinessDep):
    await _get_own_master(session, master_id, business.id)
    result = await session.execute(
        select(WorkSchedule).where(WorkSchedule.master_id == master_id, WorkSchedule.weekday == weekday)
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=404, detail="Этот день не найден в расписании")
    await session.delete(entry)
    await session.commit()


# ── Assemble ──────────────────────────────────────────────────────────────────

router.include_router(_public)
router.include_router(_admin)
