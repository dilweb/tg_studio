"""
Задача: автоматически отменить бронь если оплата не поступила за 30 минут.

Флоу:
1. При создании брони → expire_pending_payment.apply_async(countdown=1800)
2. Через 30 минут Celery запускает задачу
3. Если payment.status всё ещё pending → отменяем бронь, освобождаем слот, уведомляем клиента
4. Если payment.status уже paid/failed/etc → ничего не делаем (колбэк уже пришёл)
"""

import asyncio
import logging

from tg_studio.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

PAYMENT_TIMEOUT_SECONDS = 1800  # 30 минут


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@celery_app.task(name="expire_pending_payment", bind=True, max_retries=3)
def expire_pending_payment(self, booking_id: int):
    """Отменить бронь если оплата не поступила вовремя."""
    try:
        _run(_expire(booking_id))
    except Exception as exc:
        logger.exception("expire_pending_payment failed for booking_id=%s", booking_id)
        raise self.retry(exc=exc, countdown=60)


async def _expire(booking_id: int):
    from sqlalchemy import select

    from tg_studio.db.models import Booking, BookingStatus, Client, Payment, PaymentStatus, TimeSlot
    from tg_studio.db.session import async_session_factory

    async with async_session_factory() as session:
        payment_result = await session.execute(
            select(Payment).where(Payment.booking_id == booking_id)
        )
        payment = payment_result.scalar_one_or_none()

        if payment is None:
            logger.warning("expire_pending_payment: payment not found for booking_id=%s", booking_id)
            return

        # Колбэк от Kaspi уже пришёл — всё решено без нас
        if payment.status != PaymentStatus.pending:
            logger.info(
                "expire_pending_payment: booking_id=%s already has status=%s, skipping",
                booking_id, payment.status,
            )
            return

        booking_result = await session.execute(
            select(Booking).where(Booking.id == booking_id)
        )
        booking = booking_result.scalar_one_or_none()

        if booking is None or booking.status != BookingStatus.pending:
            return

        logger.info("expire_pending_payment: cancelling booking_id=%s due to payment timeout", booking_id)

        # Отменяем платёж и бронь
        payment.status = PaymentStatus.failed
        booking.status = BookingStatus.cancelled

        # Освобождаем слот
        slot_result = await session.execute(
            select(TimeSlot).where(TimeSlot.id == booking.slot_id)
        )
        slot = slot_result.scalar_one_or_none()
        if slot:
            slot.is_available = True

        await session.commit()

        # Уведомить клиента что бронь отменена из-за неоплаты
        client = await session.get(Client, booking.client_id)
        if client:
            await _notify_expired(client.telegram_id, booking_id)


async def _notify_expired(telegram_id: int, booking_id: int):
    from aiogram import Bot
    from tg_studio.config import settings

    bot = Bot(token=settings.bot_token)
    try:
        await bot.send_message(
            chat_id=telegram_id,
            text=(
                f"Ваша бронь #{booking_id} была автоматически отменена — "
                f"оплата не поступила в течение 30 минут.\n\n"
                f"Вы можете записаться снова."
            ),
        )
    except Exception:
        logger.warning("Failed to notify client telegram_id=%s about expired booking", telegram_id)
    finally:
        await bot.session.close()
