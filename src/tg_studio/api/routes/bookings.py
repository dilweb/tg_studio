from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from tg_studio.api.auth import TelegramUserDep
from tg_studio.api.deps import SessionDep
from tg_studio.db.models import (
    Booking,
    BookingStatus,
    Client,
    Master,
    Payment,
    PaymentStatus,
    Service,
    ServiceType,
    TimeSlot,
    WorkSchedule,
)
from tg_studio.services.payment import freedom_pay_service

router = APIRouter(prefix="/bookings", tags=["bookings"])

TZ = ZoneInfo("Asia/Almaty")


class CreateBookingRequest(BaseModel):
    master_id: int
    service_id: int
    # Только для appointment
    starts_at: datetime | None = None
    duration_hours: int | None = Field(default=None, ge=1, le=12)
    # Только для project
    project_deadline: datetime | None = None
    phone: str | None = None


class CreateBookingResponse(BaseModel):
    booking_id: int
    service_type: str
    payment_url: str
    payment_order_id: str
    total_amount: float
    prepayment_amount: float
    cancel_deadline_at: str | None
    project_deadline: str | None


def _parse_time(t: str):
    h, m = t.split(":")
    return int(h), int(m)


async def _check_within_schedule(
    session,
    master_id: int,
    starts_at: datetime,
    ends_at: datetime,
) -> None:
    """Проверить что запрошенное время попадает в рабочее расписание мастера."""
    weekday = starts_at.weekday()
    result = await session.execute(
        select(WorkSchedule).where(
            WorkSchedule.master_id == master_id,
            WorkSchedule.weekday == weekday,
        )
    )
    schedule = result.scalar_one_or_none()
    if schedule is None:
        raise HTTPException(
            status_code=409,
            detail="Мастер не работает в этот день недели",
        )

    wh, wm = _parse_time(schedule.start_time)
    eh, em = _parse_time(schedule.end_time)
    work_start = starts_at.replace(hour=wh, minute=wm, second=0, microsecond=0)
    work_end = starts_at.replace(hour=eh, minute=em, second=0, microsecond=0)

    if starts_at < work_start or ends_at > work_end:
        raise HTTPException(
            status_code=409,
            detail=f"Запрошенное время выходит за рабочие часы мастера ({schedule.start_time}–{schedule.end_time})",
        )


async def _check_no_overlap(
    session,
    master_id: int,
    starts_at: datetime,
    ends_at: datetime,
) -> None:
    """Проверить отсутствие пересекающихся броней у мастера."""
    result = await session.execute(
        select(TimeSlot)
        .join(Booking, Booking.slot_id == TimeSlot.id)
        .where(
            TimeSlot.master_id == master_id,
            Booking.status.in_([BookingStatus.pending, BookingStatus.confirmed]),
            TimeSlot.starts_at < ends_at,
            TimeSlot.ends_at > starts_at,
        )
    )
    if result.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=409,
            detail="Это время уже занято",
        )


@router.post("", response_model=CreateBookingResponse)
async def create_booking(
    body: CreateBookingRequest,
    session: SessionDep,
    tg_user: TelegramUserDep,
):
    telegram_id = tg_user["id"]
    full_name = f"{tg_user.get('first_name', '')} {tg_user.get('last_name', '')}".strip()

    # 1. Проверить мастера
    master_result = await session.execute(
        select(Master).where(Master.id == body.master_id, Master.is_active.is_(True))
    )
    master = master_result.scalar_one_or_none()
    if master is None:
        raise HTTPException(status_code=404, detail="Мастер не найден")

    # 2. Проверить услугу
    service_result = await session.execute(
        select(Service).where(Service.id == body.service_id, Service.is_active.is_(True))
    )
    service = service_result.scalar_one_or_none()
    if service is None:
        raise HTTPException(status_code=404, detail="Услуга не найдена")

    # 3. Получить или создать клиента
    client_result = await session.execute(
        select(Client).where(Client.telegram_id == telegram_id)
    )
    client = client_result.scalar_one_or_none()
    if client is None:
        client = Client(
            telegram_id=telegram_id,
            full_name=full_name,
            phone=body.phone,
        )
        session.add(client)
        await session.flush()

    if service.service_type == ServiceType.appointment:
        return await _create_appointment(session, body, client, service)
    else:
        return await _create_project(session, body, client, service)


