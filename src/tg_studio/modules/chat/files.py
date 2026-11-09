"""Копии вложений чата: S3/MinIO, fallback — диск (UPLOAD_DIR).

Telegram — только транспорт: бот скачивает фото/аудио/видео и складывает
сюда (chat/messages/<uuid>.<ext>), в БД у сообщения лежит stored_path.
Тогда чат доступен и при недоступности Telegram, и после удаления
сообщения в мессенджере.
"""

import uuid
from pathlib import Path

from tg_studio.config import settings
from tg_studio.modules.storage import s3

_MIME_EXT = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "audio/ogg": "ogg",
    "audio/mpeg": "mp3",
    "video/mp4": "mp4",
    "application/octet-stream": "bin",
}


def _ext_for(mime: str) -> str:
    return _MIME_EXT.get(mime, "bin")


def save_message_file(data: bytes, mime: str) -> str:
    """Записать вложение в своё хранилище, вернуть относительный путь (ключ)."""
    rel_path = f"chat/messages/{uuid.uuid4().hex}.{_ext_for(mime)}"
    if settings.s3_enabled:
        s3.put_object(rel_path, data, mime)
    else:
        abs_path = Path(settings.upload_dir) / rel_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_bytes(data)
    return rel_path


def read_message_file(stored_path: str) -> bytes | None:
    """Прочитать вложение: из S3 или с диска (None — нет)."""
    if settings.s3_enabled:
        return s3.get_object(stored_path)
    path = Path(settings.upload_dir) / stored_path
    return path.read_bytes() if path.is_file() else None
