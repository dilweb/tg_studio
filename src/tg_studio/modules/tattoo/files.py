"""Хранение фото сеансов: S3/MinIO, fallback — диск (UPLOAD_DIR).

Файлы лежат в tattoo/sessions/<session_id>/ (ключ объекта в S3 или путь
в upload_dir — одинаковые относительные пути), имена генерируются (uuid),
в БД — путь и метаданные. Куда пишем, решает settings.s3_enabled.
"""

import re
import shutil
import uuid
from pathlib import Path

from tg_studio.config import settings
from tg_studio.db.models import TattooFile, TattooFileKind, TattooSession
from tg_studio.modules.storage import s3

MAX_FILE_BYTES = 10 * 1024 * 1024  # как лимит Telegram для фото

_MIME_EXT = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
    "image/heic": "heic",
}


def _ext_for(original_name: str, mime: str) -> str:
    """Расширение: из имени файла, иначе из mime (image/jpeg → jpg)."""
    m = re.search(r"\.([A-Za-z0-9]{1,8})$", original_name.strip())
    if m:
        return m.group(1).lower()
    return _MIME_EXT.get(mime, mime.rpartition("/")[2][:8] or "jpg")


def session_rel_dir(session_id: int) -> Path:
    return Path("tattoo") / "sessions" / str(session_id)


def save_file(
    db,
    tattoo_session: TattooSession,
    kind: TattooFileKind,
    data: bytes,
    original_name: str,
    mime: str,
) -> TattooFile:
    """Записать файл (S3 или диск) и добавить метаданные (flush даёт id)."""
    rel_dir = session_rel_dir(tattoo_session.id)

    name = f"{kind.value}_{uuid.uuid4().hex}.{_ext_for(original_name, mime)}"
    key = str(rel_dir / name)
    if settings.s3_enabled:
        s3.put_object(key, data, mime)
    else:
        abs_dir = Path(settings.upload_dir) / rel_dir
        abs_dir.mkdir(parents=True, exist_ok=True)
        (abs_dir / name).write_bytes(data)

    record = TattooFile(
        session_id=tattoo_session.id,
        kind=kind,
        stored_path=key,
        original_name=(original_name or "photo")[:256],
        mime=mime,
        size_bytes=len(data),
    )
    db.add(record)
    return record


def absolute_path(record: TattooFile) -> Path:
    """Путь на диске (только для режима без S3)."""
    return Path(settings.upload_dir) / record.stored_path


def read_file(record: TattooFile) -> bytes | None:
    """Прочитать файл: из S3 или с диска (None — нет)."""
    if settings.s3_enabled:
        return s3.get_object(record.stored_path)
    path = absolute_path(record)
    return path.read_bytes() if path.is_file() else None


def delete_file(record: TattooFile) -> None:
    """Убрать файл (строку удаляет вызывающий через ORM-каскад)."""
    if settings.s3_enabled:
        s3.delete_object(record.stored_path)
        return
    absolute_path(record).unlink(missing_ok=True)


def delete_session_dir(session_ids) -> None:
    """Стереть папки сеансов (вызывается после commit — БД уже честна).

    Осиротевшие файлы в случае падения между commit и удалением —
    не проблема: хранилище не источник правды, чистим игнорируя ошибки.
    """
    for session_id in session_ids:
        if settings.s3_enabled:
            s3.delete_prefix(f"{session_rel_dir(session_id)}/")
        else:
            shutil.rmtree(Path(settings.upload_dir) / session_rel_dir(session_id), ignore_errors=True)
