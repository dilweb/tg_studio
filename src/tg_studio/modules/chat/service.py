"""Чаты с клиентами: приём сообщений ботом и отправка ответов из CRM.

Вся Telegram-I/O изолирована в функциях нижнего уровня (send_*_to_telegram,
download_photo) — тесты подменяют именно их.
"""

import logging
from datetime import datetime

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BufferedInputFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_studio.config import settings
from tg_studio.db.models import Business, ChatDirection, ChatMessage, Client, User, UserRole

logger = logging.getLogger(__name__)

MAX_PHOTO_BYTES = 10 * 1024 * 1024  # лимит Telegram для фото

# Медиа-типы для прокси GET /admin/chats/files/{id}: file_kind → mime.
# video/video_note/animation Telegram всегда конвертирует в mp4,
# voice — ogg; документ отдаём как octet-stream (браузер предложит скачать).
FILE_MEDIA_TYPES = {
    "photo": "image/jpeg",
    "voice": "audio/ogg",
    "video": "video/mp4",
    "video_note": "video/mp4",
    "animation": "video/mp4",
    "audio": "audio/mpeg",
    "document": "application/octet-stream",
}

# Метки медиа без текста в превью списка чатов
PREVIEW_LABELS = {
    "photo": "📷 Фото",
    "voice": "🎙 Голосовое",
    "video": "📎 Видео",
    "video_note": "📎 Видеосообщение",
    "animation": "📎 GIF",
    "audio": "📎 Аудио",
    "document": "📎 Документ",
}


class TelegramSendError(Exception):
    """Обёртка над ошибками Telegram API при отправке."""


def _new_bot() -> Bot:
    # Без parse_mode: клиентский текст с "<" не должен ломать отправку
    return Bot(token=settings.bot_token)


async def _close_bot(bot: Bot) -> None:
    # Иначе aiohttp-коннекторы текут при создании бота на каждый запрос
    await bot.session.close()


# ---------------------------------------------------------------------------
# Telegram I/O — нижний уровень
# ---------------------------------------------------------------------------

async def send_text_to_telegram(telegram_id: int, text: str) -> int:
    """Отправить текст клиенту. Возвращает telegram message_id."""
    bot = _new_bot()
    try:
        msg = await bot.send_message(telegram_id, text)
        return msg.message_id
    except TelegramAPIError as exc:
        raise TelegramSendError(str(exc)) from exc
    finally:
        await _close_bot(bot)


async def send_photo_to_telegram(
    telegram_id: int, photo: bytes, caption: str | None
) -> tuple[int, str]:
    """Отправить фото. Возвращает (message_id, file_id отправленного фото)."""
    bot = _new_bot()
    try:
        msg = await bot.send_photo(
            telegram_id,
            BufferedInputFile(photo, filename="photo.jpg"),
            caption=caption,
        )
        photo_file_id = msg.photo[-1].file_id if msg.photo else ""
        return msg.message_id, photo_file_id
    except TelegramAPIError as exc:
        raise TelegramSendError(str(exc)) from exc
    finally:
        await _close_bot(bot)


async def download_photo(file_id: str) -> bytes:
    """Скачать фото из Telegram по file_id."""
    bot = _new_bot()
    try:
        tg_file = await bot.get_file(file_id)
        buf = await bot.download_file(tg_file.file_path)
        return buf.getvalue()
    except TelegramAPIError as exc:
        raise TelegramSendError(str(exc)) from exc
    finally:
        await _close_bot(bot)


# ---------------------------------------------------------------------------
# Приём сообщений (используется ботом)
# ---------------------------------------------------------------------------

