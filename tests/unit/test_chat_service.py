"""Юнит-тесты chat service: upsert клиента, сохранение, mark_read, owner."""

import shutil
import subprocess
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from tg_studio.db.models import (
    Business,
    ChatDirection,
    ChatMessage,
    Client,
    User,
    UserRole,
)
from tg_studio.modules.chat import service

from ..conftest import make_client


@pytest.mark.asyncio
async def test_upsert_client_creates_once(db_session):
    first = await service.upsert_client_from_telegram(
        db_session,
        telegram_id=777,
        first_name="Иван",
        last_name=None,
        username="ivan_t",
    )
    assert first.telegram_id == 777
    assert first.full_name == "Иван"
    assert first.username == "ivan_t"

    # повторный вызов — тот же клиент, а не дубль
    second = await service.upsert_client_from_telegram(
        db_session,
        telegram_id=777,
        first_name="Иван",
        last_name=None,
        username="ivan_t",
    )
    assert second.id == first.id


@pytest.mark.asyncio
async def test_upsert_client_updates_profile(db_session):
    client = await service.upsert_client_from_telegram(
        db_session,
        telegram_id=778,
        first_name="СтароеИмя",
        last_name=None,
        username="old_u",
    )
    updated = await service.upsert_client_from_telegram(
        db_session,
        telegram_id=778,
        first_name="НовоеИмя",
        last_name="Фамилия",
        username="new_u",
    )
    assert updated.id == client.id
    assert updated.full_name == "НовоеИмя Фамилия"
    assert updated.username == "new_u"

    # ленивую связь .user в async не грузим — берём прямым select
    user = (
        await db_session.execute(select(User).where(User.telegram_id == 778))
    ).scalar_one()
    assert user.role == UserRole.client


@pytest.mark.asyncio
async def test_save_incoming_message(db_session):
    client = await make_client(db_session, telegram_id=42)
    msg = await service.save_incoming_message(
        db_session, client, content="Привет", telegram_file_id=None
    )
    assert msg.direction == ChatDirection.from_client
    assert msg.content == "Привет"
    assert msg.file_kind is None
    assert msg.read_at is None  # непрочитанное

    photo_msg = await service.save_incoming_message(
        db_session, client, content="эскиз", telegram_file_id="fid_1"
    )
    assert photo_msg.file_kind == "photo"


@pytest.mark.asyncio
async def test_mark_thread_read(db_session):
    client = await make_client(db_session, telegram_id=43)
    await service.save_incoming_message(db_session, client, content="1", telegram_file_id=None)
    await service.save_incoming_message(db_session, client, content="2", telegram_file_id=None)

    marked = await service.mark_thread_read(db_session, client.id)
    assert marked == 2
    # повторно — уже нечего помечать
    assert await service.mark_thread_read(db_session, client.id) == 0


@pytest.mark.asyncio
async def test_is_new_chat(db_session):
    client = await make_client(db_session, telegram_id=106)

    # сообщений ещё нет — это новое обращение
    assert await service.is_new_chat(db_session, client.id) is True

    await service.save_incoming_message(db_session, client, content="1", telegram_file_id=None)
    # чат уже был — уведомлять больше не нужно
    assert await service.is_new_chat(db_session, client.id) is False


@pytest.mark.asyncio
async def test_previews_for_media_kinds(db_session):
    voice = await make_client(db_session, telegram_id=107)
    await service.save_incoming_message(
        db_session, voice, content=None, telegram_file_id="fid_v", file_kind="voice"
    )
    sticker = await make_client(db_session, telegram_id=108)
    await service.save_incoming_message(db_session, sticker, content="😀", telegram_file_id=None)
    video = await make_client(db_session, telegram_id=109)
    await service.save_incoming_message(
        db_session, video, content=None, telegram_file_id="fid_vid", file_kind="video"
    )
    doc = await make_client(db_session, telegram_id=110)
    await service.save_incoming_message(
        db_session, doc, content="sketch.pdf", telegram_file_id="fid_doc", file_kind="document"
    )

    threads = {t["client_id"]: t for t in await service.list_threads(db_session)}
    assert threads[voice.id]["last_message_preview"] == "🎙 Голосовое"
    assert threads[sticker.id]["last_message_preview"] == "😀"
    assert threads[video.id]["last_message_preview"] == "📎 Видео"
    assert threads[doc.id]["last_message_preview"] == "sketch.pdf"


def test_file_token_roundtrip():
    token = service.make_file_token(42)
    assert service.verify_file_token(42, token)

    # чужой id, испорченная подпись, просрочка, мусор
    assert not service.verify_file_token(43, token)
    tampered = token[:-1] + ("0" if token[-1] != "0" else "1")
    assert not service.verify_file_token(42, tampered)
    expired = service.make_file_token(42, now=int(time.time()) - service.FILE_TOKEN_TTL - 5)
    assert not service.verify_file_token(42, expired)
    assert not service.verify_file_token(42, "мусор")
    assert not service.verify_file_token(42, None)


