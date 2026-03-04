from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tg_studio.db.models import ServiceType

class MasterCB(CallbackData, prefix="master"):
    id: int


class ServiceCB(CallbackData, prefix="service"):
    id: int


class DurationCB(CallbackData, prefix="duration"):
    hours: int


class DateCB(CallbackData, prefix="date"):
    value: str  # "YYYY-MM-DD"


class TimeCB(CallbackData, prefix="time", sep="|"):
    value: str  # "HH:MM"


class ConfirmCB(CallbackData, prefix="confirm"):
    action: str  # "yes" | "no"


def masters_kb(masters: list[dict]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text=m["full_name"],
                callback_data=MasterCB(id=m["id"]).pack(),
            )]
            for m in masters
        ]
    )


def services_kb(services: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for s in services:
        service_type = s.get("service_type", ServiceType.appointment)

        if service_type == ServiceType.appointment:
            label = f"{s['name']} — {int(s['price'])} тг/ч"
        else:
            label = f"{s['name']} — {int(s['price'])} тг"
        rows.append([InlineKeyboardButton(
            text=label,
            callback_data=ServiceCB(id=s["id"]).pack(),
        )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def duration_kb() -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(
            text=f"{h} ч",
            callback_data=DurationCB(hours=h).pack(),
        )
        for h in range(1, 9)
    ]
    # По 4 кнопки в ряд
    rows = [buttons[i:i+4] for i in range(0, len(buttons), 4)]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def dates_kb(dates: list[str]) -> InlineKeyboardMarkup:
    """dates — список строк "YYYY-MM-DD"."""
    from datetime import date
    rows = []
    for d in dates:
        parsed = date.fromisoformat(d)
        label = parsed.strftime("%d %b")  # "10 Mar"
        rows.append([InlineKeyboardButton(
            text=label,
            callback_data=DateCB(value=d).pack(),
        )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def times_kb(slots: list[dict]) -> InlineKeyboardMarkup:
    """slots — список {"starts_at": "...", "ends_at": "..."}."""
    buttons = []
    for s in slots:
        time_str = s["starts_at"][11:16]  # "HH:MM" из ISO
        buttons.append(InlineKeyboardButton(
            text=time_str,
            callback_data=TimeCB(value=time_str).pack(),
        ))
    rows = [buttons[i:i+4] for i in range(0, len(buttons), 4)]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="Оплатить", callback_data=ConfirmCB(action="yes").pack()),
            InlineKeyboardButton(text="Отмена", callback_data=ConfirmCB(action="no").pack()),
        ]]
    )
