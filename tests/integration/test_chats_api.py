"""Интеграционные тесты /api/admin/chats.

Telegram-функции подменяются на уровне модуля service — настоящий бот
в тестах не должен никуда стучаться.
"""

import pytest

from tg_studio.db.models import ChatDirection, ChatMessage
from tg_studio.modules.chat import service

from ..conftest import make_business, make_client

OWNER_HEADERS = {"X-Debug-User-Id": "99999"}


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
    from tg_studio.config import settings

    monkeypatch.setattr(settings, "debug", False)  # как в проде: без кредов только токен

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