@pytest.mark.asyncio
async def test_transcode_voice_to_mp3():
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg недоступен в образе")

    # валидный ogg/opus синтезируем самим ffmpeg
    ogg = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
            "-c:a", "libopus", "-f", "ogg", "pipe:1",
        ],
        capture_output=True,
        check=True,
    ).stdout

    mp3 = await service.transcode_voice_to_mp3(ogg)
    assert mp3 is not None
    # ID3-тег или MPEG-фрейм (0xFF Expose)
    assert mp3[:3] == b"ID3" or mp3[0] == 0xFF

    # битые байты — не перекодируем, прокси отдаст оригинал
    assert await service.transcode_voice_to_mp3(b"garbage") is None


@pytest.mark.asyncio
async def test_resolve_owner_telegram_id_prefers_business(db_session):
    user = User(telegram_id=99999, first_name="Owner", role=UserRole.owner)
    db_session.add(user)
    await db_session.flush()
    db_session.add(
        Business(owner_id=user.id, owner_telegram_id=228553615, name="Studio")
    )
    await db_session.flush()

    assert await service.resolve_owner_telegram_id(db_session) == 228553615


@pytest.mark.asyncio
async def test_resolve_owner_telegram_id_fallback_to_user(db_session):
    user = User(telegram_id=555000, first_name="Owner2", role=UserRole.owner)
    db_session.add(user)
    await db_session.flush()
    db_session.add(Business(owner_id=user.id, owner_telegram_id=None, name="Studio2"))
    await db_session.flush()

    assert await service.resolve_owner_telegram_id(db_session) == 555000


@pytest.mark.asyncio
async def test_list_threads_orders_and_counts(db_session):
    client_a = await make_client(db_session, telegram_id=101, full_name="Аня")
    client_b = await make_client(db_session, telegram_id=102, full_name="Борис")

    # Аня: старое входящее непрочитанное
    await service.save_incoming_message(db_session, client_a, content="привет", telegram_file_id=None)
    # Борис: входящее + наш ответ (ответ не считаем непрочитанным)
    await service.save_incoming_message(db_session, client_b, content="вопрос", telegram_file_id=None)
    reply = ChatMessage(
        client_id=client_b.id, direction=ChatDirection.from_master, content="ответ"
    )
    db_session.add(reply)
    await db_session.commit()

    threads = await service.list_threads(db_session)
    # последний по времени — Борис (его сообщения позже)
    assert [t["client_id"] for t in threads] == [client_b.id, client_a.id]
    boris, anna = threads
    assert boris["unread_count"] == 1  # ответ мастера не увеличивает
    assert boris["last_message_preview"] == "ответ"
    assert boris["last_direction"] == "from_master"
    assert anna["unread_count"] == 1
    assert anna["last_message_preview"] == "привет"


@pytest.mark.asyncio
async def test_list_messages_after_id(db_session):
    client = await make_client(db_session, telegram_id=104)
    m1 = await service.save_incoming_message(db_session, client, content="1", telegram_file_id=None)
    await service.save_incoming_message(db_session, client, content="2", telegram_file_id=None)

    all_msgs = await service.list_messages(db_session, client.id)
    assert len(all_msgs) == 2

    newer = await service.list_messages(db_session, client.id, after_id=m1.id)
    assert len(newer) == 1
    assert newer[0].content == "2"


# ---------------------------------------------------------------------------
# Надёжность (P1): дедуп входящих, ретраи отправки в Telegram,
# компенсация «фантомов» при сбое коммита, ретрай зеркала AI-ответа
# ---------------------------------------------------------------------------


class _FakeBotSession:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


class _FakeBot:
    """Подмена _new_bot: без сети, исходы send_message заданы списком."""

    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.calls: list[tuple[int, str]] = []
        self.deleted: list[tuple[int, int]] = []
        self.delete_outcome: Exception | None = None
        self.session = _FakeBotSession()

    async def send_message(self, chat_id, text, reply_markup=None):
        self.calls.append((chat_id, text))
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return SimpleNamespace(message_id=outcome)

    async def delete_message(self, chat_id, message_id):
        self.deleted.append((chat_id, message_id))
        if self.delete_outcome is not None:
            raise self.delete_outcome


