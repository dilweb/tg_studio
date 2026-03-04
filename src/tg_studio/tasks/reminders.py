import asyncio
import logging
from datetime import datetime, timedelta, timezone

from tg_studio.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@celery_app.task(name="schedule_reminders")
def schedule_reminders():
    """Планировщик: запускать каждые 15 минут через beat."""
    _run(_schedule_upcoming_reminders())


@celery_app.task(name="send_reminder", bind=True, max_retries=3)
def send_reminder(self, booking_id: int, hours_before: int):
    try:
        _run(_send_reminder(booking_id, hours_before))
    except Exception as exc:
        logger.exception("Failed to send reminder booking_id=%s", booking_id)
        raise self.retry(exc=exc, countdown=60)


async def _schedule_upcoming_reminders():
    from sqlalchemy import select

    from tg_studio.db.models import Booking, BookingStatus, TimeSlot
    from tg_studio.db.session import async_session_factory

    now = datetime.now(timezone.utc)
    window_24h = now + timedelta(hours=24)
    window_1h = now + timedelta(hours=1)

    async with async_session_factory() as session:
        result = await session.execute(
            select(Booking, TimeSlot)
            .join(TimeSlot, TimeSlot.id == Booking.slot_id)
            .where(
                Booking.status == BookingStatus.confirmed,
                TimeSlot.starts_at > now,
                TimeSlot.starts_at <= window_24h,
            )
        )
        rows = result.all()

    for booking, slot in rows:
        delta = slot.starts_at.replace(tzinfo=timezone.utc) - now
        if timedelta(hours=23) <= delta <= timedelta(hours=25):
            send_reminder.apply_async(args=[booking.id, 24], countdown=0)
        if timedelta(minutes=55) <= delta <= timedelta(hours=1, minutes=5):
            send_reminder.apply_async(args=[booking.id, 1], countdown=0)


async def _send_reminder(booking_id: int, hours_before: int):
    from aiogram import Bot
    from sqlalchemy import select

    from tg_studio.config import settings
    from tg_studio.db.models import Booking, Client, Master, Service, TimeSlot
    from tg_studio.db.session import async_session_factory

    async with async_session_factory() as session:
        result = await session.execute(select(Booking).where(Booking.id == booking_id))
        booking = result.scalar_one_or_none()
        if booking is None:
            return

        client = await session.get(Client, booking.client_id)
        service = await session.get(Service, booking.service_id)
        slot = await session.get(TimeSlot, booking.slot_id)
        master = await session.get(Master, booking.master_id)

    if not client or not slot:
        return

    starts_at = slot.starts_at.strftime("%d.%m.%Y %H:%M")
    service_name = service.name if service else "Услуга"
    master_name = master.full_name if master else "Мастер"

    time_label = f"{hours_before} час" if hours_before == 1 else f"{hours_before} часов"

    bot = Bot(token=settings.bot_token)
    try:
        await bot.send_message(
            chat_id=client.telegram_id,
            text=(
                f"Напоминание: через {time_label} у вас запись!\n\n"
                f"Услуга: {service_name}\n"
                f"Мастер: {master_name}\n"
                f"Дата и время: {starts_at}"
            ),
        )
    finally:
        await bot.session.close()
