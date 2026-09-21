"""Юнит-тесты chat service: upsert клиента, сохранение, mark_read, owner."""

import pytest
from sqlalchemy import select

from tg_studio.db.models import Business, ChatDirection, ChatMessage, User, UserRole
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
