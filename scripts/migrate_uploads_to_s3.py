"""Одноразовая миграция: файлы из upload_dir → S3/MinIO.

Ключ объекта = относительный путь от upload_dir, поэтому stored_path в БД
менять не нужно. Скрипт идемпотентный: перезапись объекта теми же данными
безвредна. Запуск: docker compose exec api python scripts/migrate_uploads_to_s3.py
"""

import mimetypes
import sys
from pathlib import Path

from tg_studio.config import settings
from tg_studio.modules.storage import s3


def main() -> None:
    if not settings.s3_enabled:
        print("S3 не настроен (пустые S3_* в .env) — мигрировать нечего")
        sys.exit(1)

    root = Path(settings.upload_dir)
    if not root.is_dir():
        print(f"Нет папки {settings.upload_dir} — мигрировать нечего")
        return

    s3.ensure_bucket()

    files = [p for p in root.rglob("*") if p.is_file()]
    print(f"Найдено файлов: {len(files)}")
    copied = skipped = 0
    for path in files:
        key = path.relative_to(root).as_posix()
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        s3.put_object(key, path.read_bytes(), mime)
        copied += 1
        print(f"  → {key}")

    print(f"Скопировано: {copied}, пропущено: {skipped}")
    if copied == len(files):
        print("Готово: все файлы в бакете (upload_dir оставлен как fallback).")


if __name__ == "__main__":
    main()
