from datetime import date

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import outerjoin

from tg_studio.api.deps import SessionDep
from tg_studio.api.routes.admin.deps import OwnerBusinessDep
from tg_studio.db.models import Booking, BookingStatus, Client, Master, Payment, Service, TimeSlot

router = APIRouter(prefix="/bookings", tags=["admin • bookings"])


class BookingAdminResponse(BaseModel):
    id: int
    status: str
    service_type: str
    client_name: str
    client_phone: str | None
    master_name: str
    service_name: str
    # Только для appointment
    starts_at: str | None
    ends_at: str | None
    duration_hours: int | None
    # Только для project
    project_deadline: str | None
    total_amount: float
    prepayment_paid: bool
    cancel_deadline_at: str | None
    created_at: str


@router.get("", response_model=list[BookingAdminResponse])
async def list_bookings(
    session: SessionDep,
    business: OwnerBusinessDep,
    status: str | None = Query(default=None, description="pending | confirmed | in_progress | cancelled | completed"),
    master_id: int | None = Query(default=None),
    from_date: date | None = Query(default=None, description="YYYY-MM-DD"),
    to_date: date | None = Query(default=None, description="YYYY-MM-DD"),
):
    """Список записей бизнеса с фильтрами."""
    q = (
        select(Booking, Client, Master, Service, TimeSlot)
        .join(Client, Client.id == Booking.client_id)
        .join(Master, Master.id == Booking.master_id)
        .join(Service, Service.id == Booking.service_id)
        .outerjoin(TimeSlot, TimeSlot.id == Booking.slot_id)
        .where(Master.business_id == business.id)
        .order_by(Booking.created_at.desc())
    )

    if status:
        try:
            q = q.where(Booking.status == BookingStatus(status))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Неверный статус: {status}")

    if master_id:
        q = q.where(Booking.master_id == master_id)

    if from_date:
        from datetime import datetime, time, timezone
        q = q.where(Booking.created_at >= datetime.combine(from_date, time.min, tzinfo=timezone.utc))

    if to_date:
        from datetime import datetime, time, timedelta, timezone
        q = q.where(Booking.created_at < datetime.combine(to_date + timedelta(days=1), time.min, tzinfo=timezone.utc))

    result = await session.execute(q)
    rows = result.all()

    booking_ids = [b.id for b, *_ in rows]
    payments: dict[int, Payment] = {}
    if booking_ids:
        pay_result = await session.execute(
            select(Payment).where(Payment.booking_id.in_(booking_ids))
        )
        payments = {p.booking_id: p for p in pay_result.scalars().all()}

    return [
        BookingAdminResponse(
            id=booking.id,
            status=booking.status.value,
            service_type=service.service_type.value,
            client_name=client.full_name,
            client_phone=client.phone,
            master_name=master.full_name,
            service_name=service.name,
            starts_at=slot.starts_at.isoformat() if slot else None,
            ends_at=slot.ends_at.isoformat() if slot else None,
            duration_hours=booking.duration_hours,
            project_deadline=booking.project_deadline.isoformat() if booking.project_deadline else None,
            total_amount=float(booking.total_amount),
            prepayment_paid=payments[booking.id].status.value == "paid" if booking.id in payments else False,
            cancel_deadline_at=booking.cancel_deadline_at.isoformat() if booking.cancel_deadline_at else None,
            created_at=booking.created_at.isoformat(),
        )
        for booking, client, master, service, slot in rows
    ]


@router.patch("/{booking_id}/complete", status_code=200)
async def mark_completed(
    booking_id: int,
    session: SessionDep,
    business: OwnerBusinessDep,
):
    """Отметить запись как выполненную."""
    result = await session.execute(
        select(Booking)
        .join(Master, Master.id == Booking.master_id)
        .where(Booking.id == booking_id, Master.business_id == business.id)
    )
    booking = result.scalar_one_or_none()
    if booking is None:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    if booking.status not in (BookingStatus.confirmed, BookingStatus.in_progress):
        raise HTTPException(
            status_code=409,
            detail=f"Нельзя завершить запись со статусом '{booking.status.value}'",
        )
    booking.status = BookingStatus.completed
    await session.commit()
    return {"id": booking_id, "status": "completed"}


@router.patch("/{booking_id}/start", status_code=200)
async def mark_in_progress(
    booking_id: int,
    session: SessionDep,
    business: OwnerBusinessDep,
):
    """Взять проект в работу (только для project-типа)."""
    result = await session.execute(
        select(Booking, Service)
        .join(Service, Service.id == Booking.service_id)
        .join(Master, Master.id == Booking.master_id)
        .where(Booking.id == booking_id, Master.business_id == business.id)
    )
    row = result.one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Запись не найдена")

    booking, service = row
    from tg_studio.db.models import ServiceType
    if service.service_type != ServiceType.project:
        raise HTTPException(status_code=409, detail="Статус 'in_progress' доступен только для проектных услуг")

    if booking.status != BookingStatus.confirmed:
        raise HTTPException(
            status_code=409,
            detail=f"Нельзя взять в работу запись со статусом '{booking.status.value}'",
        )
    booking.status = BookingStatus.in_progress
    await session.commit()
    return {"id": booking_id, "status": "in_progress"}
