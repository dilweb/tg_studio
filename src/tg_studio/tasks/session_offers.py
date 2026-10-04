"""Таймаут офферов: мастер не ответил — оффер сгорает, очередь идёт дальше.

Бьёт каждые 5 минут (celery beat). Сгоревший оффер → expired, следующий
в очереди → pending + уведомление; очередь пуста → эскалация владельцу.
"""

import logging

from tg_studio.modules.tattoo.offers import run_expire_stale_offers
from tg_studio.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="expire_session_offers")
def expire_session_offers_task() -> int:
    try:
        return run_expire_stale_offers()
    except Exception:
        logger.exception("expire_session_offers failed")
        return 0
