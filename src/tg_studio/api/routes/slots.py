from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from tg_studio.api.deps import SessionDep
from tg_studio.db.models import Master, MasterService, Service, WorkSchedule
from tg_studio.services.slots import get_available_slots

router = APIRouter(prefix="/slots", tags=["slots"])

MAX_DAYS_RANGE = 60  # максимум 2 месяца за один запрос


@router.get("/masters")
async def list_masters(session: SessionDep):
    result = await session.execute(select(Master).where(Master.is_active.is_(True)))
    masters = result.scalars().all()
    return [
        {"id": m.id, "full_name": m.full_name, "description": m.description}
        for m in masters
    ]


@router.get("/masters/{master_id}/services")
async def list_master_services(master_id: int, session: SessionDep):
    result = await session.execute(
        select(Service)
        .join(MasterService, MasterService.service_id == Service.id)
        .where(MasterService.master_id == master_id, Service.is_active.is_(True))
    )
    services = result.scalars().all()
    return [
        {
            "id": s.id,
            "name": s.name,
            "description": s.description,
            "price_per_hour": float(s.price_per_hour),
            "prepayment_percent": s.prepayment_percent,
            "cancel_deadline_hours": s.cancel_deadline_hours,
        }
        for s in services
    ]


@router.get("/masters/{master_id}/schedule")
async def get_master_schedule(master_id: int, session: SessionDep):
    """Расписание мастера по дням недели — для отображения на календаре."""
    result = await session.execute(
        select(WorkSchedule).where(WorkSchedule.master_id == master_id)
    )
    schedules = result.scalars().all()
    weekday_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    return [
        {
            "weekday": s.weekday,
            "weekday_name": weekday_names[s.weekday],
            "start_time": s.start_time,
            "end_time": s.end_time,
            "slot_duration_minutes": s.slot_duration_minutes,
        }
        for s in sorted(schedules, key=lambda x: x.weekday)
    ]


@router.get("/available")
async def list_available_slots(
    session: SessionDep,
    master_id: int = Query(...),
    from_date: date = Query(..., description="С какой даты, YYYY-MM-DD"),
    to_date: date = Query(None, description="По какую дату, YYYY-MM-DD (по умолчанию = from_date)"),
):
    """
    Вернуть свободные слоты мастера за период.

    Слоты вычисляются на лету из расписания (WorkSchedule) минус уже занятые брони.
    Можно запросить один день (?from_date=2026-03-10) или диапазон (?from_date=...&to_date=...).
    Максимальный диапазон — 60 дней.
    """
    if to_date is None:
        to_date = from_date

    if to_date < from_date:
        raise HTTPException(status_code=400, detail="to_date must be >= from_date")

    if (to_date - from_date).days > MAX_DAYS_RANGE:
        raise HTTPException(
            status_code=400,
            detail=f"Date range too large, max {MAX_DAYS_RANGE} days",
        )

    slots = await get_available_slots(session, master_id, from_date, to_date)
    return slots


@router.get("/available/month")
async def list_available_days_in_month(
    session: SessionDep,
    master_id: int = Query(...),
    year: int = Query(...),
    month: int = Query(..., ge=1, le=12),
):
    """
    Вернуть список дней в месяце, у которых есть хотя бы один свободный слот.
    Используется для подсветки доступных дней на календаре.
    """
    from_date = date(year, month, 1)
    # последний день месяца
    if month == 12:
        to_date = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        to_date = date(year, month + 1, 1) - timedelta(days=1)

    slots = await get_available_slots(session, master_id, from_date, to_date)

    available_days = sorted({s["starts_at"][:10] for s in slots})
    return {"available_days": available_days}
