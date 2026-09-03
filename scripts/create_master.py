#!/usr/bin/env python3
"""
Скрипт для создания мастера в студии.

Мастер привязывается к бизнесу, который принадлежит владельцу с указанным
telegram_id. По умолчанию — 22813371488.

Использование:
    python scripts/create_master.py --help
    python scripts/create_master.py --telegram-id 123456789 --full-name "Иван Иванов" --password "secret123"
    python scripts/create_master.py --full-name "Пётр Петров" --no-telegram --password "pass456"

Если --telegram-id не указан, мастер создаётся без привязки к Telegram.
Если --password не указан, будет сгенерирован случайный пароль.
"""

import argparse
import asyncio
import os
import secrets
import sys
import string

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from tg_studio.api.auth import hash_password
from tg_studio.config import settings
from tg_studio.db.models import Business, Master, User, UserRole

OWNER_TELEGRAM_ID = 22813371488


def generate_password(length: int = 12) -> str:
    chars = string.ascii_letters + string.digits + "!@#$%^&*"
    return "".join(secrets.choice(chars) for _ in range(length))


async def create_master(
    owner_telegram_id: int,
    full_name: str,
    telegram_id: int | None = None,
    password: str | None = None,
    description: str = "",
) -> None:
    if password is None:
        password = generate_password()
        print(f"🔑 Сгенерирован пароль: {password}")

    engine = create_async_engine(settings.database_url, echo=True)

    async with AsyncSession(engine) as session:
        # Находим бизнес по telegram_id владельца
        result = await session.execute(
            select(Business).where(
                Business.owner_telegram_id == owner_telegram_id
            )
        )
        business = result.scalar_one_or_none()

        if not business:
            # fallback: ищем через User
            result = await session.execute(
                select(User).where(
                    User.telegram_id == owner_telegram_id,
                    User.role == UserRole.owner,
                )
            )
            owner = result.scalar_one_or_none()
            if not owner:
                print(f"❌ Владелец с telegram_id={owner_telegram_id} не найден!")
                print(f"   Сначала запусти seed_owner.py или seed_owner.sql")
                await engine.dispose()
                sys.exit(1)

            result = await session.execute(
                select(Business).where(Business.owner_id == owner.id)
            )
            business = result.scalar_one_or_none()

        if not business:
            print(f"❌ Бизнес для владельца telegram_id={owner_telegram_id} не найден!")
            await engine.dispose()
            sys.exit(1)

        print(f"🏢 Бизнес: {business.name}")

        # Создаём пользователя (User) для мастера
        master_user = User(
            telegram_id=telegram_id,
            first_name=full_name.split()[0] if full_name.split() else full_name,
            last_name=" ".join(full_name.split()[1:]) if len(full_name.split()) > 1 else None,
            role=UserRole.master,
            is_active=True,
            password_hash=hash_password(password),
        )
        session.add(master_user)
        await session.flush()
        print(f"✅ Создан пользователь: id={master_user.id}, role=master")

        # Создаём запись мастера
        master = Master(
            business_id=business.id,
            user_id=master_user.id,
            telegram_id=telegram_id,
            full_name=full_name,
            description=description or None,
            is_active=True,
            password_hash=master_user.password_hash,
        )
        session.add(master)
        await session.flush()
        print(f"✅ Создан мастер: id={master.id}, full_name={master.full_name}")

        if telegram_id:
            print(f"📱 Telegram ID: {telegram_id}")
        else:
            print("📱 Telegram: не привязан")

        await session.commit()

    await engine.dispose()

    print(f"\n🎉 Мастер успешно создан!")
    print(f"   Логин: telegram_id={telegram_id or 'не указан'}")
    print(f"   Пароль: {password}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Создание мастера в тату-студии"
    )
    parser.add_argument(
        "--owner-telegram-id",
        type=int,
        default=OWNER_TELEGRAM_ID,
        help=f"Telegram ID владельца студии (по умолчанию: {OWNER_TELEGRAM_ID})",
    )
    parser.add_argument(
        "--full-name",
        type=str,
        required=True,
        help="Полное имя мастера (например: 'Иван Иванов')",
    )
    parser.add_argument(
        "--telegram-id",
        type=int,
        default=None,
        help="Telegram ID мастера (если не указан, мастер без привязки к Telegram)",
    )
    parser.add_argument(
        "--password",
        type=str,
        default=None,
        help="Пароль для входа в веб-панель (если не указан, будет сгенерирован)",
    )
    parser.add_argument(
        "--description",
        type=str,
        default="",
        help="Описание мастера (необязательно)",
    )
    parser.add_argument(
        "--no-telegram",
        action="store_true",
        help="Явно указать, что Telegram не нужен",
    )

    args = parser.parse_args()

    if args.no_telegram and args.telegram_id:
        print("❌ Нельзя указать одновременно --no-telegram и --telegram-id")
        sys.exit(1)

    if args.no_telegram:
        args.telegram_id = None

    asyncio.run(
        create_master(
            owner_telegram_id=args.owner_telegram_id,
            full_name=args.full_name,
            telegram_id=args.telegram_id,
            password=args.password,
            description=args.description,
        )
    )


if __name__ == "__main__":
    main()