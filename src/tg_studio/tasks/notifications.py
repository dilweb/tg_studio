import asyncio
import logging

from tg_studio.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@celery_app.task(name="notify_booking_confirmed", bind=True, max_retries=3)
def notify_booking_confirmed(self, booking_id: int):
    """Уведомить клиента и мастера после подтверждения оплаты."""
    try:
        _run(_send_booking_confirmed(booking_id))
    except Exception as exc:
        logger.exception("Failed to send booking confirmation for booking_id=%s", booking_id)
        raise self.retry(exc=exc, countdown=30)


async def _send_booking_confirmed(booking_id: int):
    from aiogram import Bot
    from sqlalchemy import select

    from tg_studio.config import settings
    from tg_studio.db.models import Booking, Client, Master, Service, TimeSlot
    from tg_studio.db.session import async_session_factory

    async with async_session_factory() as session:
        result = await session.execute(
            select(Booking).where(Booking.id == booking_id)
        )
        booking = result.scalar_one_or_none()
        if booking is None:
            logger.error("Booking %s not found", booking_id)
            return

        client = await session.get(Client, booking.client_id)
        master = await session.get(Master, booking.master_id)
        service = await session.get(Service, booking.service_id)
        slot = await session.get(TimeSlot, booking.slot_id)

    if not client or not slot:
        return

    starts_at = slot.starts_at.strftime("%d.%m.%Y %H:%M")
    service_name = service.name if service else "Услуга"
    master_name = master.full_name if master else "Мастер"

    bot = Bot(token=settings.bot_token)
    try:
        await bot.send_message(
            chat_id=client.telegram_id,
            text=(
                f"Ваша запись подтверждена!\n\n"
                f"Услуга: {service_name}\n"
                f"Мастер: {master_name}\n"
                f"Дата и время: {starts_at}\n\n"
                f"Ждём вас!"
            ),
        )

        if master and master.telegram_id:
            await bot.send_message(
                chat_id=master.telegram_id,
                text=(
                    f"Новая запись!\n\n"
                    f"Клиент: {client.full_name}\n"
                    f"Услуга: {service_name}\n"
                    f"Дата и время: {starts_at}"
                ),
            )
    finally:
        await bot.session.close()
