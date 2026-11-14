#!/usr/bin/env python3
"""Регистрация вебхука Telegram для inline-кнопок напоминаний («Подтвердить»).

Нужны BOT_TOKEN, TELEGRAM_WEBHOOK_SECRET и API_PUBLIC_URL (публичный https API).
Telegram будет слать нажатия на {API_PUBLIC_URL}/api/telegram/webhook.

    uv run python scripts/set_telegram_webhook.py          # зарегистрировать
    uv run python scripts/set_telegram_webhook.py --info   # текущее состояние
"""

import argparse
import asyncio

from aiogram import Bot

from tg_studio.config import settings


async def main(info: bool) -> None:
    bot = Bot(token=settings.bot_token)
    try:
        if not info:
            if not (settings.telegram_webhook_secret and settings.api_public_url):
                raise SystemExit("Задайте TELEGRAM_WEBHOOK_SECRET и API_PUBLIC_URL")
            url = settings.api_public_url.rstrip("/") + "/api/telegram/webhook"
            await bot.set_webhook(
                url, secret_token=settings.telegram_webhook_secret, allowed_updates=["callback_query"]
            )
            print(f"Вебхук установлен: {url}")
        print(await bot.get_webhook_info())
    finally:
        await bot.session.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--info", action="store_true", help="только показать состояние вебхука")
    asyncio.run(main(parser.parse_args().info))