@pytest.mark.asyncio
async def test_incoming_message_dedup_by_telegram_id(db_session):
    """Повторная доставка того же update (reconnect Telegram) — не дубль в БД."""
    client = await make_client(db_session, telegram_id=123)
    # client_id захватываем сразу: при дубликате save_incoming_message делает
    # rollback, после которого client.id в async упал бы с MissingGreenlet.
    client_id = client.id

    first = await service.save_incoming_message(
        db_session, client, content="привет", telegram_file_id=None,
        telegram_message_id=555,
    )
    assert first is not None
    assert await service.message_exists(db_session, client_id, 555) is True
    assert await service.message_exists(db_session, client_id, 556) is False

    # тот же telegram_message_id — дубликат доставки, None
    dup = await service.save_incoming_message(
        db_session, client, content="привет", telegram_file_id=None,
        telegram_message_id=555,
    )
    assert dup is None
    # rollback в save_incoming_message expire-ит client — как в боевом хендлере
    # (новый update = новая сессия), перчитываем клиента перед следующим вызовом
    client = await db_session.get(Client, client_id)
    # другое сообщение — ок
    other = await service.save_incoming_message(
        db_session, client, content="второе", telegram_file_id=None,
        telegram_message_id=556,
    )
    assert other is not None

    rows = (
        await db_session.execute(
            select(ChatMessage).where(ChatMessage.client_id == client_id)
        )
    ).scalars().all()
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_send_text_retries_retry_after(monkeypatch):
    """RetryAfter (rate limit) — ретрай; успех на 3-й попытке, бот закрыт."""
    from aiogram.exceptions import TelegramRetryAfter
    from aiogram.methods import SendMessage

    monkeypatch.setattr(service, "TG_SEND_BACKOFF_SECONDS", 0)
    method = SendMessage(chat_id=1, text="x")
    bot = _FakeBot(
        [TelegramRetryAfter(method, "retry", 0), TelegramRetryAfter(method, "retry", 0), 555]
    )
    monkeypatch.setattr(service, "_new_bot", lambda: bot)

    assert await service.send_text_to_telegram(42, "привет") == 555
    assert len(bot.calls) == 3
    assert bot.session.closed  # aiohttp-коннекторы не текут


@pytest.mark.asyncio
async def test_send_text_retries_server_error(monkeypatch):
    """5xx Telegram — ретрай, затем успех."""
    from aiogram.exceptions import TelegramServerError
    from aiogram.methods import SendMessage

    monkeypatch.setattr(service, "TG_SEND_BACKOFF_SECONDS", 0)
    bot = _FakeBot([TelegramServerError(SendMessage(chat_id=1, text="x"), "boom"), 777])
    monkeypatch.setattr(service, "_new_bot", lambda: bot)

    assert await service.send_text_to_telegram(42, "привет") == 777
    assert len(bot.calls) == 2


@pytest.mark.asyncio
async def test_send_text_non_retryable_4xx_raises_immediately(monkeypatch):
    """4xx (чат не найден, бот заблокирован) — без ретраев, сразу TelegramSendError."""
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.methods import SendMessage

    bot = _FakeBot([TelegramBadRequest(SendMessage(chat_id=1, text="x"), "chat not found")])
    monkeypatch.setattr(service, "_new_bot", lambda: bot)

    with pytest.raises(service.TelegramSendError):
        await service.send_text_to_telegram(42, "привет")
    assert len(bot.calls) == 1


@pytest.mark.asyncio
async def test_delete_message_from_telegram_is_best_effort(monkeypatch):
    """Удаление «фантома» после сбоя коммита: вызов есть, сбой не бросаем."""
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.methods import DeleteMessage

    bot = _FakeBot([])
    monkeypatch.setattr(service, "_new_bot", lambda: bot)

    await service.delete_message_from_telegram(42, 900)
    assert bot.deleted == [(42, 900)]

    bot.delete_outcome = TelegramBadRequest(
        DeleteMessage(chat_id=42, message_id=901), "message to delete not found"
    )
    await service.delete_message_from_telegram(42, 901)  # не бросает
    assert bot.deleted == [(42, 900), (42, 901)]


@pytest.mark.asyncio
async def test_send_text_message_commit_failure_deletes_phantom(db_session, monkeypatch):
    """Сбой коммита после отправки: «фантом» удалён из Telegram, транзакция откачена."""
    client = await make_client(db_session, telegram_id=124)

    async def fake_send(telegram_id, text):
        return 4242

    deleted = []

    async def fake_delete(telegram_id, message_id):
        deleted.append((telegram_id, message_id))

    async def fail_commit():
        raise RuntimeError("db down")

    monkeypatch.setattr(service, "send_text_to_telegram", fake_send)
    monkeypatch.setattr(service, "delete_message_from_telegram", fake_delete)
    monkeypatch.setattr(db_session, "commit", fail_commit)

    with pytest.raises(RuntimeError):
        await service.send_text_message(db_session, client, "привет")
    assert deleted == [(124, 4242)]

    rows = (await db_session.execute(select(ChatMessage))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_save_ai_message_retries_in_fresh_session(db_session, monkeypatch):
    """Сбой коммита зеркала AI-ответа → повтор в новой транзакции (ответ не теряем)."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    client = await make_client(db_session, telegram_id=125)

    async def fail_commit():
        raise RuntimeError("db down")

    monkeypatch.setattr(db_session, "commit", fail_commit)
    monkeypatch.setattr(
        "tg_studio.db.session.async_session_factory",
        async_sessionmaker(db_session.bind, expire_on_commit=False),
    )

    msg = await service.save_ai_message(
        db_session, client, "ответ агента", telegram_message_id=900
    )
    assert msg.content == "ответ агента"
    assert msg.telegram_message_id == 900

    # в БД ровно одна запись — старая транзакция откачена
    rows = (await db_session.execute(select(ChatMessage))).scalars().all()
    assert len(rows) == 1
    assert rows[0].direction == ChatDirection.from_ai
