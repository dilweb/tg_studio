"""Чаты с клиентами: приём сообщений ботом и отправка ответов из CRM.

Вся Telegram-I/O изолирована в функциях нижнего уровня (send_*_to_telegram,
download_photo) — тесты подменяют именно их.
"""

import asyncio
import hashlib
import hmac
import logging
import subprocess
import time
from datetime import datetime

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BufferedInputFile, InlineKeyboardMarkup, InputMediaPhoto
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tg_studio.config import settings
from tg_studio.db.models import (
    Business,
    ChatAssignment,
    ChatAssignmentStatus,
    ChatDirection,
    ChatMessage,
    Client,
    Master,
    User,
    UserRole,
)

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


# ---------------------------------------------------------------------------
# Подписанные ссылки на файлы
# ---------------------------------------------------------------------------
# Документы в миниаппе отдаются прямой ссылкой (?t=<token>): blob-ссылки
# живут только внутри страницы — вебвью/браузер пытается «открыть» их как
# файл и предлагает найти приложение (macOS: «Do you want to open blob:…»).
# Прямая ссылка с Content-Disposition: attachment скачивается штатно
# и в обычном браузере, и в системном браузере с телефона.

FILE_TOKEN_TTL = 3600  # час; фронт перегенерирует ссылки на каждом тике опроса


def make_file_token(message_id: int, now: int | None = None) -> str:
    """HMAC-подпись "<id>:<exp>" ключом JWT — доступ к одному файлу на час."""
    exp = (now if now is not None else int(time.time())) + FILE_TOKEN_TTL
    payload = f"{message_id}:{exp}"
    sig = hmac.new(
        settings.jwt_secret_key.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()[:32]
    return f"{exp}:{sig}"


def verify_file_token(message_id: int, token: str, now: int | None = None) -> bool:
    try:
        exp_str, sig = token.rsplit(":", 1)
        exp = int(exp_str)
    except (AttributeError, ValueError):
        return False
    if exp < (now if now is not None else int(time.time())):
        return False
    expected = hmac.new(
        settings.jwt_secret_key.encode(),
        f"{message_id}:{exp}".encode(),
        hashlib.sha256,
    ).hexdigest()[:32]
    return hmac.compare_digest(sig, expected)


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

async def send_text_to_telegram(
    telegram_id: int, text: str, reply_markup: InlineKeyboardMarkup | None = None
) -> int:
    """Отправить текст клиенту. Возвращает telegram message_id."""
    bot = _new_bot()
    try:
        msg = await bot.send_message(telegram_id, text, reply_markup=reply_markup)
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


async def send_media_group_to_telegram(
    telegram_id: int, photos: list[bytes], caption: str | None = None
) -> list[tuple[int, str]]:
    """Отправить альбом (2-10 фото, подпись — у первого). Возвращает
    [(message_id, file_id), ...] в порядке отправки."""
    bot = _new_bot()
    try:
        media = [
            InputMediaPhoto(
                media=BufferedInputFile(data, filename="photo.jpg"),
                caption=caption if i == 0 else None,
            )
            for i, data in enumerate(photos)
        ]
        msgs = await bot.send_media_group(telegram_id, media=media)
        return [
            (m.message_id, m.photo[-1].file_id if m.photo else "")
            for m in msgs
        ]
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
        # User с этим telegram_id мог уже существовать (owner/мастер тестируют
        # бот в режиме клиента через /switchrole) — переиспользуем его
        user_result = await session.execute(
            select(User).where(User.telegram_id == telegram_id)
        )
        user = user_result.scalar_one_or_none()
        if user is None:
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


async def get_single_business(session: AsyncSession) -> Business | None:
    """Единственный бизнес (клиенты не привязаны к бизнесу — один бизнес)."""
    result = await session.execute(select(Business).limit(1))
    return result.scalar_one_or_none()


async def save_ai_message(
    session: AsyncSession, client: Client, content: str, *, telegram_message_id: int | None
) -> ChatMessage:
    """Ответ AI-агента клиенту: зеркало в chat_messages (direction=from_ai)."""
    msg = ChatMessage(
        client_id=client.id,
        direction=ChatDirection.from_ai,
        content=content,
        telegram_message_id=telegram_message_id,
    )
    session.add(msg)
    await session.commit()
    return msg


async def save_ai_photo_message(
    session: AsyncSession,
    client: Client,
    caption: str | None,
    *,
    telegram_message_id: int,
    telegram_file_id: str,
) -> ChatMessage:
    """Фото от AI-агента (портфолио мастера): зеркало с file_kind=photo."""
    msg = ChatMessage(
        client_id=client.id,
        direction=ChatDirection.from_ai,
        content=caption,
        file_kind="photo",
        telegram_message_id=telegram_message_id,
        telegram_file_id=telegram_file_id,
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

async def list_threads(session: AsyncSession, master_id: int | None = None) -> list[dict]:
    """Список чатов: клиент + последнее сообщение + непрочитанные.

    master_id — фильтр для мастера: только чаты, закреплённые за ним открытым
    assignment'ом (владелец видит все). Без DISTINCT ON (sqlite-тесты):
    последний id через подзапрос MAX(id).
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

    # Мастер: только клиенты с открытым закреплением за ним
    if master_id is not None:
        pinned = await session.execute(
            select(ChatAssignment.client_id).where(
                ChatAssignment.master_id == master_id,
                ChatAssignment.status == ChatAssignmentStatus.open,
            )
        )
        # set() один раз: повторный .all() по тому же Result вернёт пустоту —
        # курсор уже исчерпан, и фильтр выкинет все чаты
        pinned_ids = set(pinned.scalars().all())
        last_by_client = {
            cid: msg for cid, msg in last_by_client.items() if cid in pinned_ids
        }
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

    # Открытые закрепления чатов за мастерами (после эскалации AI-агентом)
    assignment_rows = await session.execute(
        select(ChatAssignment.client_id, Master.full_name)
        .join(Master, Master.id == ChatAssignment.master_id)
        .where(
            ChatAssignment.client_id.in_(last_by_client.keys()),
            ChatAssignment.status == ChatAssignmentStatus.open,
        )
    )
    assigned_by_client = dict(assignment_rows.all())

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
                "assigned_master_name": assigned_by_client.get(client_id),
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


def _transcode_voice_to_mp3(data: bytes) -> bytes | None:
    """Ogg/Opus голосовое → mp3.

    WebKit (iOS, Telegram на macOS) не умеет ogg — blob-аудио в миниаппе
    просто не загружается. None — ffmpeg нет или перекодировать не удалось:
    тогда прокси отдаёт оригинал как есть.
    """
    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
                "-map", "a", "-c:a", "libmp3lame", "-b:a", "64k", "-f", "mp3",
                "pipe:1",
            ],
            input=data,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        return None
    if proc.returncode != 0 or not proc.stdout:
        return None
    return proc.stdout


async def transcode_voice_to_mp3(data: bytes) -> bytes | None:
    """Обёртка в тредпул: subprocess блокирует event loop, файл может быть до 20 МБ."""
    return await asyncio.to_thread(_transcode_voice_to_mp3, data)


async def get_message_file(session: AsyncSession, message_id: int) -> ChatMessage | None:
    result = await session.execute(select(ChatMessage).where(ChatMessage.id == message_id))
    msg = result.scalar_one_or_none()
    if msg is None or not msg.telegram_file_id:
        return None
    return msg
