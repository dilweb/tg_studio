"""Интеграционные тесты /api/admin/chats.

Telegram-функции подменяются на уровне модуля service — настоящий бот
в тестах не должен никуда стучаться.
"""

import pytest

from tg_studio.db.models import (
    ChatAssignmentStatus,
    ChatDirection,
    ChatMessage,
    Master,
)
from tg_studio.modules.chat import service

from ..conftest import make_business, make_client, make_init_data_headers, make_master

OWNER_HEADERS = make_init_data_headers(99999)


async def seed_message(session, client, *, direction, content=None, file_id=None, file_kind=None):
    if file_kind is None:
        file_kind = "photo" if file_id else None
    msg = ChatMessage(
        client_id=client.id,
        direction=direction,
        content=content,
        telegram_file_id=file_id,
        file_kind=file_kind,
    )
    session.add(msg)
    await session.commit()
    return msg


@pytest.fixture
async def business(db_session):
    return await make_business(db_session, owner_telegram_id=99999)


@pytest.mark.asyncio
async def test_list_chats_empty(api_client, business):
    resp = await api_client.get("/api/admin/chats", headers=OWNER_HEADERS)
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_chats_threads(api_client, db_session, business):
    client = await make_client(db_session, telegram_id=111, full_name="Клиент Один")
    await seed_message(db_session, client, direction=ChatDirection.from_client, content="здравствуйте")

    resp = await api_client.get("/api/admin/chats", headers=OWNER_HEADERS)
    assert resp.status_code == 200
    threads = resp.json()
    assert len(threads) == 1
    t = threads[0]
    assert t["client_id"] == client.id
    assert t["full_name"] == "Клиент Один"
    assert t["unread_count"] == 1
    assert t["last_message_preview"] == "здравствуйте"
    assert t["last_direction"] == "from_client"


