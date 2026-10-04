"""Дедлайн предоплаты: счёт не оплачен за 24ч — бронь отменяется.

Бьёт каждые 15 минут (celery beat). Только apipay-счета: manual мастер
подтверждает сам. Счёт → expired, работа и сеансы → cancelled (слот
свободен), клиент и владелец уведомлены.
"""

import logging

from tg_studio.modules.payments.service import run_expire_unpaid_prepay
from tg_studio.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="expire_prepay_invoices")
def expire_prepay_invoices_task() -> int:
    try:
        return run_expire_unpaid_prepay()
    except Exception:
        logger.exception("expire_prepay_invoices failed")
        return 0