async def _create_appointment(session, body: CreateBookingRequest, client: Client, service: Service) -> CreateBookingResponse:
    if body.starts_at is None or body.duration_hours is None:
        raise HTTPException(
            status_code=422,
            detail="Для почасовой записи обязательны поля: starts_at, duration_hours",
        )

    starts_at = body.starts_at.astimezone(TZ)
    ends_at = starts_at + timedelta(hours=body.duration_hours)

    if starts_at <= datetime.now(TZ):
        raise HTTPException(status_code=400, detail="Нельзя записаться в прошлое")

    await _check_within_schedule(session, body.master_id, starts_at, ends_at)
    await _check_no_overlap(session, body.master_id, starts_at, ends_at)

    total_amount = float(service.price) * body.duration_hours
    prepayment_amount = round(total_amount * service.prepayment_percent / 100, 2)

    ideal_deadline = starts_at - timedelta(hours=service.cancel_deadline_hours)
    cancel_deadline_at = max(ideal_deadline, datetime.now(TZ))

    slot = TimeSlot(
        master_id=body.master_id,
        starts_at=starts_at,
        ends_at=ends_at,
        is_available=False,
    )
    session.add(slot)
    await session.flush()

    booking = Booking(
        client_id=client.id,
        master_id=body.master_id,
        slot_id=slot.id,
        service_id=body.service_id,
        status=BookingStatus.pending,
        duration_hours=body.duration_hours,
        total_amount=total_amount,
        cancel_deadline_at=cancel_deadline_at,
    )
    session.add(booking)
    await session.flush()

    fp_order = await freedom_pay_service.create_order(
        session,
        business_id=master.business_id,
        booking_id=booking.id,
        amount=prepayment_amount,
        description=f"Предоплата {service.prepayment_percent}%: {service.name} x{body.duration_hours}ч",
        user_phone=client.phone,
    )

    payment = Payment(
        booking_id=booking.id,
        gateway_order_id=fp_order["order_id"],
        gateway="freedompay",
        amount=prepayment_amount,
        status=PaymentStatus.pending,
    )
    session.add(payment)
    await session.commit()

    from tg_studio.tasks.expire_payment import PAYMENT_TIMEOUT_SECONDS, expire_pending_payment
    expire_pending_payment.apply_async(args=[booking.id], countdown=PAYMENT_TIMEOUT_SECONDS)

    return CreateBookingResponse(
        booking_id=booking.id,
        service_type="appointment",
        payment_url=fp_order["payment_url"],
        payment_order_id=fp_order["order_id"],
        total_amount=total_amount,
        prepayment_amount=prepayment_amount,
        cancel_deadline_at=cancel_deadline_at.isoformat(),
        project_deadline=None,
    )


async def _create_project(session, body: CreateBookingRequest, client: Client, service: Service) -> CreateBookingResponse:
    master_result = await session.execute(
        select(Master).where(Master.id == body.master_id, Master.is_active.is_(True))
    )
    master = master_result.scalar_one_or_none()
    if master is None:
        raise HTTPException(status_code=404, detail="Мастер не найден")

    total_amount = float(service.price)
    prepayment_amount = round(total_amount * service.prepayment_percent / 100, 2)

    project_deadline = body.project_deadline.astimezone(TZ) if body.project_deadline else None

    booking = Booking(
        client_id=client.id,
        master_id=body.master_id,
        slot_id=None,
        service_id=body.service_id,
        status=BookingStatus.pending,
        duration_hours=None,
        total_amount=total_amount,
        cancel_deadline_at=None,
        project_deadline=project_deadline,
    )
    session.add(booking)
    await session.flush()

    fp_order = await freedom_pay_service.create_order(
        session,
        business_id=master.business_id,
        booking_id=booking.id,
        amount=prepayment_amount,
        description=f"Предоплата {service.prepayment_percent}%: {service.name}",
        user_phone=client.phone,
    )

    payment = Payment(
        booking_id=booking.id,
        gateway_order_id=fp_order["order_id"],
        gateway="freedompay",
        amount=prepayment_amount,
        status=PaymentStatus.pending,
    )
    session.add(payment)
    await session.commit()

    from tg_studio.tasks.expire_payment import PAYMENT_TIMEOUT_SECONDS, expire_pending_payment
    expire_pending_payment.apply_async(args=[booking.id], countdown=PAYMENT_TIMEOUT_SECONDS)

    return CreateBookingResponse(
        booking_id=booking.id,
        service_type="project",
        payment_url=fp_order["payment_url"],
        payment_order_id=fp_order["order_id"],
        total_amount=total_amount,
        prepayment_amount=prepayment_amount,
        cancel_deadline_at=None,
        project_deadline=project_deadline.isoformat() if project_deadline else None,
    )


