from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ChatMessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=4000)  # лимит Telegram — 4096


class ChatMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    client_id: int
    direction: str
    content: str | None
    file_kind: str | None
    has_photo: bool
    # Подписанная прямая ссылка на файл (?t=<token>): документы скачиваются
    # по ней браузером — blob-ссылки вне страницы не работают
    file_url: str | None = None
    created_at: datetime


class ChatThreadOut(BaseModel):
    client_id: int
    full_name: str
    username: str | None
    last_message_at: datetime
    last_message_preview: str  # текст или метка медиа ("📷 Фото", "🎙 Голосовое")
    last_direction: str
    unread_count: int
