"""Исходящие уведомления в Telegram (бот-процесса нет — только отправка по Bot API).

Вся Telegram-I/O здесь: тесты подменяют send_text_to_telegram.
"""

import asyncio
import logging

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
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


async def send_text_with_button(telegram_id: int, text: str, button_text: str, callback_data: str) -> int:
    """Текст + одна inline-кнопка (нажатие придёт callback_query на вебхук
    /api/telegram/webhook). Возвращает telegram message_id."""
    markup = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=button_text, callback_data=callback_data)]]
    )
    bot = Bot(token=settings.bot_token)
    try:
        msg = await _send_with_retry(lambda: bot.send_message(telegram_id, text, reply_markup=markup))
        return msg.message_id
    except TelegramAPIError as exc:
        raise TelegramSendError(str(exc)) from exc
    finally:
        await bot.session.close()


async def answer_callback(callback_query_id: str, text: str, *, replace_message: tuple[int, int, str] | None = None) -> None:
    """Ответ на нажатие inline-кнопки (всплывашка у пользователя) и, если задан
    replace_message=(chat_id, message_id, text), замена текста сообщения без кнопок."""
    bot = Bot(token=settings.bot_token)
    try:
        await _send_with_retry(lambda: bot.answer_callback_query(callback_query_id, text=text))
        if replace_message is not None:
            chat_id, message_id, new_text = replace_message
            await _send_with_retry(
                lambda: bot.edit_message_text(new_text, chat_id=chat_id, message_id=message_id)
            )
    except TelegramAPIError as exc:
        raise TelegramSendError(str(exc)) from exc
    finally:
        await bot.session.close()


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
