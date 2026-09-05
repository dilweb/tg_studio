#!/usr/bin/env python3
"""
Скрипт для регистрации владельца (owner), создания бизнеса и мастера.

Использование:
    python scripts/seed_owner.py --help
    python scripts/seed_owner.py                              # owner_id=22813371488
    python scripts/seed_owner.py --owner-id 123456789          # другой владелец
    python scripts/seed_owner.py --owner-id 123456789 --master-telegram-id 987654321

"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from tg_studio.api.auth import hash_password
from tg_studio.config import settings
from tg_studio.db.models import Business, Master, User, UserRole

DEFAULT_OWNER_TELEGRAM_ID = 22813371488
DEFAULT_PASSWORD = "123"


async def seed_owner(
    owner_telegram_id: int,
    master_telegram_id: int | None = None,
    password: str = DEFAULT_PASSWORD,
) -> None:
    if master_telegram_id is None:
        master_telegram_id = owner_telegram_id + 1

    password_hash_value = hash_password(password)

    engine = create_async_engine(settings.database_url, echo=True)

    async with AsyncSession(engine) as session:
        # ---- Owner ----
        result = await session.execute(
            select(User).where(User.telegram_id == owner_telegram_id)
        )
        owner = result.scalar_one_or_none()

        if not owner:
            owner = User(
                telegram_id=owner_telegram_id,
                first_name="Owner",
                role=UserRole.owner,
                password_hash=password_hash_value,
                is_active=True,
                is_email_verified=True,
            )
            session.add(owner)
            await session.flush()
            print(f"✅ Создан владелец: telegram_id={owner.telegram_id}")
        else:
            print(f"ℹ️  Владелец уже существует: telegram_id={owner.telegram_id}, role={owner.role}")

        # ---- Business ----
        result = await session.execute(
            select(Business).where(Business.owner_id == owner.id)
        )
        business = result.scalar_one_or_none()

        if not business:
            business = Business(
                owner_id=owner.id,
                owner_telegram_id=owner_telegram_id,
                name="TG Studio",
                description="Тату-студия",
                is_active=True,
            )
            session.add(business)
            await session.flush()
            print(f"✅ Создан бизнес: {business.name}")
        else:
            print(f"ℹ️  Бизнес уже существует: {business.name}")

        # ---- Master ----
        result = await session.execute(
            select(User).where(User.telegram_id == master_telegram_id)
        )
        master_user = result.scalar_one_or_none()

        if not master_user:
            master_user = User(
                telegram_id=master_telegram_id,
                first_name="Master",
                role=UserRole.master,
                is_active=True,
                is_email_verified=True,
                password_hash=password_hash_value,
            )
            session.add(master_user)
            await session.flush()
            print(f"✅ Создан пользователь-мастер: telegram_id={master_user.telegram_id}")
        else:
            if master_user.role != UserRole.master:
                master_user.role = UserRole.master
                print(f"ℹ️  Обновлён role→master для telegram_id={master_telegram_id}")
            else:
                print(f"ℹ️  Пользователь-мастер уже существует: telegram_id={master_telegram_id}")

        # Проверяем, есть ли уже запись в masters
        result = await session.execute(
            select(Master).where(Master.user_id == master_user.id)
        )
        master_record = result.scalar_one_or_none()

        if not master_record:
            master_record = Master(
                business_id=business.id,
                user_id=master_user.id,
                telegram_id=master_telegram_id,
                full_name="Master Tattoo",
                description="Главный мастер студии",
                is_active=True,
                password_hash=password_hash_value,
            )
            session.add(master_record)
            await session.flush()
            print(f"✅ Создана запись мастера: {master_record.full_name}")
        else:
            print(f"ℹ️  Запись мастера уже существует: {master_record.full_name}")

        await session.commit()

    await engine.dispose()

    print(f"\n{'='*50}")
    print(f"🎉 Всё готово!")
    print(f"   Владелец: telegram_id={owner_telegram_id}, пароль={password}")
    print(f"   Мастер:   telegram_id={master_telegram_id}, пароль={password}")
    print(f"{'='*50}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Создание владельца, бизнеса и мастера"
    )
    parser.add_argument(
        "--owner-id",
        type=int,
        default=DEFAULT_OWNER_TELEGRAM_ID,
        help=f"Telegram ID владельца (по умолчанию: {DEFAULT_OWNER_TELEGRAM_ID})",
    )
    parser.add_argument(
        "--master-telegram-id",
        type=int,
        default=None,
        help="Telegram ID мастера (по умолчанию: owner_id + 1)",
    )
    parser.add_argument(
        "--password",
        type=str,
        default=DEFAULT_PASSWORD,
        help=f"Пароль для owner и master (по умолчанию: {DEFAULT_PASSWORD})",
    )

    args = parser.parse_args()
    asyncio.run(
        seed_owner(
            owner_telegram_id=args.owner_id,
            master_telegram_id=args.master_telegram_id,
            password=args.password,
        )
    )


if __name__ == "__main__":
    main()