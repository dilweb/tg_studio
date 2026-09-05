"""
Команды для администратора/мастера.
Доступ только мастерам, у которых telegram_id совпадает с записью в БД.
"""

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import select

from tg_studio.db.models import Master
from tg_studio.db.session import async_session_factory

router = Router(name="admin")


async def _is_master(telegram_id: int) -> Master | None:
    async with async_session_factory() as session:
        result = await session.execute(
            select(Master).where(Master.telegram_id == telegram_id, Master.is_active.is_(True))
        )
        return result.scalar_one_or_none()


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "/help — помощь"
    )