@router.get("/{booking_id}")
async def get_booking(booking_id: int, session: SessionDep):
    result = await session.execute(
        select(Booking).where(Booking.id == booking_id)
    )
    booking = result.scalar_one_or_none()
    if booking is None:
        raise HTTPException(status_code=404, detail="Booking not found")

    slot = await session.get(TimeSlot, booking.slot_id) if booking.slot_id else None
    return {
        "id": booking.id,
        "status": booking.status,
        "starts_at": slot.starts_at.isoformat() if slot else None,
        "ends_at": slot.ends_at.isoformat() if slot else None,
        "duration_hours": booking.duration_hours,
        "total_amount": float(booking.total_amount),
        "cancel_deadline_at": booking.cancel_deadline_at.isoformat() if booking.cancel_deadline_at else None,
        "project_deadline": booking.project_deadline.isoformat() if booking.project_deadline else None,
        "service_id": booking.service_id,
        "master_id": booking.master_id,
    }


@router.delete("/{booking_id}")
async def cancel_booking(
    booking_id: int,
    session: SessionDep,
    tg_user: TelegramUserDep,
):
    """
    Отменить бронь.

    Appointment: нельзя после начала сеанса; до/после дедлайна → возврат/сгорание предоплаты.
    Project: можно отменить пока статус не in_progress/completed.
    """
    result = await session.execute(
        select(Booking).where(Booking.id == booking_id)
    )
    booking = result.scalar_one_or_none()
    if booking is None:
        raise HTTPException(status_code=404, detail="Booking not found")

    client = await session.get(Client, booking.client_id)
    if client is None or client.telegram_id != tg_user["id"]:
        raise HTTPException(status_code=403, detail="Нет доступа к этой записи")

    if booking.status == BookingStatus.cancelled:
        raise HTTPException(status_code=409, detail="Запись уже отменена")

    if booking.status == BookingStatus.completed:
        raise HTTPException(status_code=409, detail="Завершённую запись нельзя отменить")

    if booking.status == BookingStatus.in_progress:
        raise HTTPException(status_code=409, detail="Проект уже в работе — обратитесь к мастеру")

    now = datetime.now(TZ)

    # Для appointment — нельзя отменить после начала сеанса
    if booking.slot_id:
        slot = await session.get(TimeSlot, booking.slot_id)
        if slot and slot.starts_at.replace(tzinfo=TZ) <= now:
            raise HTTPException(
                status_code=409,
                detail="Сеанс уже начался или завершился — отмена невозможна",
            )
        if slot:
            slot.is_available = True

    booking.status = BookingStatus.cancelled

    payment_result = await session.execute(
        select(Payment).where(Payment.booking_id == booking_id)
    )
    payment = payment_result.scalar_one_or_none()

    refund = False
    if payment and payment.status == PaymentStatus.paid:
        if booking.cancel_deadline_at:
            deadline = booking.cancel_deadline_at.replace(tzinfo=TZ)
            if now <= deadline:
                payment.status = PaymentStatus.refunded
                refund = True
            else:
                payment.status = PaymentStatus.forfeited
        else:
            # Project без дедлайна — всегда возвращаем предоплату при отмене
            payment.status = PaymentStatus.refunded
            refund = True

    await session.commit()

    return {
        "booking_id": booking_id,
        "status": "cancelled",
        "refund": refund,
        "message": (
            "Запись отменена. Предоплата будет возвращена в течение 3–5 рабочих дней."
            if refund
            else "Запись отменена. Предоплата не возвращается — дедлайн бесплатной отмены истёк."
        ),
    }
