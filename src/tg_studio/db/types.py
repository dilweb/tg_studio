"""Декоратор колонок времени с единой семантикой: в БД хранится UTC.

Postgres-колонки без таймзоны (TIMESTAMP) при server_default=func.now()
сохраняют wall-clock сессионной зоны; сессия пинится в UTC (db/session.py),
поэтому хранимое значение — всегда UTC. Декоратор при чтении возвращает
aware datetime с UTC: API сериализует его со смещением "+00:00", и фронтенд
корректно переводит в локальное время любого зрителя. Раньше наивный ISO
без смещения фронтенд парсил как локальный — в Алматы (UTC+5) время уезжало
на 5 часов назад.

При записи aware-значение приводится к UTC wall-clock, naive пишется как есть.
DDL не меняется (impl = DateTime) — миграция не нужна.
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator):
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is not None:
            return value.astimezone(UTC).replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
