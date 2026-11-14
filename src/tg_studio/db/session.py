from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tg_studio.config import settings

# Сессия Postgres пинится в UTC: колонки без таймзоны (server_default=func.now())
# тогда хранят UTC wall-clock, а db/types.UTCDateTime при чтении метит их aware-UTC.
_connect_args = (
    {"server_settings": {"timezone": "UTC"}}
    if settings.database_url.startswith("postgresql")
    else {}
)

engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_pre_ping=True,
    connect_args=_connect_args,
)

async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session


@asynccontextmanager
async def task_session() -> AsyncGenerator[AsyncSession, None]:
    """Сессия для одноразового event loop — celery-задачи (asyncio.run на задачу).

    Глобальный engine держит пул коннектов, привязанных к event loop первого
    запроса; celery-воркер открывает каждой задаче новый loop, и коннект из
    старого пула падает («Event loop is closed», «attached to a different loop»).
    NullPool: соединение живёт ровно одну сессию и закрывается в её же loop.
    """
    task_engine = create_async_engine(
        settings.database_url,
        echo=settings.debug,
        poolclass=NullPool,
        connect_args=_connect_args,
    )
    try:
        async with async_sessionmaker(task_engine, expire_on_commit=False)() as session:
            yield session
    finally:
        await task_engine.dispose()
