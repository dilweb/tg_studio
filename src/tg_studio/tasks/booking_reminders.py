"""Напоминания мастерам в Telegram (modules/coworking/reminders.py), beat каждые 5 минут."""

import logging

from tg_studio.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def run_send_booking_reminders() -> dict[str, int]:
    import asyncio

    from tg_studio.modules.coworking.reminders import send_due_reminders

    async def _run() -> dict[str, int]:
        from tg_studio.db.session import async_session_factory

        async with async_session_factory() as db_session:
            return await send_due_reminders(db_session)

    return asyncio.run(_run())


@celery_app.task(name="send_booking_reminders", bind=True, acks_late=True, max_retries=3)
def send_booking_reminders_task(self) -> dict[str, int]:
    try:
        return run_send_booking_reminders()
    except Exception as exc:
        logger.exception("send_booking_reminders failed — retry")
        raise self.retry(exc=exc, countdown=60) from exc
