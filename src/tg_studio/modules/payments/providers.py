"""Платёжные провайдеры за одним узким интерфейсом.

Провайдер — только «как клиент платит»: создать внешний счёт и отменить его.
Машина состояний платежа (pending → paid/cancelled/expired), баланс работы
и журнал живут в payments/api.py и от провайдера не зависят.

- manual — наличные/перевод: внешнего счёта нет, оплату подтверждает
  мастер/владелец кнопкой «Оплатил». Работает всегда, fallback навсегда.
- apipay — счёт Kaspi по номеру телефона клиента (api.apipay.kz):
  клиенту приходит push в Kaspi, оплату подтверждает вебхук с HMAC-подписью
  (задвоение страховкой — опрос GET /invoices/{id}).

Ключ ApiPay живёт только в окружении (settings.apipay_api_key): не в логах,
не в БД, не во frontend bundle.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx

from tg_studio.db.models import Payment

logger = logging.getLogger(__name__)


@dataclass
class InvoiceContext:
    """Всё, что провайдеру нужно знать про счёт, кроме самого Payment."""

    client_phone: str | None  # уже нормализованный (8XXXXXXXXXX) или None
    client_name: str
    description: str  # назначение платежа (лимит провайдера — до 60 симв.)


@dataclass
class CreatedInvoice:
    """Внешний счёт, созданный провайдером."""

    external_id: str
    payment_url: str | None = None
    expires_at: datetime | None = None


class PaymentProviderError(Exception):
    """Провайдер не смог создать/отменить счёт (сеть, лимиты, KYC...)."""


class ManualProvider:
    name = "manual"

    async def create_invoice(self, payment: Payment, ctx: InvoiceContext) -> None:
        """Внешнего счёта нет — оплата «на кассе», подтверждение вручную."""
        return None

    async def cancel_invoice(self, payment: Payment) -> None:
        return None


class ApipayProvider:
    """ApiPay (Kaspi Pay): счёт по номеру телефона, вебхук об оплате.

    API: base https://api.apipay.kz/api/v1, заголовок X-API-Key.
    Телефон строго 8XXXXXXXXXX (11 цифр), сумма — целые тенге,
    описание ≤ 60 символов. Создание асинхронное: 201 со status=processing —
    норма, счёт дозревает до pending на стороне ApiPay.

    Статусы ApiPay могут идти назад (cancelled→paid, expired→paid) и
    «error» — временное состояние (error→pending), поэтому финальным его
    не считаем. Возвраты оплату не отменяют: частичный refund — счёт всё
    ещё paid (флаг is_fully_refunded нас не отменяет — возврат денег
    оформляется пересмотром contract_price в CRM).
    """

    name = "apipay"

    # Счёт ApiPay живёт сутки — это и есть дедлайн оплаты в CRM.
    INVOICE_TTL = timedelta(hours=24)

    def __init__(self, http: httpx.AsyncClient | None = None):
        self._http = http  # подмена в тестах (MockTransport); чужой клиент не закрываем

    def _client(self) -> httpx.AsyncClient:
        if self._http is not None:
            return self._http
        from tg_studio.config import settings

        return httpx.AsyncClient(
            base_url=settings.apipay_base_url,
            headers={"X-API-Key": settings.apipay_api_key},
            timeout=15,
        )

    async def _request(self, method: str, path: str, json_body: dict | None = None):
        client = self._client()
        own = self._http is None
        try:
            resp = await client.request(method, path, json=json_body)
        finally:
            if own:
                await client.aclose()
        if resp.status_code // 100 != 2:
            raise PaymentProviderError(self._error_text(resp))
        return resp

    @staticmethod
    def _error_text(resp: httpx.Response) -> str:
        try:
            data = resp.json()
        except Exception:
            return f"ApiPay: HTTP {resp.status_code}"
        if isinstance(data, dict):
            code = data.get("error_code") or data.get("detail") or data.get("message")
            if code:
                return f"ApiPay: {code} (HTTP {resp.status_code})"
        return f"ApiPay: HTTP {resp.status_code}"

    async def create_invoice(self, payment: Payment, ctx: InvoiceContext) -> CreatedInvoice:
        if not ctx.client_phone:
            raise PaymentProviderError(
                "У клиента нет телефона — счёт Kaspi выставить нельзя "
                "(укажите телефон в «Клиентах» или примите оплату вручную)"
            )
        amount = int(payment.amount)
        if amount != payment.amount:
            raise PaymentProviderError("ApiPay принимает только целые тенге")
        resp = await self._request(
            "POST",
            "/invoices",
            {
                "phone_number": ctx.client_phone,
                "amount": amount,
                "description": ctx.description[:60],
                # external_order_id — наша ссылка на платёж; же значение как
                # ключ идемпотентности: ретрай не создаст второй счёт.
                "external_order_id": f"payment-{payment.id}",
                "external_order_id_idempotency": f"payment-{payment.id}",
            },
        )
        data = resp.json()
        if data.get("error_message"):
            raise PaymentProviderError(f"ApiPay: {data['error_message']}")
        # kaspi_qr_link на момент 201/processing обычно null — подтянется
        # при опросе (sync). expires_at приблизительный: TTL счёта — сутки.
        return CreatedInvoice(
            external_id=str(data["id"]),
            payment_url=data.get("kaspi_qr_link"),
            expires_at=datetime.now(UTC) + self.INVOICE_TTL,
        )

    async def cancel_invoice(self, payment: Payment) -> None:
        if not payment.external_invoice_id:
            return
        await self._request("POST", f"/invoices/{payment.external_invoice_id}/cancel")

    async def fetch_invoice(self, payment: Payment) -> dict:
        """Опрос статуса счёта — страховка вебхука и сверка."""
        if not payment.external_invoice_id:
            raise PaymentProviderError("У платежа нет внешнего счёта")
        resp = await self._request("GET", f"/invoices/{payment.external_invoice_id}")
        return resp.json()


# ---------------------------------------------------------------------------
# Перевод статусов ApiPay → машина состояний CRM
# ---------------------------------------------------------------------------

# error и partially_refunded намеренно не попадают в статусы CRM:
# error — временное состояние (error→pending бывает), возвраты не отменяют
# факт оплаты (возврат денег оформляется пересмотром contract_price).
_PROVIDER_TO_LOCAL = {
    "processing": "pending",
    "pending": "pending",
    "paid": "paid",
    "cancelled": "cancelled",
    "expired": "expired",
    "error": "pending",
    "partially_refunded": "paid",
}


def provider_status_to_local(status: str) -> str | None:
    return _PROVIDER_TO_LOCAL.get(status)


def apply_provider_status(payment: Payment, provider_status: str, paid_at=None) -> bool:
    """Применить статус ApiPay к платежу с учётом «статусы идут назад».

    True — статус платежа изменился. Правила: «paid» липкий (оплату не
    отменяем задним числом), из cancelled/expired разрешён переход в paid,
    повторный тот же статус — no-op.
    """
    new_local = _PROVIDER_TO_LOCAL.get(provider_status)
    if new_local is None:
        logger.warning("Unknown apipay status %r for payment %s", provider_status, payment.id)
        return False
    if payment.status == new_local:
        if new_local == "paid" and payment.paid_at is None and paid_at is not None:
            payment.paid_at = paid_at
        return False
    if payment.status == "paid" and new_local != "paid":
        logger.warning(
            "Ignoring apipay downgrade %s→%s for payment %s",
            payment.status, new_local, payment.id,
        )
        return False
    payment.status = new_local
    if new_local == "paid" and paid_at is not None and payment.paid_at is None:
        payment.paid_at = paid_at
    return True


_MANUAL = ManualProvider()
_APIPAY = ApipayProvider()


def apipay_configured() -> bool:
    """Ключ ApiPay задан в окружении — счёт Kaspi доступен."""
    from tg_studio.config import settings

    return bool(settings.apipay_api_key)


def get_provider(name: str):
    return _APIPAY if name == "apipay" else _MANUAL