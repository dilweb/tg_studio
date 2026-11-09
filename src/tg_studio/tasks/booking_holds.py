"""Hold-TTL броней коворкинга: не оплачена к дедлайну → истекла_оплата.

Бьёт каждые 5 минут (celery beat). Бронь в статусе ожидает_оплаты с
hold_deadline < now переводится в истекла_оплата — место освобождается
(терминальный статус не участвует в проверке пересечений) + событие в
booking_events. Оплата подключается в 04 (тогда статус ожидает_оплаты
будет сниматься по вебхуку до истечения hold).

acks_late + max_retries: сбой sweep'а не проглатывается молча.
"""

import logging

from tg_studio.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def run_expire_booking_holds() -> int:
    """Синхронная обёртка для celery-воркера."""
    import asyncio

    from tg_studio.modules.coworking.service import expire_booking_holds

    async def _run() -> int:
        from tg_studio.db.session import async_session_factory

        async with async_session_factory() as db_session:
            expired = await expire_booking_holds(db_session)
            await db_session.commit()
            return expired

    return asyncio.run(_run())


@celery_app.task(name="expire_booking_holds", bind=True, acks_late=True, max_retries=3)
def expire_booking_holds_task(self) -> int:
    try:
        return run_expire_booking_holds()
    except Exception as exc:
        logger.exception("expire_booking_holds failed — retry")
        raise self.retry(exc=exc, countdown=60) from exc
