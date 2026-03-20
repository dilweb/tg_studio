from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import select

from tg_studio.db.models import Master
from tg_studio.db.session import async_session_factory

router = Router(name="start")

REGISTRATION_PREFIX = "master_"


def _main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="Записаться на сеанс", callback_data="book:start"),
        ]]
    )


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    telegram_id = message.from_user.id if message.from_user else None
    if not telegram_id:
        await message.answer("Не удалось определить пользователя.")
        return

    # Проверяем глубокую ссылку регистрации мастера: /start master_<token>
    text = (message.text or "").strip()
    parts = text.split(maxsplit=1)
    if len(parts) >= 2 and parts[1].startswith(REGISTRATION_PREFIX):
        token = parts[1][len(REGISTRATION_PREFIX) :].strip()
        if token:
            async with async_session_factory() as session:
                result = await session.execute(
                    select(Master).where(Master.registration_token == token)
                )
                master = result.scalar_one_or_none()
                if master is None:
                    await message.answer(
                        "Ссылка недействительна или уже использована. "
                        "Попросите владельца бизнеса создать новую ссылку в админке."
                    )
                    return
                if master.telegram_id == telegram_id:
                    await message.answer(
                        f"Вы уже зарегистрированы как мастер {master.full_name}.\n\n"
                        "Команды: /bookings — ваши записи, /help — помощь.",
                        reply_markup=_main_kb(),
                    )
                    return
                # Один Telegram — один мастер
                other = await session.execute(
                    select(Master).where(
                        Master.telegram_id == telegram_id,
                        Master.id != master.id,
                    )
                )
                if other.scalar_one_or_none():
                    await message.answer(
                        "Этот Telegram уже привязан к другому мастеру. "
                        "Используйте другой аккаунт или обратитесь к владельцу бизнеса."
                    )
                    return
                master.telegram_id = telegram_id
                master.registration_token = None  # одноразовая ссылка
                await session.commit()
            await message.answer(
                f"Вы зарегистрированы как мастер <b>{master.full_name}</b>.\n\n"
                "Теперь вам будут приходить уведомления о новых записях. "
                "Команды: /bookings — ваши записи, /help — помощь.",
                reply_markup=_main_kb(),
            )
            return

    await message.answer(
        "Привет! Здесь вы можете записаться на сеанс и оплатить предоплату.",
        reply_markup=_main_kb(),
    )
