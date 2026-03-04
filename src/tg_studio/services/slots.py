"""
Генерация доступных слотов на лету.

Логика:
1. Берём расписание мастера (WorkSchedule) на запрошенный период
2. Разбиваем рабочее время на слоты по slot_duration_minutes
3. Вычитаем уже занятые брони (Booking со статусом pending/confirmed)
4. Возвращаем свободные интервалы — никакой предварительной генерации не нужно
"""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_studio.db.models import Booking, BookingStatus, TimeSlot, WorkSchedule

TZ = ZoneInfo("Asia/Almaty")

BUSY_STATUSES = {BookingStatus.pending, BookingStatus.confirmed}


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
) -> list[dict]:
    """
    Вернуть список свободных слотов для мастера на период [from_date, to_date].

    Результат — список словарей вида:
      { "starts_at": "2026-03-10T10:00:00+06:00",
        "ends_at":   "2026-03-10T11:00:00+06:00" }
    """
    # 1. Расписание мастера
    schedules_result = await session.execute(
        select(WorkSchedule).where(WorkSchedule.master_id == master_id)
    )
    schedules_by_weekday: dict[int, WorkSchedule] = {
        s.weekday: s for s in schedules_result.scalars().all()
    }

    if not schedules_by_weekday:
        return []

    # 2. Уже занятые промежутки из таблицы time_slots (забронированные)
    period_start = datetime.combine(from_date, time.min, tzinfo=TZ)
    period_end = datetime.combine(to_date, time.max, tzinfo=TZ)

    booked_result = await session.execute(
        select(TimeSlot)
        .join(Booking, Booking.slot_id == TimeSlot.id)
        .where(
            TimeSlot.master_id == master_id,
            TimeSlot.starts_at >= period_start,
            TimeSlot.starts_at <= period_end,
            Booking.status.in_(BUSY_STATUSES),
        )
    )
    booked_intervals: set[datetime] = {
        s.starts_at.replace(tzinfo=TZ) for s in booked_result.scalars().all()
    }

    # 3. Генерируем слоты по расписанию и фильтруем занятые
    available = []
    current_date = from_date
    while current_date <= to_date:
        schedule = schedules_by_weekday.get(current_date.weekday())
        if schedule:
            for starts_at, ends_at in _generate_day_slots(master_id, current_date, schedule):
                if starts_at not in booked_intervals and starts_at > datetime.now(TZ):
                    available.append({
                        "starts_at": starts_at.isoformat(),
                        "ends_at": ends_at.isoformat(),
                    })
        current_date += timedelta(days=1)

    return available
