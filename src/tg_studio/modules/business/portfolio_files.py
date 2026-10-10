"""Фото портфолио мастеров: S3/MinIO, fallback — диск (UPLOAD_DIR).

Файлы лежат в masters/portfolio/<master_id>/ (ключ объекта в S3 или путь
в upload_dir — одинаковые относительные пути), имена генерируются (uuid),
в БД — путь и метаданные (MasterPortfolioFile). Отдаются публичным
read-only эндпоинтом — маркетинговый материал для клиентов.
"""

import re
import uuid
from pathlib import Path

from tg_studio.config import settings
from tg_studio.db.models import MasterPortfolioFile
from tg_studio.modules.storage import s3

MAX_FILE_BYTES = 10 * 1024 * 1024

_ALLOWED_MIME = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
    "image/heic",
}

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


def master_rel_dir(master_id: int) -> Path:
    return Path("masters") / "portfolio" / str(master_id)


def save_file(
    db,
    master_id: int,
    data: bytes,
    original_name: str,
    mime: str,
) -> MasterPortfolioFile:
    """Записать файл (S3 или диск) и добавить метаданные (flush даёт id)."""
    rel_dir = master_rel_dir(master_id)

    name = f"portfolio_{uuid.uuid4().hex}.{_ext_for(original_name, mime)}"
    key = str(rel_dir / name)
    if settings.s3_enabled:
        s3.put_object(key, data, mime)
    else:
        abs_dir = Path(settings.upload_dir) / rel_dir
        abs_dir.mkdir(parents=True, exist_ok=True)
        (abs_dir / name).write_bytes(data)

    record = MasterPortfolioFile(
        master_id=master_id,
        stored_path=key,
        original_name=(original_name or "photo")[:256],
        mime=mime,
        size_bytes=len(data),
    )
    db.add(record)
    return record


def absolute_path(record: MasterPortfolioFile) -> Path:
    """Путь на диске (только для режима без S3)."""
    return Path(settings.upload_dir) / record.stored_path


def read_file(record: MasterPortfolioFile) -> bytes | None:
    """Прочитать файл: из S3 или с диска (None — нет)."""
    if settings.s3_enabled:
        return s3.get_object(record.stored_path)
    path = absolute_path(record)
    return path.read_bytes() if path.is_file() else None


def delete_file(record: MasterPortfolioFile) -> None:
    """Убрать файл (строку удаляет вызывающий)."""
    if settings.s3_enabled:
        s3.delete_object(record.stored_path)
        return
    absolute_path(record).unlink(missing_ok=True)


def portfolio_url(record: MasterPortfolioFile) -> str:
    """Абсолютная ссылка для клиента (бот, get_masters_info)."""
    base = (settings.api_public_url or "").rstrip("/")
    return f"{base}/api/public/masters/{record.master_id}/portfolio/{record.id}"


# ── Аватар мастера (фото профиля) ────────────────────────────────────────────
# Один файл на мастера, путь хранится в masters.avatar_stored_path (без
# отдельной таблицы — история аватаров не нужна).

def save_avatar(master, data: bytes, original_name: str, mime: str) -> str:
    """Записать аватар, вернуть относительный путь/ключ (прошлый файл
    затирается — вызывающий обновляет master.avatar_stored_path)."""
    rel_dir = Path("masters") / "avatar" / str(master.id)
    name = f"avatar_{uuid.uuid4().hex}.{_ext_for(original_name, mime)}"
    key = str(rel_dir / name)

    delete_avatar_file(master)  # прошлый файл из S3/с диска
    if settings.s3_enabled:
        s3.put_object(key, data, mime)
    else:
        abs_dir = Path(settings.upload_dir) / rel_dir
        abs_dir.mkdir(parents=True, exist_ok=True)
        (abs_dir / name).write_bytes(data)
    return key


def avatar_absolute_path(master) -> Path:
    """Путь на диске (только для режима без S3)."""
    return Path(settings.upload_dir) / (master.avatar_stored_path or "")


def read_avatar(master) -> bytes | None:
    if not master.avatar_stored_path:
        return None
    if settings.s3_enabled:
        return s3.get_object(master.avatar_stored_path)
    path = avatar_absolute_path(master)
    return path.read_bytes() if path.is_file() else None


def delete_avatar_file(master) -> None:
    """Убрать старый файл аватара (если был) и обнулить путь в объекте."""
    if not master.avatar_stored_path:
        return
    if settings.s3_enabled:
        s3.delete_object(master.avatar_stored_path)
    else:
        avatar_absolute_path(master).unlink(missing_ok=True)
    master.avatar_stored_path = None


def avatar_mime(master) -> str:
    """MIME по расширению сохранённого файла (расширение ставим сами)."""
    return {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "webp": "image/webp",
        "gif": "image/gif",
        "heic": "image/heic",
    }.get(
        (master.avatar_stored_path or "").rpartition(".")[2].lower(), "image/jpeg"
    )


def avatar_url(master) -> str | None:
    """Абсолютная ссылка на аватар для клиентов (бот, миниаппа)."""
    if not master.avatar_stored_path:
        return None
    base = (settings.api_public_url or "").rstrip("/")
    return f"{base}/api/public/masters/{master.id}/avatar"
