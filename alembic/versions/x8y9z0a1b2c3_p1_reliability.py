"""P1-надёжность: дедуп входящих чата, копия вложений, индексы, drift session_offers.

Revision ID: x8y9z0a1b2c3
Revises: w7x8y9z0a1b2
Create Date: 2026-10-26
"""

from alembic import op
import sqlalchemy as sa

revision = "x8y9z0a1b2c3"
down_revision = "w7x8y9z0a1b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Копия вложений чата в своё хранилище (Telegram — только транспорт)
    op.add_column(
        "chat_messages",
        sa.Column("stored_path", sa.String(512), nullable=True),
    )
    # Дедуп повторных доставок сообщений бота: (client_id, telegram_message_id)
    # уникальны (в одном чате Telegram id сообщений не повторяются)
    op.create_index(
        "ix_chat_messages_client_tg_msg",
        "chat_messages",
        ["client_id", "telegram_message_id"],
        unique=True,
    )
    # Фильтр пересечений сеансов по диапазону дат (_assert_slot_free)
    op.create_index(
        "ix_tattoo_sessions_session_date",
        "tattoo_sessions",
        ["session_date"],
        unique=False,
    )
    # Drift модель↔БД: в модели created_at naive (UTCDateTime), в БД колонка
    # timestamptz. Приводим к timestamp without time zone; значения хранятся
    # в UTC wall-clock (сессия приложения пинится в UTC), а AT TIME ZONE 'UTC'
    # сохраняет их как есть независимо от таймзоны сервера миграции.
    op.alter_column(
        "session_offers",
        "created_at",
        existing_type=sa.DateTime(timezone=True),
        type_=sa.DateTime(),
        postgresql_using="created_at AT TIME ZONE 'UTC'",
    )


def downgrade() -> None:
    op.alter_column(
        "session_offers",
        "created_at",
        existing_type=sa.DateTime(),
        type_=sa.DateTime(timezone=True),
        postgresql_using="created_at AT TIME ZONE 'UTC'",
    )
    op.drop_index("ix_tattoo_sessions_session_date", table_name="tattoo_sessions")
    op.drop_index("ix_chat_messages_client_tg_msg", table_name="chat_messages")
    op.drop_column("chat_messages", "stored_path")
