"""Периодический sync pending-счетов ApiPay — страховка вебхуков.

Вебхук может не дойти (сеть) или прийти раньше, чем сервис сохранил
external_invoice_id («webhook for unknown invoice» — нагрузочный тест
2026-11-14, находки kaspi_webhook_before_create_response): деньги в банке
есть, бронь остаётся ожидает_оплаты, пока кто-то не нажмёт sync вручную.
Задача опрашивает ApiPay по всем pending-счетам apipay и подтягивает статус —
та же логика, что POST /payments/{id}/sync, но по всем разом.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from tg_studio.db.models import Payment, PaymentProvider, PaymentStatus
from tg_studio.modules.payments.providers import (
    PaymentProviderError,
    apply_provider_status,
    get_provider,
)

logger = logging.getLogger(__name__)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


async def sync_pending_apipay_payments(session) -> dict[str, int]:
    """Опросить ApiPay по всем pending-счетам apipay. Коммитит сам.

    «paid» липкий и дедуп по проводке (apply_payment_paid): гонка с вебхуком
    безопасна — кто первый, тот и применяет, второй заход no-op.
    """
    payments = (
        await session.execute(
            select(Payment).where(
                Payment.provider == PaymentProvider.apipay.value,
                Payment.status == PaymentStatus.pending.value,
                Payment.external_invoice_id.is_not(None),
            )
        )
    ).scalars().all()

    stats: dict[str, int] = {"checked": 0, "paid": 0, "errors": 0}
    if not payments:
        return stats

    from tg_studio.modules.coworking import money

    provider = get_provider(PaymentProvider.apipay.value)
    for payment in payments:
        stats["checked"] += 1
        # сеть — до блокировок: строки брони/счёта не ждут ApiPay
        try:
            invoice: dict[str, Any] = await provider.fetch_invoice(payment)
        except PaymentProviderError as exc:
            logger.warning("apipay sync: fetch invoice for payment %s failed: %s", payment.id, exc)
            stats["errors"] += 1
            continue
        payment = await money.lock_payment(session, payment.id)
        if payment.status != PaymentStatus.pending.value:
            continue  # пока ходили по сети, статус уже изменился (вебхук) — не трогаем
        paid_at = _parse_iso(invoice.get("paid_at"))
        if paid_at is None and invoice.get("status") == "paid":
            paid_at = datetime.now(UTC)
        apply_provider_status(payment, invoice.get("status") or "", paid_at)
        link = invoice.get("kaspi_qr_link")
        if link and payment.payment_url != link:
            payment.payment_url = link
        if invoice.get("status") == "error" and invoice.get("error_message"):
            payment.note = f"ApiPay: {invoice['error_message']}"
        if payment.status == PaymentStatus.paid.value:
            await money.apply_payment_paid(session, payment)
            stats["paid"] += 1
            logger.info("apipay sync: payment %s became paid (booking %s)", payment.id, payment.booking_id)
        await session.commit()
        await money.run_provider_ops(session)
    return stats
