"""
Чат клиент–мастер после оплаты.

Обычный текст от пользователя считается сообщением мастеру только если у клиента
есть оплаченная бронь (Payment.status == paid). Иначе — подсказка записаться и оплатить.
"""

import logging

from aiogram import F, Router
from aiogram.types import Message
from sqlalchemy import select

from tg_studio.db.models import (
    Booking,
    BookingChatMessage,
    BookingStatus,
    Client,
    Master,
    Payment,
    PaymentStatus,
)
from tg_studio.db.session import async_session_factory

logger = logging.getLogger(__name__)
router = Router(name="client_chat")


async def _get_client_paid_booking(telegram_id: int) -> tuple[Client | None, Booking | None]:
    """
    Найти клиента по telegram_id и его последнюю оплаченную бронь
    (payment paid, бронь не отменена).
    """
    async with async_session_factory() as session:
        client_result = await session.execute(
            select(Client).where(Client.telegram_id == telegram_id)
        )
        client = client_result.scalar_one_or_none()
        if client is None:
            return None, None

        booking_result = await session.execute(
            select(Booking)
            .join(Payment, Payment.booking_id == Booking.id)
            .where(
                Booking.client_id == client.id,
                Payment.status == PaymentStatus.paid,
                Booking.status != BookingStatus.cancelled,
            )
            .order_by(Booking.created_at.desc())
            .limit(1)
        )
        booking = booking_result.scalar_one_or_none()
        return client, booking


@router.message(F.text, ~F.text.startswith("/"))
async def on_client_text(message: Message):
    """
    Любой текст от пользователя: проверяем, есть ли оплаченная бронь.
    Если да — сохраняем сообщение и уведомляем мастера; если нет — подсказка.
    """
    telegram_id = message.from_user.id if message.from_user else None
    if not telegram_id:
        return

    text = (message.text or "").strip()
    if not text:
        return

    client, booking = await _get_client_paid_booking(telegram_id)
    if booking is None or client is None:
        await message.answer(
            "Чтобы писать мастеру, сначала нужно оплатить запись.\n"
            "Нажмите /start чтобы записаться и оплатить."
        )
        return

    async with async_session_factory() as session:
        chat_msg = BookingChatMessage(
            booking_id=booking.id,
            sender_type="client",
            content=text,
        )
        session.add(chat_msg)
        await session.commit()

    # Уведомить мастера в Telegram
    async with async_session_factory() as session:
        master_result = await session.execute(
            select(Master).where(Master.id == booking.master_id)
        )
        master = master_result.scalar_one_or_none()

    if master and master.telegram_id:
        from aiogram import Bot
        from tg_studio.config import settings

        bot = Bot(token=settings.bot_token)
        try:
            await bot.send_message(
                chat_id=master.telegram_id,
                text=f"💬 <b>Клиент {client.full_name}</b> (запись #{booking.id}):\n\n{text}",
            )
        except Exception:
            logger.exception(
                "Failed to notify master telegram_id=%s about client message",
                master.telegram_id,
            )
        finally:
            await bot.session.close()

    await message.answer("Сообщение отправлено мастеру.")
