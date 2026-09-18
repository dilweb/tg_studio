from datetime import datetime

from pydantic import BaseModel


class BookingCreate(BaseModel):
    master_id: int | None = None
    service_id: int
    start_datetime: datetime
    client_name: str
    client_phone: str | None = None


class BookingResponse(BaseModel):
    event_id: str
    master_id: int | None
    service_id: int | None
    client_name: str | None
    client_phone: str | None
    starts_at: str
    ends_at: str
    summary: str | None = None
