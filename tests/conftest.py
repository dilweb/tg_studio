"""
Shared fixtures and database helpers for the test suite.

Each test gets a fresh SQLite in-memory database (StaticPool) so tests are
fully isolated without needing a running Postgres instance.
"""

import os

# Must be set BEFORE tg_studio modules are imported so pydantic-settings is satisfied.
os.environ.setdefault("BOT_TOKEN", "1234567890:AAHtest_token_for_testing_only_ci")
os.environ.setdefault("DEBUG", "true")
os.environ.setdefault("MINIAPP_URL", "http://testserver")
os.environ.setdefault("API_PUBLIC_URL", "http://testserver")

from datetime import datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from tg_studio.api.app import app
from tg_studio.db.base import Base
from tg_studio.db.models import (
    Business,
    Client,
    Master,
    MasterService,
    Service,
    User,
    UserRole,
    WorkSchedule,
)
from tg_studio.db.session import get_session

# ---------------------------------------------------------------------------
# SQLite in-memory engine — recreated fresh for every test function
# ---------------------------------------------------------------------------

@pytest.fixture
async def engine():
    _engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield _engine
    await _engine.dispose()


@pytest.fixture
async def db_session(engine):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest.fixture
async def api_client(db_session):
    async def override_get_session():
        yield db_session

    app.dependency_overrides[get_session] = override_get_session
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
            follow_redirects=True,
        ) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# DB record builder helpers (used directly in integration tests)
# ---------------------------------------------------------------------------

async def make_user(
    session: AsyncSession,
    telegram_id: int | None = None,
    email: str | None = None,
    first_name: str = "Test",
    role: UserRole = UserRole.client,
) -> User:
    obj = User(
        telegram_id=telegram_id,
        email=email,
        first_name=first_name,
        role=role,
    )
    session.add(obj)
    await session.flush()
    return obj


async def make_business(
    session: AsyncSession,
    owner_telegram_id: int = 99999,
    name: str = "Test Studio",
) -> Business:
    owner = await make_user(
        session, telegram_id=owner_telegram_id, first_name=name, role=UserRole.owner
    )
    obj = Business(owner_id=owner.id, owner_telegram_id=owner_telegram_id, name=name)
    session.add(obj)
    await session.flush()
    return obj


async def make_master(
    session: AsyncSession,
    business_id: int,
    is_active: bool = True,
    full_name: str = "Test Master",
) -> Master:
    obj = Master(business_id=business_id, full_name=full_name, is_active=is_active)
    session.add(obj)
    await session.flush()
    return obj


async def make_service(
    session: AsyncSession,
    business_id: int,
    price: float = 5000.0,
) -> Service:
    obj = Service(
        business_id=business_id,
        name="Test Service",
        price=price,
    )
    session.add(obj)
    await session.flush()
    return obj


async def make_schedule(
    session: AsyncSession,
    master_id: int,
    weekday: int = 0,
    start_time: str = "10:00",
    end_time: str = "18:00",
    slot_duration_minutes: int = 60,
) -> WorkSchedule:
    obj = WorkSchedule(
        master_id=master_id,
        weekday=weekday,
        start_time=start_time,
        end_time=end_time,
        slot_duration_minutes=slot_duration_minutes,
    )
    session.add(obj)
    await session.flush()
    return obj


async def make_client(
    session: AsyncSession,
    telegram_id: int = 42,
    full_name: str = "Test Client",
) -> Client:
    user = await make_user(
        session, telegram_id=telegram_id, first_name=full_name, role=UserRole.client
    )
    obj = Client(user_id=user.id, telegram_id=telegram_id, full_name=full_name)
    session.add(obj)
    await session.flush()
    return obj
