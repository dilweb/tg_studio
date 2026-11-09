"""Дедлайн предоплаты: счёт не оплачен за 24ч — бронь отменяется.

Бьёт каждые 15 минут (celery beat). Только apipay-счета: manual мастер
подтверждает сам. Счёт → expired, работа и сеансы → cancelled (слот
свободен, события удалены из Google Calendar), клиент и владелец уведомлены.

acks_late + max_retries: сбой sweep'а (БД недоступна, Telegram упал) не
проглатывается молча — задача повторяется; acks_late страхует от потери
задачи при падении воркера.
"""

import logging

from tg_studio.modules.payments.service import run_expire_unpaid_prepay
from tg_studio.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="expire_prepay_invoices", bind=True, acks_late=True, max_retries=3)
def expire_prepay_invoices_task(self) -> int:
    try:
        return run_expire_unpaid_prepay()
    except Exception as exc:
        logger.exception("expire_prepay_invoices failed — retry")
        raise self.retry(exc=exc, countdown=60) from exc
