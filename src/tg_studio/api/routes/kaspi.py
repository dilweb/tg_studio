import hashlib
import hmac
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from tg_studio.api.deps import SessionDep
from tg_studio.config import settings
from tg_studio.db.models import Booking, BookingStatus, Payment, PaymentStatus

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/kaspi", tags=["kaspi"])


class KaspiCallbackPayload(BaseModel):
    order_id: str
    transaction_id: str
    status: str  # "APPROVED" | "DECLINED" | "WAIT"


def _verify_signature(request_body: bytes, signature: str) -> bool:
    expected = hmac.new(
        settings.kaspi_callback_secret.encode(),
        request_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


@router.post("/callback")
async def kaspi_callback(request: Request, session: SessionDep):
    body_bytes = await request.body()
    signature = request.headers.get("X-Signature", "")

    if settings.kaspi_callback_secret and not _verify_signature(body_bytes, signature):
        raise HTTPException(status_code=403, detail="Invalid signature")

    data = KaspiCallbackPayload.model_validate_json(body_bytes)
    logger.info("Kaspi callback: order_id=%s status=%s", data.order_id, data.status)

    payment_result = await session.execute(
        select(Payment).where(Payment.gateway_order_id == data.order_id)
    )
    payment = payment_result.scalar_one_or_none()
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment not found")

    booking_result = await session.execute(
        select(Booking).where(Booking.id == payment.booking_id)
    )
    booking = booking_result.scalar_one_or_none()

    if data.status == "APPROVED":
        payment.status = PaymentStatus.paid
        payment.paid_at = datetime.now(timezone.utc)
        if booking:
            booking.status = BookingStatus.confirmed

        await session.commit()

        if booking:
            from tg_studio.tasks.notifications import notify_booking_confirmed
            notify_booking_confirmed.delay(booking.id)

    elif data.status == "DECLINED":
        payment.status = PaymentStatus.failed
        if booking:
            booking.status = BookingStatus.cancelled
            from tg_studio.db.models import TimeSlot
            slot_result = await session.execute(
                select(TimeSlot).where(TimeSlot.id == booking.slot_id)
            )
            slot = slot_result.scalar_one_or_none()
            if slot:
                slot.is_available = True
        await session.commit()

    return {"ok": True}
