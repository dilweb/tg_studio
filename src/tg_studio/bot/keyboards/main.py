from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from tg_studio.config import settings


def get_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Записаться на сеанс",
                    web_app=WebAppInfo(url=settings.miniapp_url),
                )
            ]
        ]
    )
