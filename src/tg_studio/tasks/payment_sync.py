"""Страховка вебхуков ApiPay: sync pending-счетов (beat каждые 5 минут).

Логика — modules/payments/sync.py; здесь только celery-обёртка.
"""

import logging

from tg_studio.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def run_sync_pending_payments() -> dict[str, int]:
    import asyncio

    from tg_studio.modules.payments.sync import sync_pending_apipay_payments

    async def _run() -> dict[str, int]:
        from tg_studio.db.session import task_session

        async with task_session() as db_session:
            return await sync_pending_apipay_payments(db_session)

    return asyncio.run(_run())


@celery_app.task(name="sync_pending_payments", bind=True, acks_late=True, max_retries=3)
def sync_pending_payments_task(self) -> dict[str, int]:
    try:
        return run_sync_pending_payments()
    except Exception as exc:
        logger.exception("sync_pending_payments failed — retry")
        raise self.retry(exc=exc, countdown=60) from exc
