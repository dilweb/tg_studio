"""
Freedom Pay интеграция (multi-merchant).

Документация: https://docs.freedompay.kz/

Флоу:
1. create_order(business_id, ...) → возвращает payment_url (pg_redirect_url)
2. Клиент оплачивает на странице Freedom Pay (Kaspi, карты, Apple/Google Pay)
3. Freedom Pay вызывает POST pg_result_url
4. Мы подтверждаем бронь и шлём уведомление
"""

import hashlib
import logging
import secrets
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_studio.config import settings
from tg_studio.db.models import Business

logger = logging.getLogger(__name__)

FREEDOM_PAY_API_URL = "https://api.freedompay.kz"
INIT_SCRIPT = "init_payment.php"


def _build_freedom_pay_sig(params: dict[str, Any], script: str, secret_key: str) -> str:
    """Подпись запроса Freedom Pay: MD5(script;param1;param2;...;secret_key), params по алфавиту."""
    sorted_keys = sorted(k for k in params.keys() if k != "pg_sig")
    parts = [script]
    for k in sorted_keys:
        v = params[k]
        if isinstance(v, (list, dict)):
            continue  # сложные структуры — см. документацию
        parts.append(str(v))
    parts.append(secret_key)
    sig_str = ";".join(parts)
    return hashlib.md5(sig_str.encode("utf-8")).hexdigest().lower()


class FreedomPayService:
    """Сервис Freedom Pay. Credentials берутся из Business (multi-merchant)."""

    def __init__(self):
        self.api_url = getattr(settings, "freedom_pay_api_url", FREEDOM_PAY_API_URL)

    async def create_order(
        self,
        session: AsyncSession,
        business_id: int,
        booking_id: int,
        amount: float,
        description: str,
        user_phone: str | None = None,
    ) -> dict:
        """
        Создать платёж в Freedom Pay и вернуть ссылку для оплаты.

        Использует freedom_pay_merchant_id и freedom_pay_secret_key из Business.
        """
        result = await session.execute(
            select(Business).where(Business.id == business_id)
        )
        business = result.scalar_one_or_none()
        if business is None:
            raise ValueError(f"Business {business_id} not found")

        merchant_id = business.freedom_pay_merchant_id
        secret_key = business.freedom_pay_secret_key

        if not merchant_id or not secret_key:
            fake_order_id = f"DEV-{booking_id}"
            logger.warning(
                "Freedom Pay не настроен для business_id=%s, используется stub. order_id=%s",
                business_id,
                fake_order_id,
            )
            return {
                "order_id": fake_order_id,
                "payment_url": f"https://pay.kaspi.kz/pay/stub?order={fake_order_id}",
            }

        callback_base = getattr(settings, "api_public_url", None) or settings.miniapp_url
        callback_base = callback_base.rstrip("/")

        pg_order_id = str(booking_id)
        pg_salt = secrets.token_urlsafe(16)

        params: dict[str, Any] = {
            "pg_merchant_id": merchant_id,
            "pg_order_id": pg_order_id,
            "pg_amount": amount,
            "pg_description": description[:1024],
            "pg_salt": pg_salt,
            "pg_result_url": f"{callback_base}/api/freedompay/callback",
            "pg_success_url": f"{callback_base}/payment/success",
            "pg_failure_url": f"{callback_base}/payment/failure",
        }

        if user_phone:
            phone = user_phone.replace(" ", "").replace("+", "").replace("-", "")
            if not phone.startswith("7"):
                phone = "7" + phone
            params["pg_user_phone"] = phone

        params["pg_sig"] = _build_freedom_pay_sig(params, INIT_SCRIPT, secret_key)

        url = f"{self.api_url}/{INIT_SCRIPT}"
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(url, data=params)
            response.raise_for_status()

        # Freedom Pay может вернуть XML или JSON
        try:
            data = response.json()
        except Exception:
            data = {}
            import re
            m = re.search(r"<pg_redirect_url>([^<]+)</pg_redirect_url>", response.text)
            if m:
                data["pg_redirect_url"] = m.group(1)

        if "pg_redirect_url" not in data:
            logger.error("Freedom Pay не вернул pg_redirect_url: %s", response.text[:500])
            raise ValueError("Freedom Pay не вернул ссылку оплаты")

        return {
            "order_id": pg_order_id,
            "payment_url": data["pg_redirect_url"],
        }


freedom_pay_service = FreedomPayService()

# Kaspi Pay оставлен только для callback старых платежей (см. api/routes/kaspi.py)
