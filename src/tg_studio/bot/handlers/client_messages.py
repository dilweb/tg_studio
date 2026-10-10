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
import re

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import select

from tg_studio.api.auth import resolve_allowed_role
from tg_studio.db.models import Master, MasterPortfolioFile
from tg_studio.db.session import async_session_factory
from tg_studio.modules.ai import booking
from tg_studio.modules.ai.router import ROUTE_AGENT, classify_message
from tg_studio.modules.business import portfolio_files
from tg_studio.modules.chat import service

logger = logging.getLogger(__name__)

router = Router(name="client_messages")

# Режим клиента для staff-пользователей: по умолчанию owner/мастера боту
# отвечают отказом (их канал — миниаппа), /switchrole переключает в режим
# клиента, чтобы тестировать чат и AI-агента. В памяти процесса: после
# рестарта бота режим сбрасывается — снова /switchrole.
_client_mode: set[int] = set()

# Заглушки для контента, который нельзя скачать (message.content_type)
_STUB_LABELS = {
    "contact": "📎 Контакт",
    "location": "📍 Локация",
    "poll": "📎 Опрос",
}

# Ссылки на фото из ответа агента (портфолио/аватар): бот скачивает файл и
# отправляет настоящим фото, а ссылку из текста убирает. База ссылки может
# быть любым хостом (api_public_url) — матчем путь.
_MEDIA_URL_RE = re.compile(
    r"https?://[^\s<>()\"]+?/api/public/masters/(?P<mid>\d+)"
    r"(?:/portfolio/(?P<pid>\d+)|/avatar)"
)


def extract_media_urls(text: str) -> list[tuple[str, int, int | None]]:
    """Уникальные медиа-ссылки из текста: [(url, master_id, portfolio_id|None)]."""
    seen: dict[str, tuple[int, int | None]] = {}
    for m in _MEDIA_URL_RE.finditer(text):
        pid = m.group("pid")
        seen[m.group(0)] = (int(m.group("mid")), int(pid) if pid else None)
    return [(url, mid, pid) for url, (mid, pid) in seen.items()]


def group_media_by_master(
    media: list[tuple[str, int, int | None]],
) -> list[tuple[int, str | None, list[tuple[str, int]]]]:
    """Медиа по мастерам в порядке появления:
    [(master_id, avatar_url|None, [(portfolio_url, portfolio_id), ...])]."""
    groups: dict[int, tuple[str | None, list[tuple[str, int]]]] = {}
    for url, mid, pid in media:
        if mid not in groups:
            groups[mid] = (None, [])
        avatar, portfolio = groups[mid]
        if pid is None:
            groups[mid] = (url, portfolio)
        else:
            portfolio.append((url, pid))
    return [(mid, *g) for mid, g in groups.items()]


def strip_urls(text: str, urls: list[str]) -> str:
    """Убрать отправленные ссылки; строки, состоявшие только из ссылок,
    исчезают целиком, схлопываются пустые строки."""
    lines = text.splitlines()
    kept: list[str] = []
    for line in lines:
        cleaned = line
        for url in urls:
            cleaned = cleaned.replace(url, "")
        if not cleaned.strip() and any(url in line for url in urls):
            continue  # строка была только из ссылок — убрать целиком
        kept.append(cleaned.rstrip())
    return "\n".join(kept).strip()


async def _is_staff(telegram_id: int) -> bool:
    """Мастер (привязан к бизнесу) или кто-то из ALLOWED_USERS (owner/master).

    Staff в режиме клиента (/switchrole) считаем клиентом — иначе его
    сообщения не доходят до агента записи и «Чатов».
    """
    if telegram_id in _client_mode:
        return False
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
    allow_agent: bool = False,
    allow_agent_with_photo: bool = False,
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
        if allow_agent and content:
            await _maybe_agent_reply(session, message, client, content)
        elif allow_agent_with_photo and telegram_file_id:
            # Фото (эскиз/референс): агент с мультимодальной моделью видит
            # картинку; подпись может отсутствовать
            await _maybe_agent_reply(
                session, message, client, content or "",
                photo_file_id=telegram_file_id,
            )


