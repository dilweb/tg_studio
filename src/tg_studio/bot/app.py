import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from tg_studio.bot.handlers import admin, start
from tg_studio.bot.handlers import booking_flow
from tg_studio.config import settings

logger = logging.getLogger(__name__)


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    # MemoryStorage — FSM-состояния хранятся в памяти процесса.
    # При перезапуске бота состояния сбрасываются (для MVP это нормально).
    # Для продакшна заменить на RedisStorage.
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)

    dp.include_router(start.router)
    dp.include_router(booking_flow.router)
    dp.include_router(admin.router)

    logger.info("Starting bot polling...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
