"""Блокировки уровня БД для check-then-act цепочек.

Два механизма:
- pg_advisory_xact_lock по мастеру — сериализует «слот свободен → создаём
  сеанс / принимаем оффер»: два параллельных запроса (вкладки, владелец +
  мастер) не пройдут проверку пересечений одновременно;
- SELECT … FOR UPDATE на ключевых строках (оффер, работа, платёж) —
  страховка от гонок между API-эндпоинтами и celery-sweep'ами.

Оба — no-op на SQLite (тесты): там одна транзакция за раз.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Первый ключ pg_advisory_xact_lock(key1, key2) — пространство имён, чтобы не
# пересекаться с другими блокировками приложения ("ts" — исторический ключ слотов,
# "st" = seat slots — коворкинг)
SLOT_LOCK_NAMESPACE = 0x7473
SEAT_LOCK_NAMESPACE = 0x7374
# Коворкинг (06): остаток склада — сериализация списаний/продаж по позиции
SUPPLY_LOCK_NAMESPACE = 0x7370
# Продажа: сериализация частичных возвратов по одному чеку
SALE_LOCK_NAMESPACE = 0x7361
# Лимит мест: сериализация создания/включения мест одного бизнеса
SEAT_LIMIT_LOCK_NAMESPACE = 0x736c


def _is_postgres(session: AsyncSession) -> bool:
    bind = session.bind
    return bind is not None and bind.dialect.name == "postgresql"


async def lock_master_slot(session: AsyncSession, master_id: int) -> None:
    """Advisory-блокировка по мастеру до конца транзакции (xact-lock).

    Вызывать ДО проверки занятости слота и до любых мутаций, зависящих от неё.
    """
    if not _is_postgres(session):
        return
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:ns, :key)"),
        {"ns": SLOT_LOCK_NAMESPACE, "key": master_id},
    )


async def lock_seat_slot(session: AsyncSession, seat_id: int) -> None:
    """Advisory-блокировка по месту до конца транзакции (xact-lock).

    Сериализует «место свободно → создаём бронь/блокировку»: два параллельных
    запроса (две вкладки, мастер + админ) не пройдут проверку пересечений
    одновременно. Вызывать ДО проверки пересечений по этому месту.
    """
    if not _is_postgres(session):
        return
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:ns, :key)"),
        {"ns": SEAT_LOCK_NAMESPACE, "key": seat_id},
    )


async def lock_business_seats(session: AsyncSession, business_id: int) -> None:
    """Блокировка набора мест бизнеса до конца транзакции: два параллельных
    создания/включения места не обойдут лимит max_seats."""
    if not _is_postgres(session):
        return
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:ns, :key)"),
        {"ns": SEAT_LIMIT_LOCK_NAMESPACE, "key": business_id},
    )


async def lock_supply(session: AsyncSession, supply_id: int) -> None:
    """Advisory-блокировка по складской позиции до конца транзакции (06).

    Сериализует «проверка остатка → списание/продажа»: два параллельных
    запроса не уведут остаток в минус.
    """
    if not _is_postgres(session):
        return
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:ns, :key)"),
        {"ns": SUPPLY_LOCK_NAMESPACE, "key": supply_id},
    )


async def lock_sale(session: AsyncSession, sale_id: int) -> None:
    """Блокировка чека: два одновременных частичных возврата не вернут больше
    проданного. Строки Sale/SaleItem перечитываются после неё.
    """
    if not _is_postgres(session):
        return
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:ns, :key)"),
        {"ns": SALE_LOCK_NAMESPACE, "key": sale_id},
    )


def for_update(stmt, session: AsyncSession):
    """SELECT … FOR UPDATE; на SQLite (тесты) — просто возвращаем как есть."""
    if _is_postgres(session):
        return stmt.with_for_update()
    return stmt
