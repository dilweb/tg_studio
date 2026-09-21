"""Чаты с клиентами: admin-API для миниаппа."""

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response

from tg_studio.api.admin_deps import MasterBusinessDep
from tg_studio.api.deps import SessionDep
from tg_studio.modules.chat import service
from tg_studio.modules.chat.schemas import ChatMessageCreate, ChatMessageOut, ChatThreadOut

router = APIRouter(prefix="/admin/chats", tags=["admin • chats"])

MAX_PHOTO_BYTES = service.MAX_PHOTO_BYTES


async def _get_client_or_404(session, client_id: int):
    client = await service.get_client(session, client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Чат не найден")
    return client


@router.get("", response_model=list[ChatThreadOut])
async def list_chats(session: SessionDep, _business: MasterBusinessDep) -> list[ChatThreadOut]:
    # Клиенты не привязаны к бизнесу (Client без business_id) — один бизнес,
    # auth-зависимость здесь только проверяет доступ owner/master
    return [ChatThreadOut(**t) for t in await service.list_threads(session)]


@router.get("/files/{message_id}")
async def get_message_file(
    message_id: int, session: SessionDep, _business: MasterBusinessDep
) -> Response:
    msg = await service.get_message_file(session, message_id)
    if msg is None:
        raise HTTPException(status_code=404, detail="Файл не найден")
    try:
        data = await service.download_photo(msg.telegram_file_id)
    except service.TelegramSendError:
        raise HTTPException(status_code=502, detail="Не удалось скачать файл из Telegram") from None
    return Response(
        content=data,
        media_type=service.FILE_MEDIA_TYPES.get(msg.file_kind or "", "application/octet-stream"),
        headers={"Cache-Control": "private, max-age=300"},
    )


@router.get("/{client_id}/messages", response_model=list[ChatMessageOut])
async def get_messages(
    client_id: int,
    session: SessionDep,
    _business: MasterBusinessDep,
    after_id: int | None = Query(default=None),
) -> list[ChatMessageOut]:
    await _get_client_or_404(session, client_id)
    msgs = await service.list_messages(session, client_id, after_id=after_id)
    return [_to_out(m) for m in msgs]


@router.post("/{client_id}/messages", response_model=ChatMessageOut, status_code=201)
async def post_message(
    client_id: int,
    body: ChatMessageCreate,
    session: SessionDep,
    _business: MasterBusinessDep,
) -> ChatMessageOut:
    client = await _get_client_or_404(session, client_id)
    try:
        msg = await service.send_text_message(session, client, body.content)
    except service.TelegramSendError as exc:
        raise HTTPException(status_code=502, detail=f"Telegram не принял сообщение: {exc}") from exc
    return _to_out(msg)


@router.post("/{client_id}/photo", response_model=ChatMessageOut, status_code=201)
async def post_photo(
    client_id: int,
    session: SessionDep,
    _business: MasterBusinessDep,
    file: UploadFile = File(...),
    caption: str | None = Form(default=None),
) -> ChatMessageOut:
    client = await _get_client_or_404(session, client_id)
    if file.content_type and not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Только изображения")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Пустой файл")
    if len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(status_code=400, detail="Файл больше 10 МБ")
    try:
        msg = await service.send_photo_message(session, client, data, caption)
    except service.TelegramSendError as exc:
        raise HTTPException(status_code=502, detail=f"Telegram не принял фото: {exc}") from exc
    return _to_out(msg)


@router.post("/{client_id}/read")
async def mark_read(
    client_id: int, session: SessionDep, _business: MasterBusinessDep
) -> dict:
    await _get_client_or_404(session, client_id)
    marked = await service.mark_thread_read(session, client_id)
    return {"marked": marked}


def _to_out(m) -> ChatMessageOut:
    return ChatMessageOut(
        id=m.id,
        client_id=m.client_id,
        direction=m.direction.value,
        content=m.content,
        file_kind=m.file_kind,
        has_photo=bool(m.telegram_file_id),
        created_at=m.created_at,
    )
