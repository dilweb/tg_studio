"""Хранение фото сеансов на диске (UPLOAD_DIR) — без облака.

Файлы лежат в upload_dir/tattoo/sessions/<session_id>/, имена генерируются
(uuid), в БД — относительный путь и метаданные. Папка переезжает на VPS
rsync'ом вместе с дампом БД без каких-либо правок.
"""

import re
import shutil
import uuid
from pathlib import Path

from tg_studio.config import settings
from tg_studio.db.models import TattooFile, TattooFileKind, TattooSession

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
    """Записать файл на диск и добавить метаданные (flush даёт id)."""
    rel_dir = session_rel_dir(tattoo_session.id)
    abs_dir = Path(settings.upload_dir) / rel_dir
    abs_dir.mkdir(parents=True, exist_ok=True)

    name = f"{kind.value}_{uuid.uuid4().hex}.{_ext_for(original_name, mime)}"
    (abs_dir / name).write_bytes(data)

    record = TattooFile(
        session_id=tattoo_session.id,
        kind=kind,
        stored_path=str(rel_dir / name),
        original_name=(original_name or "photo")[:256],
        mime=mime,
        size_bytes=len(data),
    )
    db.add(record)
    return record


def absolute_path(record: TattooFile) -> Path:
    return Path(settings.upload_dir) / record.stored_path


def delete_file(record: TattooFile) -> None:
    """Убрать файл с диска (строку удаляет вызывающий через ORM-каскад)."""
    absolute_path(record).unlink(missing_ok=True)


def delete_session_dir(session_ids) -> None:
    """Стереть папки сеансов (вызывается после commit — БД уже честна).

    Осиротевшие на диске файлы в случае падения между commit и rmtree —
    не проблема: диск не источник правды, чистим игнорируя ошибки.
    """
    for session_id in session_ids:
        shutil.rmtree(Path(settings.upload_dir) / session_rel_dir(session_id), ignore_errors=True)
