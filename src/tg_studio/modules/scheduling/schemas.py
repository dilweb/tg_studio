import re

from pydantic import BaseModel, field_validator

_TIME_RE = re.compile(r"^\d{2}:\d{2}$")
WEEKDAY_NAMES = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


class ScheduleEntry(BaseModel):
    weekday: int = 0
    start_time: str = "10:00"
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
