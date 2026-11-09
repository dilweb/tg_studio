"""Postgres-гонка склада (06). Не входит в обычный SQLite-прогон.

Запуск против выделенной тестовой БД:
    TEST_POSTGRES_URL=postgresql+asyncpg://... pytest -m postgres_race \
        tests/integration/test_coworking_stock_postgres.py

БД должна быть пустой/одноразовой: тест создаёт схему из metadata.
"""

import asyncio
import os

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tg_studio.db.base import Base
from tg_studio.db.models import Business, Sale, Supply, SupplyMovement, User, UserRole
from tg_studio.modules.coworking import stock
from tg_studio.modules.coworking.stock_schemas import SaleCreate

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_POSTGRES_URL"),
    reason="Задайте TEST_POSTGRES_URL для реальной Postgres-гонки",
)


@pytest.mark.asyncio
async def test_concurrent_sales_cannot_oversell():
    """Восемь независимых транзакций за один остаток: 1 успех, 7 × 409.

    Здесь принципиально НЕ общая db_session: только отдельные соединения
    Postgres проверяют advisory lock + перечитывание остатка после lock.
    """
    engine = create_async_engine(os.environ["TEST_POSTGRES_URL"])
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            owner = User(first_name="race", role=UserRole.owner)
            session.add(owner)
            await session.flush()
            business = Business(owner_id=owner.id, owner_telegram_id=1, name="race")
            session.add(business)
            await session.flush()
            product = Supply(
                business_id=business.id,
                name="Последний товар",
                category="test",
                unit="шт",
                quantity=1,
                sale_price=100,
            )
            session.add(product)
            await session.commit()
            ids = (business.id, product.id, owner.id)

        business_id, supply_id, owner_id = ids
        body = SaleCreate(
            items=[{"supply_id": supply_id, "qty": 1}],
            buyer_name="Гость",
            payment_method="cash",
        )
        start = asyncio.Event()

        async def buy():
            from fastapi import HTTPException

            async with factory() as session:
                business = await session.get(Business, business_id)
                await start.wait()
                try:
                    await stock.create_sale(session, business, body, owner_id, None)
                    await session.commit()
                    return "ok"
                except HTTPException as exc:
                    await session.rollback()
                    return exc.status_code

        tasks = [asyncio.create_task(buy()) for _ in range(8)]
        await asyncio.sleep(0.1)  # все сессии открыли соединение
        start.set()
        results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=30)

        async with factory() as session:
            product = await session.get(Supply, supply_id)
            sales = (await session.execute(select(func.count(Sale.id)))).scalar_one()
            movement_sum = (
                await session.execute(
                    select(func.sum(SupplyMovement.delta)).where(
                        SupplyMovement.supply_id == supply_id
                    )
                )
            ).scalar_one()
            assert results.count("ok") == 1, results
            assert results.count(409) == 7, results
            assert float(product.quantity) == 0
            assert sales == 1
            assert float(movement_sum) == -1
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()
