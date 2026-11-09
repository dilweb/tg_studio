"""Таймаут офферов: мастер не ответил — оффер сгорает, очередь идёт дальше.

Бьёт каждые 5 минут (celery beat). Сгоревший оффер → expired, следующий
в очереди → pending + уведомление; очередь пуста → эскалация владельцу.

acks_late + max_retries: сбой sweep'а (БД недоступна, Telegram упал) не
проглатывается молча — задача повторяется; acks_late страхует от потери
задачи при падении воркера.
"""

import logging

from tg_studio.modules.tattoo.offers import run_expire_stale_offers
from tg_studio.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="expire_session_offers", bind=True, acks_late=True, max_retries=3)
def expire_session_offers_task(self) -> int:
    try:
        return run_expire_stale_offers()
    except Exception as exc:
        logger.exception("expire_session_offers failed — retry")
        raise self.retry(exc=exc, countdown=60) from exc