TG_ALBUM_LIMIT = 10  # максимум фото в одном альбоме Telegram
TG_CAPTION_LIMIT = 1024


def _master_caption(master: Master) -> str:
    """Подпись профайл-пика: имя мастера + описание (лимит подписи TG)."""
    desc = (master.description or "").strip()
    caption = master.full_name + (f"\n{desc}" if desc else "")
    return caption[:TG_CAPTION_LIMIT]


def _master_avatar_bytes(master: Master | None) -> bytes | None:
    if master is None or not master.is_active or master.deleted_at is not None:
        return None
    return portfolio_files.read_avatar(master)


async def _portfolio_bytes(
    session, master_id: int, portfolio_id: int
) -> bytes | None:
    """Байты фото портфолио по ссылке из ответа агента."""
    record = await session.get(MasterPortfolioFile, portfolio_id)
    if record is None or record.master_id != master_id:
        return None
    return portfolio_files.read_file(record)


async def _send_photo_saved(
    session, client, data: bytes, caption: str | None
) -> bool:
    """Одно фото клиенту + зеркало в чат. False при ошибке отправки."""
    try:
        tg_msg_id, tg_file_id = await service.send_photo_to_telegram(
            client.telegram_id, data, caption=caption
        )
    except service.TelegramSendError:
        logger.warning("Не удалось отправить фото клиенту")
        return False
    await service.save_ai_photo_message(
        session, client, caption=caption,
        telegram_message_id=tg_msg_id, telegram_file_id=tg_file_id,
    )
    return True


async def _send_album_saved(
    session, client, datas: list[bytes]
) -> list[bool]:
    """Альбом 2-10 фото клиенту + зеркало по каждому; сбой альбома → по одному."""
    try:
        results = await service.send_media_group_to_telegram(
            client.telegram_id, datas
        )
    except service.TelegramSendError:
        logger.warning("Альбом не отправился — отправляю по одному")
        return [await _send_photo_saved(session, client, d, None) for d in datas]
    for tg_msg_id, tg_file_id in results:
        await service.save_ai_photo_message(
            session, client, caption=None,
            telegram_message_id=tg_msg_id, telegram_file_id=tg_file_id,
        )
    return [True] * len(datas)


async def _send_reply_photos(session, client, reply: str) -> tuple[str, int]:
    """Ссылки на фото из ответа агента → настоящие фото клиенту.

    По каждому мастеру: аватар отдельным сообщением с именем и описанием,
    портфолио — альбомом (до 10, дальше — следующий альбом). Отправленные
    ссылки убираются из текста; нескачанные/неотправленные остаются
    ссылками. Возвращает (текст, сколько фото отправлено).
    """
    media = extract_media_urls(reply)
    if not media:
        return reply, 0

    sent_urls: list[str] = []
    sent_count = 0
    for master_id, avatar_url, portfolio in group_media_by_master(media):
        master = await session.get(Master, master_id)
        # Профайл-пик мастера отдельным сообщением — с описанием
        if avatar_url is not None:
            data = _master_avatar_bytes(master)
            if data is None or master is None:
                logger.warning(
                    "Аватар мастера %s не найден — остаётся ссылкой", master_id
                )
            elif await _send_photo_saved(session, client, data, _master_caption(master)):
                sent_urls.append(avatar_url)
                sent_count += 1
        # Портфолио — альбомами по TG_ALBUM_LIMIT
        photos: list[tuple[str, bytes]] = []
        for url, portfolio_id in portfolio:
            data = await _portfolio_bytes(session, master_id, portfolio_id)
            if data is None:
                logger.warning(
                    "Медиа из ответа агента не найдено: %s — остаётся ссылкой", url
                )
                continue
            photos.append((url, data))
        for i in range(0, len(photos), TG_ALBUM_LIMIT):
            chunk = photos[i : i + TG_ALBUM_LIMIT]
            datas = [d for _, d in chunk]
            ok_flags = (
                await _send_album_saved(session, client, datas)
                if len(datas) > 1
                else [await _send_photo_saved(session, client, datas[0], None)]
            )
            for (url, _), ok in zip(chunk, ok_flags):
                if ok:
                    sent_urls.append(url)
                    sent_count += 1
    return strip_urls(reply, sent_urls), sent_count


