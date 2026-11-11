"""Исходящие уведомления в Telegram (бот-процесса нет — только отправка по Bot API).

Вся Telegram-I/O здесь: тесты подменяют send_text_to_telegram.
"""

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_studio.config import settings
from tg_studio.db.models import Business, User, UserRole

logger = logging.getLogger(__name__)

# Ретраим RetryAfter (rate limit), 5xx и сеть; 4xx (бот заблокирован, чат не найден) — сразу наружу
TG_SEND_MAX_ATTEMPTS = 3
TG_SEND_BACKOFF_SECONDS = 1.0


class TelegramSendError(Exception):
    """Обёртка над ошибками Telegram API при отправке."""


async def _send_with_retry(send_once):
    for attempt in range(1, TG_SEND_MAX_ATTEMPTS + 1):
        try:
            return await send_once()
        except TelegramRetryAfter as exc:
            if attempt == TG_SEND_MAX_ATTEMPTS:
                raise
            delay = max(TG_SEND_BACKOFF_SECONDS * attempt, exc.retry_after)
        except (TelegramServerError, TelegramNetworkError):
            if attempt == TG_SEND_MAX_ATTEMPTS:
                raise
            delay = TG_SEND_BACKOFF_SECONDS * attempt
        logger.warning(
            "Telegram: отправка не удалась (попытка %d/%d) — повтор через %.1fс",
            attempt,
            TG_SEND_MAX_ATTEMPTS,
            delay,
        )
        await asyncio.sleep(delay)


async def send_text_to_telegram(telegram_id: int, text: str) -> int:
    """Отправить текст. Возвращает telegram message_id."""
    bot = Bot(token=settings.bot_token)  # без parse_mode: "<" в тексте не ломает отправку
    try:
        msg = await _send_with_retry(lambda: bot.send_message(telegram_id, text))
        return msg.message_id
    except TelegramAPIError as exc:
        raise TelegramSendError(str(exc)) from exc
    finally:
        await bot.session.close()  # иначе aiohttp-коннекторы текут


async def resolve_owner_telegram_id(session: AsyncSession) -> int | None:
    """Кому слать уведомления владельцу: businesses.owner_telegram_id → User.telegram_id."""
    owner = (
        await session.execute(select(User).where(User.role == UserRole.owner))
    ).scalars().first()
    if owner is None:
        return None
    business = (
        await session.execute(select(Business).where(Business.owner_id == owner.id))
    ).scalar_one_or_none()
    if business is not None and business.owner_telegram_id:
        return business.owner_telegram_id
    return owner.telegram_id
