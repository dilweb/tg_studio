"""
Генерация доступных слотов на лету.

Логика:
1. Берём расписание мастера (WorkSchedule) на запрошенный период
2. Разбиваем рабочее время на слоты по slot_duration_minutes
3. Вычитаем уже занятые периоды
4. Возвращаем свободные интервалы — никакой предварительной генерации не нужно
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from tg_studio.db.models import WorkSchedule

TZ = ZoneInfo("Asia/Almaty")


def _parse_time(t: str) -> time:
    h, m = t.split(":")
    return time(int(h), int(m))


def _generate_day_slots(
    master_id: int,
    day: date,
    schedule: WorkSchedule,
) -> list[tuple[datetime, datetime]]:
    """Нарезать рабочий день на равные интервалы."""
    start = datetime.combine(day, _parse_time(schedule.start_time), tzinfo=TZ)
    end = datetime.combine(day, _parse_time(schedule.end_time), tzinfo=TZ)
    step = timedelta(minutes=schedule.slot_duration_minutes)

    slots = []
    cursor = start
    while cursor + step <= end:
        slots.append((cursor, cursor + step))
        cursor += step
    return slots


async def get_available_slots(
    session: AsyncSession,
    master_id: int,
    from_date: date,
    to_date: date,
    booked_ranges: list[tuple[datetime, datetime]] | None = None,
) -> list[dict]:
    """
    Вернуть список свободных слотов для мастера на период [from_date, to_date].

    Результат — список словарей вида:
      { "starts_at": "2026-03-10T10:00:00+06:00",
        "ends_at":   "2026-03-10T11:00:00+06:00" }
    """

    # 1. Расписание мастера
    from sqlalchemy import select

    schedules_result = await session.execute(
        select(WorkSchedule).where(WorkSchedule.master_id == master_id)
    )
    schedules_by_weekday: dict[int, WorkSchedule] = {
        s.weekday: s for s in schedules_result.scalars().all()
    }

    if not schedules_by_weekday:
        return []

    # 2. Занятые промежутки
    busy = booked_ranges or []

    def _overlaps(s_start: datetime, s_end: datetime) -> bool:
        for b_start, b_end in busy:
            if s_start < b_end and s_end > b_start:
                return True
        return False

    # 3. Генерируем слоты по расписанию и фильтруем занятые
    now = datetime.now(TZ)
    available = []
    current_date = from_date
    while current_date <= to_date:
        schedule = schedules_by_weekday.get(current_date.weekday())
        if schedule:
            for starts_at, ends_at in _generate_day_slots(master_id, current_date, schedule):
                if not _overlaps(starts_at, ends_at) and starts_at > now:
                    available.append({
                        "starts_at": starts_at.isoformat(),
                        "ends_at": ends_at.isoformat(),
                    })
        current_date += timedelta(days=1)

    return available