async def upsert_client_from_telegram(
    session: AsyncSession,
    *,
    telegram_id: int,
    first_name: str | None,
    last_name: str | None,
    username: str | None,
) -> Client:
    """Найти клиента по telegram_id или создать User(role=client) + Client."""
    result = await session.execute(select(Client).where(Client.telegram_id == telegram_id))
    client = result.scalar_one_or_none()
    full_name = " ".join(n for n in (first_name, last_name) if n) or f"tg_{telegram_id}"

    if client is None:
        user = User(
            telegram_id=telegram_id,
            first_name=first_name or full_name,
            last_name=last_name,
            role=UserRole.client,
        )
        session.add(user)
        await session.flush()
        client = Client(
            user_id=user.id,
            telegram_id=telegram_id,
            username=username,
            full_name=full_name,
        )
        session.add(client)
        await session.flush()
        return client

    # Имя/юзернейм могли измениться в Telegram — подтягиваем
    updated = False
    if client.full_name != full_name:
        client.full_name = full_name
        updated = True
    if client.username != username:
        client.username = username
        updated = True
    if updated:
        await session.flush()
    return client


async def is_new_chat(session: AsyncSession, client_id: int) -> bool:
    """Было ли у клиента сообщение до текущего (вызывать ДО сохранения).

    True — чата ещё не было ни разу: бот уведомит владельца о новом обращении.
    """
    result = await session.execute(
        select(func.count())
        .select_from(ChatMessage)
        .where(ChatMessage.client_id == client_id)
    )
    return result.scalar_one() == 0


async def save_incoming_message(
    session: AsyncSession,
    client: Client,
    *,
    content: str | None,
    telegram_file_id: str | None,
    file_kind: str | None = None,
) -> ChatMessage:
    """Входящее сообщение от клиента (текст, фото, стикер, голосовое)."""
    if file_kind is None and telegram_file_id:
        file_kind = "photo"
    msg = ChatMessage(
        client_id=client.id,
        direction=ChatDirection.from_client,
        content=content,
        telegram_file_id=telegram_file_id,
        file_kind=file_kind,
    )
    session.add(msg)
    await session.commit()
    return msg


async def resolve_owner_telegram_id(session: AsyncSession) -> int | None:
    """Кому слать уведомления о новых сообщениях клиентов.

    Клиенты не привязаны к бизнесу (Client без business_id) — один бизнес.
    Приоритет: businesses.owner_telegram_id → owner.telegram_id (User).
    """
    result = await session.execute(select(User).where(User.role == UserRole.owner))
    owner = result.scalars().first()
    if owner is None:
        return None
    business_result = await session.execute(
        select(Business).where(Business.owner_id == owner.id)
    )
    business = business_result.scalar_one_or_none()
    if business is not None and business.owner_telegram_id:
        return business.owner_telegram_id
    return owner.telegram_id


async def notify_owner(bot: Bot, client: Client, text: str) -> None:
    """Уведомить владельца о первом обращении нового клиента — best-effort.

    Вызывается только когда чата с клиентом ещё не было (is_new_chat):
    последующие сообщения в существующем чате владельца не дёргают.

    Владелец мог не стартовать бота — тогда Telegram API вернёт ошибку,
    молча логируем: сообщение клиента уже сохранено.
    """
    from tg_studio.db.session import async_session_factory

    async with async_session_factory() as session:
        owner_tg_id = await resolve_owner_telegram_id(session)
    if not owner_tg_id:
        logger.warning("Не найден владелец для уведомления о сообщении клиента")
        return
    handle = f" (@{client.username})" if client.username else ""
    try:
        await bot.send_message(
            owner_tg_id,
            f"🆕 Новое обращение от {client.full_name}{handle}:\n\n{text}",
            parse_mode=None,
        )
    except TelegramAPIError as exc:
        logger.warning("Не удалось уведомить владельца %s: %s", owner_tg_id, exc)


# ---------------------------------------------------------------------------
# Отправка из CRM
# ---------------------------------------------------------------------------

async def send_text_message(session: AsyncSession, client: Client, content: str) -> ChatMessage:
    """Ответ мастера: сохранить и отправить. Недоставленное НЕ сохраняем."""
    msg = ChatMessage(
        client_id=client.id,
        direction=ChatDirection.from_master,
        content=content,
    )
    session.add(msg)
    await session.flush()
    try:
        msg.telegram_message_id = await send_text_to_telegram(client.telegram_id, content)
    except TelegramSendError:
        await session.rollback()
        raise
    await session.commit()
    return msg


