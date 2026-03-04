"""
Команды для администратора/мастера.
Доступ только мастерам, у которых telegram_id совпадает с записью в БД.
"""

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import select

from tg_studio.db.models import Booking, BookingStatus, Client, Master, Service, TimeSlot
from tg_studio.db.session import async_session_factory

router = Router(name="admin")


async def _is_master(telegram_id: int) -> Master | None:
    async with async_session_factory() as session:
        result = await session.execute(
            select(Master).where(Master.telegram_id == telegram_id, Master.is_active.is_(True))
        )
        return result.scalar_one_or_none()


@router.message(Command("bookings"))
async def cmd_bookings(message: Message):
    master = await _is_master(message.from_user.id)
    if master is None:
        await message.answer("Команда доступна только мастерам.")
        return

    async with async_session_factory() as session:
        result = await session.execute(
            select(Booking, Client, Service, TimeSlot)
            .join(Client, Client.id == Booking.client_id)
            .join(Service, Service.id == Booking.service_id)
            .join(TimeSlot, TimeSlot.id == Booking.slot_id)
            .where(
                Booking.master_id == master.id,
                Booking.status == BookingStatus.confirmed,
            )
            .order_by(TimeSlot.starts_at)
        )
        rows = result.all()

    if not rows:
        await message.answer("Нет подтверждённых записей.")
        return

    lines = ["Ваши записи:\n"]
    for booking, client, service, slot in rows:
        starts_at = slot.starts_at.strftime("%d.%m %H:%M")
        lines.append(f"{starts_at} — {client.full_name} ({service.name})")

    await message.answer("\n".join(lines))


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "/bookings — список ваших записей\n"
        "/help — помощь"
    )
