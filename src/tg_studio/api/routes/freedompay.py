"""
Freedom Pay webhook: pg_result_url.

Документация: https://docs.freedompay.kz/
"""

import hashlib
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import select

from tg_studio.api.deps import SessionDep
from tg_studio.db.models import Booking, BookingStatus, Master, Payment, PaymentStatus, TimeSlot
from tg_studio.services.payment import _build_freedom_pay_sig

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/freedompay", tags=["freedompay"])


def _verify_freedom_pay_sig(params: dict, secret_key: str) -> bool:
    """Проверить подпись pg_sig от Freedom Pay. Script = callback (путь нашего URL)."""
    received_sig = params.get("pg_sig")
    if not received_sig or not secret_key:
        return False
    params_copy = {k: v for k, v in params.items() if k != "pg_sig"}
    expected = _build_freedom_pay_sig(params_copy, "callback", secret_key)
    return received_sig.lower() == expected.lower()


@router.post("/callback")
async def freedompay_callback(request: Request, session: SessionDep):
    """
    Webhook pg_result_url от Freedom Pay.

    Freedom Pay шлёт POST с form-data: pg_order_id, pg_payment_id, pg_result, pg_salt, pg_sig, ...
    pg_result: ok | rejected | error
    """
    form = await request.form()
    params = dict(form)

    pg_order_id = params.get("pg_order_id")
    pg_result = params.get("pg_result", "").lower()

    if not pg_order_id:
        logger.warning("Freedom Pay callback без pg_order_id")
        raise HTTPException(status_code=400, detail="Missing pg_order_id")

    logger.info("Freedom Pay callback: pg_order_id=%s pg_result=%s", pg_order_id, pg_result)

    payment_result = await session.execute(
        select(Payment).where(Payment.gateway_order_id == pg_order_id)
    )
    payment = payment_result.scalar_one_or_none()
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment not found")

    booking_result = await session.execute(
        select(Booking).where(Booking.id == payment.booking_id)
    )
    booking = booking_result.scalar_one_or_none()
    if booking is None:
        raise HTTPException(status_code=404, detail="Booking not found")

    master_result = await session.execute(
        select(Master).where(Master.id == booking.master_id)
    )
    master = master_result.scalar_one_or_none()
    if master is None:
        raise HTTPException(status_code=404, detail="Master not found")

    from tg_studio.db.models import Business

    business_result = await session.execute(
        select(Business).where(Business.id == master.business_id)
    )
    business = business_result.scalar_one_or_none()
    if business is None or not business.freedom_pay_secret_key:
        logger.error("Business или secret_key не найден для payment pg_order_id=%s", pg_order_id)
        raise HTTPException(status_code=500, detail="Cannot verify signature")

    if not _verify_freedom_pay_sig(params, business.freedom_pay_secret_key):
        logger.warning("Freedom Pay callback: неверная подпись pg_order_id=%s", pg_order_id)
        raise HTTPException(status_code=403, detail="Invalid signature")

    if pg_result == "ok":
        payment.status = PaymentStatus.paid
        payment.paid_at = datetime.now(timezone.utc)
        if booking:
            booking.status = BookingStatus.confirmed

        await session.commit()

        if booking:
            from tg_studio.tasks.notifications import notify_booking_confirmed

            notify_booking_confirmed.delay(booking.id)

    elif pg_result in ("rejected", "error"):
        payment.status = PaymentStatus.failed
        if booking:
            booking.status = BookingStatus.cancelled
            slot_result = await session.execute(
                select(TimeSlot).where(TimeSlot.id == booking.slot_id)
            )
            slot = slot_result.scalar_one_or_none()
            if slot:
                slot.is_available = True
        await session.commit()

    return PlainTextResponse("ok")
