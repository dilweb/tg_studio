import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import select

from tg_studio.api.deps import SessionDep
from tg_studio.api.routes.admin.deps import OwnerBusinessDep
from tg_studio.db.models import Master, WorkSchedule

router = APIRouter(prefix="/masters/{master_id}/schedule", tags=["admin • schedule"])

_TIME_RE = re.compile(r"^\d{2}:\d{2}$")
WEEKDAY_NAMES = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


class ScheduleEntry(BaseModel):
    weekday: int = 0           # 0=пн, 6=вс
    start_time: str = "10:00"  # "HH:MM"
    end_time: str = "18:00"
    slot_duration_minutes: int = 60

    @field_validator("weekday")
    @classmethod
    def check_weekday(cls, v: int) -> int:
        if not 0 <= v <= 6:
            raise ValueError("weekday must be 0–6")
        return v

    @field_validator("start_time", "end_time")
    @classmethod
    def check_time_format(cls, v: str) -> str:
        if not _TIME_RE.match(v):
            raise ValueError("time must be HH:MM")
        h, m = v.split(":")
        if not (0 <= int(h) <= 23 and 0 <= int(m) <= 59):
            raise ValueError("invalid time value")
        return v


class ScheduleResponse(BaseModel):
    weekday: int
    weekday_name: str
    start_time: str
    end_time: str
    slot_duration_minutes: int


def _to_response(s: WorkSchedule) -> ScheduleResponse:
    return ScheduleResponse(
        weekday=s.weekday,
        weekday_name=WEEKDAY_NAMES[s.weekday],
        start_time=s.start_time,
        end_time=s.end_time,
        slot_duration_minutes=s.slot_duration_minutes,
    )


@router.get("", response_model=list[ScheduleResponse])
async def get_schedule(
    master_id: int,
    session: SessionDep,
    business: OwnerBusinessDep,
):
    await _get_own_master(session, master_id, business.id)
    result = await session.execute(
        select(WorkSchedule)
        .where(WorkSchedule.master_id == master_id)
        .order_by(WorkSchedule.weekday)
    )
    return [_to_response(s) for s in result.scalars().all()]


@router.put("", response_model=list[ScheduleResponse])
async def set_schedule(
    master_id: int,
    body: list[ScheduleEntry],
    session: SessionDep,
    business: OwnerBusinessDep,
):
    """
    Полностью заменить расписание мастера.
    Передай список рабочих дней — старое расписание удалится.
    Пример: [{"weekday": 0, "start_time": "10:00", "end_time": "18:00"}]
    """
    await _get_own_master(session, master_id, business.id)

    # Проверить дублирующиеся дни недели в запросе
    weekdays = [e.weekday for e in body]
    if len(weekdays) != len(set(weekdays)):
        raise HTTPException(status_code=400, detail="Дублирующиеся дни недели в запросе")

    # Проверить что start < end для каждого дня
    for entry in body:
        if entry.start_time >= entry.end_time:
            raise HTTPException(
                status_code=400,
                detail=f"{WEEKDAY_NAMES[entry.weekday]}: start_time должен быть раньше end_time",
            )

    # Удалить старое расписание
    old = await session.execute(
        select(WorkSchedule).where(WorkSchedule.master_id == master_id)
    )
    for row in old.scalars().all():
        await session.delete(row)
        await session.flush()

    # Записать новое
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

    return [_to_response(e) for e in sorted(new_entries, key=lambda x: x.weekday)]


@router.delete("/{weekday}", status_code=204)
async def remove_day(
    master_id: int,
    weekday: int,
    session: SessionDep,
    business: OwnerBusinessDep,
):
    """Убрать один рабочий день из расписания (0=пн, 6=вс)."""
    await _get_own_master(session, master_id, business.id)
    result = await session.execute(
        select(WorkSchedule).where(
            WorkSchedule.master_id == master_id,
            WorkSchedule.weekday == weekday,
        )
    )
    entry = result.scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=404, detail="Этот день не найден в расписании")
    await session.delete(entry)
    await session.commit()


async def _get_own_master(session, master_id: int, business_id: int) -> Master:
    master = await session.get(Master, master_id)
    if master is None or master.business_id != business_id:
        raise HTTPException(status_code=404, detail="Мастер не найден")
    return master
