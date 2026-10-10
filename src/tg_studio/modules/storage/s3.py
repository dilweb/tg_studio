"""S3/MinIO — файловое хранилище (boto3, синхронный как и дисковый ввод-вывод).

Ключи объектов = прежние относительные пути на диске ("tattoo/sessions/5/x.jpg"),
поэтому миграция — копирование файл-в-объект без изменений в БД. Включается
четырьмя переменными S3_* в env; если их нет — модули хранения работают с диском
(upload_dir), тесты и локальная разработка от этого не зависят.
"""

import logging
from functools import lru_cache

from tg_studio.config import settings

logger = logging.getLogger(__name__)

# Пресайн ссылок: агент шлёт клиенту ссылки на портфолио в сообщениях,
# они должны жить долго
PRESIGN_EXPIRES_SECONDS = 7 * 24 * 3600


@lru_cache(maxsize=1)
def _client():
    """Ленивый boto3-клиент (сетевые вызовы только при использовании)."""
    import boto3  # импорт сюда — чтобы S3 не был нужен, когда он выключен

    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name="us-east-1",  # MinIO: регион фиктивный, но SDK требует
    )


_bucket_checked = False


def ensure_bucket() -> None:
    """Создать бакет, если его нет. Один раз на процесс (дальше дёшево)."""
    global _bucket_checked
    if _bucket_checked:
        return
    from botocore.exceptions import ClientError

    try:
        _client().head_bucket(Bucket=settings.s3_bucket)
    except ClientError:
        _client().create_bucket(Bucket=settings.s3_bucket)
    _bucket_checked = True


def put_object(key: str, data: bytes, mime: str) -> None:
    ensure_bucket()
    _client().put_object(
        Bucket=settings.s3_bucket, Key=key, Body=data, ContentType=mime
    )


def get_object(key: str) -> bytes | None:
    """Прочитать объект (None — нет такого ключа)."""
    ensure_bucket()
    try:
        response = _client().get_object(Bucket=settings.s3_bucket, Key=key)
    except _client().exceptions.NoSuchKey:
        return None
    except Exception:
        logger.warning("S3 get_object %s не удался", key, exc_info=True)
        return None
    try:
        return response["Body"].read()
    finally:
        response["Body"].close()


def delete_object(key: str) -> None:
    """Удалить объект; отсутствие — не ошибка (idempotent)."""
    try:
        _client().delete_object(Bucket=settings.s3_bucket, Key=key)
    except Exception:
        logger.warning("S3 delete_object %s не удался", key, exc_info=True)


def delete_prefix(prefix: str) -> None:
    """Удалить все объекты с префиксом (аналог rmtree для папки сеансов)."""
    try:
        response = _client().list_objects_v2(
            Bucket=settings.s3_bucket, Prefix=prefix
        )
        keys = [{"Key": obj["Key"]} for obj in response.get("Contents", [])]
        if keys:
            _client().delete_objects(
                Bucket=settings.s3_bucket, Delete={"Objects": keys}
            )
    except Exception:
        logger.warning("S3 delete_prefix %s не удался", prefix, exc_info=True)


def presigned_url(key: str, expires: int = PRESIGN_EXPIRES_SECONDS) -> str | None:
    try:
        return _client().generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.s3_bucket, "Key": key},
            ExpiresIn=expires,
        )
    except Exception:
        logger.warning("S3 presign %s не удался", key, exc_info=True)
        return None
