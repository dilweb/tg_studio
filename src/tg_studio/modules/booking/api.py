"""
Единый эндпоинт записей — для мастера, владельца и (в будущем) AI-агента.
Источник данных — только Google Calendar, локальной таблицы бронирований нет.
"""
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from tg_studio.api.admin_deps import MasterBusinessAndSelfDep
from tg_studio.api.deps import SessionDep
from tg_studio.modules.booking.schemas import BookingCreate, BookingResponse
from tg_studio.modules.booking.service import (
    BookingError,
    cancel_booking,
    create_booking,
    list_bookings,
)
from tg_studio.modules.scheduling.service import TZ

router = APIRouter(prefix="/admin/bookings", tags=["admin • bookings"])

MAX_DAYS_RANGE = 60


def _localize(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=TZ)


def _resolve_master_id(master_id: int | None, ctx: tuple) -> int:
    """Мастер видит только свои записи (own_master задан); владелец обязан указать master_id."""
    _business, own_master = ctx
    if own_master is not None:
        if master_id is not None and master_id != own_master.id:
            raise HTTPException(status_code=403, detail="Доступ только к своим записям")
        return own_master.id
    if master_id is None:
        raise HTTPException(status_code=400, detail="master_id обязателен для владельца")
    return master_id


@router.get("", response_model=list[BookingResponse])
async def get_bookings(
    session: SessionDep,
    ctx: MasterBusinessAndSelfDep,
    master_id: int | None = Query(None),
    from_date: datetime = Query(...),
    to_date: datetime = Query(...),
):
    business, _ = ctx
    master_id = _resolve_master_id(master_id, ctx)
    if to_date < from_date:
        raise HTTPException(status_code=400, detail="to_date must be >= from_date")
    if (to_date - from_date).days > MAX_DAYS_RANGE:
        raise HTTPException(status_code=400, detail=f"Date range too large, max {MAX_DAYS_RANGE} days")
    try:
        return await list_bookings(session, business, master_id, _localize(from_date), _localize(to_date))
    except BookingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("", response_model=BookingResponse, status_code=201)
async def post_booking(body: BookingCreate, session: SessionDep, ctx: MasterBusinessAndSelfDep):
    business, _ = ctx
    master_id = _resolve_master_id(body.master_id, ctx)
    try:
        return await create_booking(
            session,
            business,
            master_id=master_id,
            service_id=body.service_id,
            start_datetime=_localize(body.start_datetime),
            client_name=body.client_name,
            client_phone=body.client_phone,
        )
    except BookingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/{event_id}", status_code=204)
async def delete_booking(
    event_id: str,
    session: SessionDep,
    ctx: MasterBusinessAndSelfDep,
    master_id: int | None = Query(None),
):
    business, _ = ctx
    master_id = _resolve_master_id(master_id, ctx)
    try:
        await cancel_booking(session, business, master_id, event_id)
    except BookingError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
