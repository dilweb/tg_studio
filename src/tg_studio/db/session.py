from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

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
