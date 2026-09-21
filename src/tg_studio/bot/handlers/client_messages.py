"""Личные сообщения клиентов боту.

Всё от клиента падает в chat_messages: текст, фото, стикеры (эмодзи),
голосовые/видео/кружочки/GIF/аудио/документы (file_id — проигрываются
и скачиваются в миниаппе через прокси). Нескачиваемое (контакт, локация,
опрос) сохраняется заглушкой — ничего не теряется молча. Владелец получает
уведомление только на первое сообщение клиента (новое обращение). Мастера
и владелец сюда не попадают: им вежливо отказываем — их канал общения
с клиентами это миниапп (раздел «Чаты»).

Роутер регистрируется ПОСЛЕДНИМ (после start и admin), чтобы не перехватить
/start и /help.
"""

import logging

from aiogram import F, Router
from aiogram.types import Message
from sqlalchemy import select

from tg_studio.api.auth import resolve_allowed_role
from tg_studio.db.models import Master
from tg_studio.db.session import async_session_factory
from tg_studio.modules.chat import service

logger = logging.getLogger(__name__)

router = Router(name="client_messages")

# Заглушки для контента, который нельзя скачать (message.content_type)
_STUB_LABELS = {
    "contact": "📎 Контакт",
    "location": "📍 Локация",
    "poll": "📎 Опрос",
}


async def _is_staff(telegram_id: int) -> bool:
    """Мастер (привязан к бизнесу) или кто-то из ALLOWED_USERS (owner/master)."""
    async with async_session_factory() as session:
        result = await session.execute(
            select(Master).where(
                Master.telegram_id == telegram_id, Master.is_active.is_(True)
            )
        )
        if result.scalar_one_or_none() is not None:
            return True
    return resolve_allowed_role(telegram_id) is not None


async def _handle(
    message: Message,
    *,
    content: str | None,
    telegram_file_id: str | None,
    file_kind: str | None = None,
) -> None:
    tg_user = message.from_user
    if tg_user is None:
        return
    if await _is_staff(tg_user.id):
        await message.answer(
            "Этот чат — для общения клиентов с мастерской. "
            "Для работы используйте Mini App."
        )
        return

    preview = service.preview_for(content, file_kind)
    async with async_session_factory() as session:
        client = await service.upsert_client_from_telegram(
            session,
            telegram_id=tg_user.id,
            first_name=tg_user.first_name,
            last_name=tg_user.last_name,
            username=tg_user.username,
        )
        # Уведомление владельцу — только если чата с клиентом ещё не было
        is_new = await service.is_new_chat(session, client.id)
        await service.save_incoming_message(
            session,
            client,
            content=content,
            telegram_file_id=telegram_file_id,
            file_kind=file_kind,
        )
        if is_new:
            await service.notify_owner(message.bot, client, preview)


@router.message(F.chat.type == "private", F.text)
async def handle_client_text(message: Message) -> None:
    await _handle(message, content=message.text or None, telegram_file_id=None)


@router.message(F.chat.type == "private", F.photo)
async def handle_client_photo(message: Message) -> None:
    # Самое большое фото в альбоме/сообщении — последний элемент
    file_id = message.photo[-1].file_id if message.photo else None
    await _handle(message, content=message.caption, telegram_file_id=file_id)


@router.message(F.chat.type == "private", F.sticker)
async def handle_client_sticker(message: Message) -> None:
    # Храним эмодзи-эквивалент стикера: сам файл (webp/tgs/webm) в миниаппе
    # надёжно не отрисовать. Обычный стикер всегда имеет emoji.
    await _handle(message, content=message.sticker.emoji or "(стикер)", telegram_file_id=None)


@router.message(F.chat.type == "private", F.voice)
async def handle_client_voice(message: Message) -> None:
    await _handle(
        message,
        content=message.caption,
        telegram_file_id=message.voice.file_id,
        file_kind="voice",
    )


@router.message(
    F.chat.type == "private",
    F.video | F.video_note | F.animation | F.audio | F.document,
)
async def handle_client_media(message: Message) -> None:
    """Видео, кружочки, GIF, аудио, документы: сохраняем file_id — файл
    проигрывается/скачивается в миниаппе через прокси."""
    content = message.caption
    file_id = file_kind = None
    if message.video:
        file_id, file_kind = message.video.file_id, "video"
    elif message.video_note:
        file_id, file_kind = message.video_note.file_id, "video_note"
    elif message.animation:
        file_id, file_kind = message.animation.file_id, "animation"
    elif message.audio:
        file_id, file_kind = message.audio.file_id, "audio"
    else:
        doc = message.document
        file_id, file_kind = doc.file_id, "document"
        # имя файла — чтобы его было видно в чате; подпись приоритетнее
        content = message.caption or doc.file_name
    await _handle(message, content=content, telegram_file_id=file_id, file_kind=file_kind)


@router.message(F.chat.type == "private")
async def handle_client_other(message: Message) -> None:
    """Нескачиваемое (контакт, локация, опрос…) — заглушкой, не молча."""
    label = _STUB_LABELS.get(message.content_type, f"📎 {message.content_type}")
    await _handle(message, content=label, telegram_file_id=None)
