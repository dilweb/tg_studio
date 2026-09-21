from datetime import datetime

from pydantic import BaseModel, Field


class BookingCreate(BaseModel):
    master_id: int | None = None
    service_name: str | None = Field(default=None, max_length=256)
    start_datetime: datetime
    # длительность события в минутах; None → дефолт мастера → 60
    duration_minutes: int | None = Field(default=None, ge=15, le=1440)
    client_name: str
    client_phone: str | None = None


class BookingResponse(BaseModel):
    event_id: str
    master_id: int | None
    service_name: str | None
    client_name: str | None
    client_phone: str | None
    starts_at: str
    ends_at: str
    summary: str | None = None
    master_name: str | None = None