async def _maybe_agent_reply(
    session,
    message: Message,
    client,
    text: str,
    *,
    photo_file_id: str | None = None,
) -> None:
    """AI-агент записи отвечает на текст клиента — если тот адресован ему.

    Роутер вызывается только при открытом закреплении чата (до первой
    эскалации всё агенту без LLM). Ответ зеркалим в chat_messages
    (from_ai) — мастер/владелец видят его в «Чатах». С photo_file_id агенту
    уходит и картинка (эскиз/референс) — нужна мультимодальная модель.
    """
    business = await service.get_single_business(session)
    if business is None:
        logger.warning("No business — booking agent disabled")
        return
    route = await classify_message(session, client.id, text)
    if route != ROUTE_AGENT:
        logger.info("Client %s message routed to %s — no agent reply", client.id, route)
        return
    try:
        await message.bot.send_chat_action(message.chat.id, action="typing")
    except Exception:
        pass  # индикатор не критичен
    images: list[booking.ChatImage] | None = None
    if photo_file_id:
        try:
            data = await service.download_photo(photo_file_id)
        except service.TelegramSendError:
            logger.warning("Не удалось скачать фото клиента — отвечаем текстом")
            data = None
        if data is not None:
            images = [booking.ChatImage(data=data, mime="image/jpeg")]
    reply = await booking.run_booking_turn(
        session, business, client, text, images=images
    )
    if not reply:
        return
    # Ссылки на портфолио/аватар → настоящие фото (текст очищается)
    reply, photo_count = await _send_reply_photos(session, client, reply)
    if not reply and not photo_count:
        return
    if reply:
        try:
            telegram_message_id = await service.send_text_to_telegram(
                client.telegram_id, reply
            )
        except service.TelegramSendError:
            logger.exception("Failed to send agent reply to client %s", client.id)
            return
        await service.save_ai_message(
            session, client, reply, telegram_message_id=telegram_message_id
        )


@router.message(Command("switchrole"))
async def handle_switch_role(message: Message) -> None:
    """Переключить режим staff-пользователя: владелец/мастер ↔ клиент.

    Для тестирования клиентского чата и AI-агента своей учёткой.
    """
    tg_user = message.from_user
    if tg_user is None:
        return
    is_master = False
    async with async_session_factory() as session:
        result = await session.execute(
            select(Master).where(
                Master.telegram_id == tg_user.id, Master.is_active.is_(True)
            )
        )
        is_master = result.scalar_one_or_none() is not None
    if resolve_allowed_role(tg_user.id) is None and not is_master:
        # обычному клиенту команда не нужна (он всегда клиент)
        await message.answer("Эта команда — для владельца и мастеров.")
        return
    if tg_user.id in _client_mode:
        _client_mode.discard(tg_user.id)
        await message.answer(
            "Режим: <b>владелец/мастер</b>. Клиентские сообщения отключены — "
            "работайте в Mini App. Вернуться клиентом: /switchrole"
        )
    else:
        _client_mode.add(tg_user.id)
        await message.answer(
            "Режим: <b>клиент</b>. Пишите сюда как клиент — сообщения падают в "
            "«Чаты», AI-агент записи отвечает и эскалирует. Вернуться: /switchrole"
        )


@router.message(F.chat.type == "private", F.text)
async def handle_client_text(message: Message) -> None:
    await _handle(
        message, content=message.text or None, telegram_file_id=None, allow_agent=True
    )


@router.message(F.chat.type == "private", F.photo)
async def handle_client_photo(message: Message) -> None:
    # Самое большое фото в альбоме/сообщении — последний элемент.
    # Фото идёт агенту (мультимодальная модель видит эскиз/референс),
    # с подписью или без.
    file_id = message.photo[-1].file_id if message.photo else None
    await _handle(
        message,
        content=message.caption,
        telegram_file_id=file_id,
        allow_agent_with_photo=True,
    )


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