@pytest.mark.asyncio
async def test_messages_history_and_after_id(api_client, db_session, business):
    client = await make_client(db_session, telegram_id=112)
    m1 = await seed_message(db_session, client, direction=ChatDirection.from_client, content="1")
    await seed_message(db_session, client, direction=ChatDirection.from_master, content="2")

    resp = await api_client.get(f"/api/admin/chats/{client.id}/messages", headers=OWNER_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert body[0]["content"] == "1"
    assert body[1]["direction"] == "from_master"

    resp = await api_client.get(
        f"/api/admin/chats/{client.id}/messages",
        params={"after_id": m1.id},
        headers=OWNER_HEADERS,
    )
    body = resp.json()
    assert len(body) == 1
    assert body[0]["content"] == "2"


@pytest.mark.asyncio
async def test_messages_unknown_client(api_client, business):
    resp = await api_client.get("/api/admin/chats/9999/messages", headers=OWNER_HEADERS)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_post_text_message(api_client, db_session, business, monkeypatch):
    client = await make_client(db_session, telegram_id=113)

    async def fake_send(telegram_id, text):
        assert telegram_id == 113
        return 777

    monkeypatch.setattr(service, "send_text_to_telegram", fake_send)

    resp = await api_client.post(
        f"/api/admin/chats/{client.id}/messages",
        json={"content": "Проверка"},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["direction"] == "from_master"
    assert body["content"] == "Проверка"
    assert body["file_url"] is None  # у текста файла нет

    row = await db_session.get(ChatMessage, body["id"])
    assert row.telegram_message_id == 777


@pytest.mark.asyncio
async def test_role_prefix_in_telegram_only(api_client, db_session, business, monkeypatch):
    """В Telegram ответ уходит с меткой роли, в chat_messages — чистым текстом."""
    from ..conftest import make_master
    from tg_studio.modules.chat import assignments as assignments_mod

    sent: list[str] = []

    async def fake_send(telegram_id, text):
        sent.append(text)
        # Реальный Telegram не переиспользует message_id в одном чате, а у нас
        # unique-индекс (client_id, telegram_message_id) — фейк возвращает разные id.
        return 778 + len(sent)

    monkeypatch.setattr(service, "send_text_to_telegram", fake_send)

    master = await make_master(db_session, business.id, full_name="Дилявер Мастер", telegram_id=7790)
    client = await make_client(db_session, telegram_id=115)
    await assignments_mod.create_assignment(
        db_session, business_id=business.id, client_id=client.id,
        master_id=master.id, order_summary=None,
    )

    # Владелец → «Студия», мастер → «Мастер <имя>»
    await api_client.post(
        f"/api/admin/chats/{client.id}/messages",
        json={"content": "привет"},
        headers=OWNER_HEADERS,
    )
    master_headers = make_init_data_headers(7790)
    await api_client.post(
        f"/api/admin/chats/{client.id}/messages",
        json={"content": "из мастерской"},
        headers=master_headers,
    )
    assert sent == ["🏪 Студия: привет", "🎨 Мастер Дилявер: из мастерской"]

    # В БД — без префиксов (миниапп показывает роль сам)
    rows = (
        (await db_session.execute(__import__("sqlalchemy").select(ChatMessage)))
        .scalars().all()
    )
    assert sorted(m.content for m in rows) == ["из мастерской", "привет"]


@pytest.mark.asyncio
async def test_post_text_telegram_error_not_persisted(api_client, db_session, business, monkeypatch):
    client = await make_client(db_session, telegram_id=114)

    async def failing_send(telegram_id, text):
        raise service.TelegramSendError("blocked")

    monkeypatch.setattr(service, "send_text_to_telegram", failing_send)

    resp = await api_client.post(
        f"/api/admin/chats/{client.id}/messages",
        json={"content": "не дойдёт"},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 502

    rows = (
        (await db_session.execute(__import__("sqlalchemy").select(ChatMessage)))
        .scalars()
        .all()
    )
    assert rows == []


@pytest.mark.asyncio
async def test_post_photo(api_client, db_session, business, monkeypatch):
    client = await make_client(db_session, telegram_id=115)

    async def fake_send_photo(telegram_id, photo, caption):
        assert telegram_id == 115
        assert photo == b"jpg-bytes"
        return 888, "fileid_out"

    monkeypatch.setattr(service, "send_photo_to_telegram", fake_send_photo)

    resp = await api_client.post(
        f"/api/admin/chats/{client.id}/photo",
        files={"file": ("a.jpg", b"jpg-bytes", "image/jpeg")},
        data={"caption": "смотри эскиз"},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["has_photo"] is True
    assert body["file_kind"] == "photo"
    assert body["content"] == "смотри эскиз"
    assert body["file_url"].startswith(f"/api/admin/chats/files/{body['id']}?t=")

    row = await db_session.get(ChatMessage, body["id"])
    assert row.telegram_file_id == "fileid_out"


@pytest.mark.asyncio
async def test_post_photo_rejects_non_image(api_client, db_session, business):
    client = await make_client(db_session, telegram_id=116)
    resp = await api_client.post(
        f"/api/admin/chats/{client.id}/photo",
        files={"file": ("a.pdf", b"%PDF", "application/pdf")},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_content_validation(api_client, db_session, business):
    client = await make_client(db_session, telegram_id=117)
    resp = await api_client.post(
        f"/api/admin/chats/{client.id}/messages",
        json={"content": ""},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 422

    resp = await api_client.post(
        f"/api/admin/chats/{client.id}/messages",
        json={"content": "x" * 4001},
        headers=OWNER_HEADERS,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_mark_read(api_client, db_session, business):
    client = await make_client(db_session, telegram_id=118)
    await seed_message(db_session, client, direction=ChatDirection.from_client, content="привет")

    resp = await api_client.post(f"/api/admin/chats/{client.id}/read", headers=OWNER_HEADERS)
    assert resp.status_code == 200
    assert resp.json() == {"marked": 1}

    threads = (
        await api_client.get("/api/admin/chats", headers=OWNER_HEADERS)
    ).json()
    assert threads[0]["unread_count"] == 0

    # повторно помечать нечего
    resp = await api_client.post(f"/api/admin/chats/{client.id}/read", headers=OWNER_HEADERS)
    assert resp.json() == {"marked": 0}


@pytest.mark.asyncio
async def test_file_proxy(api_client, db_session, business, monkeypatch):
    client = await make_client(db_session, telegram_id=119)
    msg = await seed_message(
        db_session, client, direction=ChatDirection.from_client, file_id="fid_in"
    )

    async def fake_download(file_id):
        assert file_id == "fid_in"
        return b"imgbytes"

    monkeypatch.setattr(service, "download_photo", fake_download)

    resp = await api_client.get(f"/api/admin/chats/files/{msg.id}", headers=OWNER_HEADERS)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert resp.content == b"imgbytes"

    # голосовое отдаётся как audio/ogg
    async def fake_download_voice(file_id):
        assert file_id == "fid_voice"
        return b"oggbytes"

    monkeypatch.setattr(service, "download_photo", fake_download_voice)

    voice_msg = await seed_message(
        db_session, client, direction=ChatDirection.from_client,
        file_id="fid_voice", file_kind="voice",
    )
    resp = await api_client.get(f"/api/admin/chats/files/{voice_msg.id}", headers=OWNER_HEADERS)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "audio/ogg"
    assert resp.content == b"oggbytes"

    # перекодировка удалась (ffmpeg на месте) — отдаём mp3
    async def fake_voice_mp3(data):
        return b"mp3-bytes"

    monkeypatch.setattr(service, "transcode_voice_to_mp3", fake_voice_mp3)
    resp = await api_client.get(f"/api/admin/chats/files/{voice_msg.id}", headers=OWNER_HEADERS)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "audio/mpeg"
    assert resp.content == b"mp3-bytes"

    # видео отдаётся как video/mp4
    async def fake_download_video(file_id):
        assert file_id == "fid_video"
        return b"mp4bytes"

    monkeypatch.setattr(service, "download_photo", fake_download_video)

    video_msg = await seed_message(
        db_session, client, direction=ChatDirection.from_client,
        file_id="fid_video", file_kind="video",
    )
    resp = await api_client.get(f"/api/admin/chats/files/{video_msg.id}", headers=OWNER_HEADERS)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "video/mp4"
    assert resp.content == b"mp4bytes"

    # документ: attachment с именем файла — браузер скачает его как есть
    async def fake_download_doc(file_id):
        assert file_id == "fid_doc"
        return b"%PDF-1.4 bytes"

    monkeypatch.setattr(service, "download_photo", fake_download_doc)

    doc_msg = await seed_message(
        db_session, client, direction=ChatDirection.from_client,
        content="sketch.pdf", file_id="fid_doc", file_kind="document",
    )
    resp = await api_client.get(f"/api/admin/chats/files/{doc_msg.id}", headers=OWNER_HEADERS)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/octet-stream"
    assert resp.headers["content-disposition"] == "attachment; filename*=UTF-8''sketch.pdf"

    # сообщение без фото → 404
    text_msg = await seed_message(
        db_session, client, direction=ChatDirection.from_client, content="текст"
    )
    resp = await api_client.get(f"/api/admin/chats/files/{text_msg.id}", headers=OWNER_HEADERS)
    assert resp.status_code == 404

    # неизвестное сообщение → 404
    resp = await api_client.get("/api/admin/chats/files/99999", headers=OWNER_HEADERS)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_file_proxy_signed_token(api_client, db_session, monkeypatch):
    """Прямая ссылка ?t= отдаёт файл без заголовков: так документ скачивает
    обычный браузер (на ноуте) и системный браузер с телефона."""

    client = await make_client(db_session, telegram_id=120)
    msg = await seed_message(
        db_session, client, direction=ChatDirection.from_client, file_id="fid_tok"
    )

    async def fake_download(file_id):
        return b"tokbytes"

    monkeypatch.setattr(service, "download_photo", fake_download)

    resp = await api_client.get(
        f"/api/admin/chats/files/{msg.id}?t={service.make_file_token(msg.id)}"
    )
    assert resp.status_code == 200
    assert resp.content == b"tokbytes"

    # битый токен и вовсе без токена — без авторизации → 401
    resp = await api_client.get(f"/api/admin/chats/files/{msg.id}?t=1:bad")
    assert resp.status_code == 401
    resp = await api_client.get(f"/api/admin/chats/files/{msg.id}")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_from_ai_message_in_history(api_client, db_session, business):
    """Ответ AI-агента виден в истории чата с direction=from_ai."""
    client = await make_client(db_session, telegram_id=130)
    await seed_message(db_session, client, direction=ChatDirection.from_client, content="хочу тату")
    await seed_message(db_session, client, direction=ChatDirection.from_ai, content="Расскажите про идею")

    resp = await api_client.get(
        f"/api/admin/chats/{client.id}/messages", headers=OWNER_HEADERS
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body[-1]["direction"] == "from_ai"
    assert body[-1]["content"] == "Расскажите про идею"


@pytest.mark.asyncio
async def test_thread_assigned_master_name(api_client, db_session, business):
    """Открытое закрепление чата за мастером отдаётся в треде."""
    from tg_studio.modules.chat import assignments as assignments_mod

    client = await make_client(db_session, telegram_id=131)
    master = Master(
        business_id=business.id, full_name="Елена Лизунова", is_active=True
    )
    db_session.add(master)
    await db_session.flush()
    await seed_message(db_session, client, direction=ChatDirection.from_client, content="привет")
    await assignments_mod.create_assignment(
        db_session, business_id=business.id, client_id=client.id,
        master_id=master.id, order_summary="минимализм",
    )

    threads = (
        await api_client.get("/api/admin/chats", headers=OWNER_HEADERS)
    ).json()
    t = next(t for t in threads if t["client_id"] == client.id)
    assert t["assigned_master_name"] == "Елена Лизунова"


# ---------------------------------------------------------------------------
# Фильтрация чатов по роли: мастер видит только закреплённые за ним
# ---------------------------------------------------------------------------

async def test_master_sees_only_assigned_chats(api_client, db_session, business):
    from tg_studio.modules.chat import assignments as assignments_mod

    master = await make_master(db_session, business.id, telegram_id=7777)
    mine = await make_client(db_session, telegram_id=101, full_name="Мой клиент")
    other = await make_client(db_session, telegram_id=102, full_name="Чужой клиент")
    await seed_message(db_session, mine, direction=ChatDirection.from_client, content="мой")
    await seed_message(db_session, other, direction=ChatDirection.from_client, content="чужой")
    await assignments_mod.create_assignment(
        db_session, business_id=business.id, client_id=mine.id,
        master_id=master.id, order_summary="минимализм",
    )

    headers = make_init_data_headers(7777)
    resp = await api_client.get("/api/admin/chats", headers=headers)
    assert resp.status_code == 200
    threads = resp.json()
    assert [t["client_id"] for t in threads] == [mine.id]

    # Владелец по-прежнему видит оба
    owner_threads = (
        await api_client.get("/api/admin/chats", headers=OWNER_HEADERS)
    ).json()
    assert {t["client_id"] for t in owner_threads} == {mine.id, other.id}


async def test_master_cannot_read_unassigned_chat(api_client, db_session, business):
    from tg_studio.modules.chat import assignments as assignments_mod

    master = await make_master(db_session, business.id, telegram_id=7779)
    other_master = await make_master(db_session, business.id, full_name="Другой", telegram_id=7780)
    client = await make_client(db_session, telegram_id=104)
    await seed_message(db_session, client, direction=ChatDirection.from_client, content="секрет")
    await assignments_mod.create_assignment(
        db_session, business_id=business.id, client_id=client.id,
        master_id=other_master.id, order_summary=None,
    )

    headers = make_init_data_headers(7779)
    resp = await api_client.get(f"/api/admin/chats/{client.id}/messages", headers=headers)
    assert resp.status_code == 404
    resp = await api_client.post(
        f"/api/admin/chats/{client.id}/messages", headers=headers, json={"content": "привет"}
    )
    assert resp.status_code == 404


async def test_master_release_returns_chat_to_agent(api_client, db_session, business):
    from tg_studio.modules.chat import assignments as assignments_mod

    master = await make_master(db_session, business.id, telegram_id=7781)
    client = await make_client(db_session, telegram_id=105)
    await seed_message(db_session, client, direction=ChatDirection.from_client, content="?")
    await assignments_mod.create_assignment(
        db_session, business_id=business.id, client_id=client.id,
        master_id=master.id, order_summary=None,
    )

    headers = make_init_data_headers(7781)
    resp = await api_client.post(f"/api/admin/chats/{client.id}/release", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == {"released": True}

    # Отвязанный чат мастер продолжает видеть — но уже без бейджа закрепления
    threads = (await api_client.get("/api/admin/chats", headers=headers)).json()
    [thread] = threads
    assert thread["client_id"] == client.id
    assert thread["assigned_master_name"] is None
    # Историю и отправку мастер тоже не теряет
    resp = await api_client.get(f"/api/admin/chats/{client.id}/messages", headers=headers)
    assert resp.status_code == 200

    # Повторный release — закрепления уже нет, но без ошибки
    resp = await api_client.post(f"/api/admin/chats/{client.id}/release", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == {"released": False}


async def test_master_closed_assignment_chat_stays_visible(api_client, db_session, business):
    """Закрытие работы отвязывает чат, но из списка мастера он не пропадает."""
    from tg_studio.modules.chat import assignments as assignments_mod

    master = await make_master(db_session, business.id, telegram_id=7778)
    client = await make_client(db_session, telegram_id=103)
    await seed_message(db_session, client, direction=ChatDirection.from_client, content="?")
    assignment = await assignments_mod.create_assignment(
        db_session, business_id=business.id, client_id=client.id,
        master_id=master.id, order_summary=None,
    )
    await assignments_mod.close_assignment(db_session, assignment)

    headers = make_init_data_headers(7778)
    threads = (await api_client.get("/api/admin/chats", headers=headers)).json()
    [thread] = threads
    assert thread["client_id"] == client.id
    assert thread["assigned_master_name"] is None
    resp = await api_client.get(f"/api/admin/chats/{client.id}/messages", headers=headers)
    assert resp.status_code == 200


async def test_master_cannot_release_foreign_chat(api_client, db_session, business):
    from tg_studio.modules.chat import assignments as assignments_mod

    master = await make_master(db_session, business.id, telegram_id=7782)
    other_master = await make_master(db_session, business.id, full_name="Другой", telegram_id=7783)
    client = await make_client(db_session, telegram_id=106)
    await seed_message(db_session, client, direction=ChatDirection.from_client, content="?")
    assignment = await assignments_mod.create_assignment(
        db_session, business_id=business.id, client_id=client.id,
        master_id=other_master.id, order_summary=None,
    )

    headers = make_init_data_headers(7782)
    resp = await api_client.post(f"/api/admin/chats/{client.id}/release", headers=headers)
    assert resp.status_code == 404
    assert assignment.status == ChatAssignmentStatus.open


async def test_owner_release_unpins_any_chat(api_client, db_session, business):
    from tg_studio.modules.chat import assignments as assignments_mod

    other_master = await make_master(db_session, business.id, full_name="Другой", telegram_id=7784)
    client = await make_client(db_session, telegram_id=107)
    await seed_message(db_session, client, direction=ChatDirection.from_client, content="?")
    assignment = await assignments_mod.create_assignment(
        db_session, business_id=business.id, client_id=client.id,
        master_id=other_master.id, order_summary=None,
    )

    resp = await api_client.post(
        f"/api/admin/chats/{client.id}/release", headers=OWNER_HEADERS
    )
    assert resp.status_code == 200
    assert resp.json() == {"released": True}
    assert assignment.status == ChatAssignmentStatus.closed


@pytest.mark.asyncio
async def test_message_created_at_has_utc_offset(api_client, db_session, business):
    """created_at отдаётся со смещением UTC (aware datetime из БД).

    Регрессия: колонки без таймзоны хранят UTC wall-clock, но раньше API отдавал
    наивный ISO — фронтенд парсил его как локальное время и в Алматы показывал
    время на 5 часов раньше реального.
    """
    client = await make_client(db_session, telegram_id=114)
    await seed_message(db_session, client, direction=ChatDirection.from_client, content="привет")

    from datetime import datetime

    resp = await api_client.get(f"/api/admin/chats/{client.id}/messages", headers=OWNER_HEADERS)
    assert resp.status_code == 200
    created_at = resp.json()[0]["created_at"]
    assert datetime.fromisoformat(created_at).tzinfo is not None  # UTC-aware ("Z" или "+00:00")

    resp = await api_client.get("/api/admin/chats", headers=OWNER_HEADERS)
    assert resp.status_code == 200
    last_at = resp.json()[0]["last_message_at"]
    assert datetime.fromisoformat(last_at).tzinfo is not None


@pytest.mark.asyncio
async def test_file_proxy_serves_stored_copy(
    api_client, db_session, business, monkeypatch, tmp_path
):
    """Копия в своём хранилище отдаётся без обращения к Telegram."""
    from tg_studio.config import settings
    from tg_studio.modules.chat import files as chat_files

    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    client = await make_client(db_session, telegram_id=132)
    stored = chat_files.save_message_file(b"stored-bytes", "image/jpeg")
    msg = await seed_message(
        db_session, client, direction=ChatDirection.from_client, file_id="fid_stored"
    )
    msg.stored_path = stored
    await db_session.commit()

    async def fail_download(file_id):
        raise AssertionError("Telegram не должен вызываться при наличии копии")

    monkeypatch.setattr(service, "download_photo", fail_download)

    resp = await api_client.get(f"/api/admin/chats/files/{msg.id}", headers=OWNER_HEADERS)
    assert resp.status_code == 200
    assert resp.content == b"stored-bytes"
    assert resp.headers["content-type"] == "image/jpeg"


@pytest.mark.asyncio
async def test_file_proxy_falls_back_to_telegram(api_client, db_session, business, monkeypatch):
    """Копии нет в хранилище → fallback: скачиваем из Telegram по file_id."""
    client = await make_client(db_session, telegram_id=133)
    msg = await seed_message(
        db_session, client, direction=ChatDirection.from_client, file_id="fid_fb"
    )
    msg.stored_path = "chat/messages/missing.jpg"
    await db_session.commit()

    async def fake_download(file_id):
        assert file_id == "fid_fb"
        return b"tg-bytes"

    monkeypatch.setattr(service, "download_photo", fake_download)

    resp = await api_client.get(f"/api/admin/chats/files/{msg.id}", headers=OWNER_HEADERS)
    assert resp.status_code == 200
    assert resp.content == b"tg-bytes"