async def send_photo_message(
    session: AsyncSession, client: Client, photo: bytes, caption: str | None
) -> ChatMessage:
    """Ответ мастера фото (эскизом): сохранить и отправить."""
    msg = ChatMessage(
        client_id=client.id,
        direction=ChatDirection.from_master,
        content=caption,
        file_kind="photo",
    )
    session.add(msg)
    await session.flush()
    try:
        message_id, photo_file_id = await send_photo_to_telegram(client.telegram_id, photo, caption)
        msg.telegram_message_id = message_id
        msg.telegram_file_id = photo_file_id
    except TelegramSendError:
        await session.rollback()
        raise
    await session.commit()
    return msg


# ---------------------------------------------------------------------------
# Чтение (API)
# ---------------------------------------------------------------------------

async def list_threads(session: AsyncSession) -> list[dict]:
    """Список чатов: клиент + последнее сообщение + непрочитанные.

    Без DISTINCT ON (sqlite-тесты): последний id через подзапрос MAX(id).
    """
    result = await session.execute(
        select(ChatMessage).where(
            ChatMessage.id.in_(
                select(func.max(ChatMessage.id)).group_by(ChatMessage.client_id)
            )
        )
    )
    last_by_client = {m.client_id: m for m in result.scalars().all()}
    if not last_by_client:
        return []

    clients = await session.execute(
        select(Client).where(Client.id.in_(last_by_client.keys()))
    )
    clients_by_id = {c.id: c for c in clients.scalars().all()}

    unread = await session.execute(
        select(ChatMessage.client_id, func.count())
        .where(
            ChatMessage.direction == ChatDirection.from_client,
            ChatMessage.read_at.is_(None),
        )
        .group_by(ChatMessage.client_id)
    )
    unread_by_client = dict(unread.all())

    threads = []
    for client_id, last in sorted(
        last_by_client.items(),
        # id в тайбрейке: в Postgres now() одинаков в рамках транзакции
        key=lambda item: (item[1].created_at, item[1].id),
        reverse=True,
    ):
        client = clients_by_id.get(client_id)
        if client is None:
            continue
        threads.append(
            {
                "client_id": client_id,
                "full_name": client.full_name,
                "username": client.username,
                "last_message_at": last.created_at,
                "last_message_preview": _preview(last),
                "last_direction": last.direction.value,
                "unread_count": unread_by_client.get(client_id, 0),
            }
        )
    return threads


def preview_for(content: str | None, file_kind: str | None) -> str:
    """Короткий текст для превью в списке чатов.

    Используется и ботом (превью для уведомления владельцу), и list_threads.
    """
    if content:
        return content if len(content) <= 80 else content[:77] + "…"
    return PREVIEW_LABELS.get(file_kind or "", "📎 Вложение")


def _preview(msg: ChatMessage) -> str:
    return preview_for(msg.content, msg.file_kind)


async def get_client(session: AsyncSession, client_id: int) -> Client | None:
    result = await session.execute(select(Client).where(Client.id == client_id))
    return result.scalar_one_or_none()


async def list_messages(
    session: AsyncSession, client_id: int, after_id: int | None = None
) -> list[ChatMessage]:
    stmt = select(ChatMessage).where(ChatMessage.client_id == client_id)
    if after_id is not None:
        stmt = stmt.where(ChatMessage.id > after_id)
    stmt = stmt.order_by(ChatMessage.id.asc())
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def mark_thread_read(session: AsyncSession, client_id: int) -> int:
    result = await session.execute(
        select(ChatMessage).where(
            ChatMessage.client_id == client_id,
            ChatMessage.direction == ChatDirection.from_client,
            ChatMessage.read_at.is_(None),
        )
    )
    rows = result.scalars().all()
    now = datetime.now()
    for row in rows:
        row.read_at = now
    await session.commit()
    return len(rows)


async def get_message_file(session: AsyncSession, message_id: int) -> ChatMessage | None:
    result = await session.execute(select(ChatMessage).where(ChatMessage.id == message_id))
    msg = result.scalar_one_or_none()
    if msg is None or not msg.telegram_file_id:
        return None
    return msg
