"""clients: идентификация по нескольким каналам

Клиент может прийти не только из Telegram (телефон, Instagram, лично).
telegram_id и user_id становятся nullable, добавляются instagram_username,
source (откуда пришли) и note (свободные метаданные от владельца).
PK остаётся суррогатным int — идентификаторы каналов это атрибуты, а не
сущность клиента.

Revision ID: p3q4r5s6t7u8
Revises: p2q3r4s5t6u7
Create Date: 2026-10-03

"""

import sqlalchemy as sa
from alembic import op

revision = "p3q4r5s6t7u8"
down_revision = "p2q3r4s5t6u7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("clients", "user_id", existing_type=sa.Integer(), nullable=True)
    op.alter_column("clients", "telegram_id", existing_type=sa.BigInteger(), nullable=True)
    op.add_column("clients", sa.Column("instagram_username", sa.String(64), nullable=True))
    op.add_column("clients", sa.Column("source", sa.String(16), nullable=False, server_default="tg"))
    op.add_column("clients", sa.Column("note", sa.Text(), nullable=True))
    op.alter_column("clients", "source", server_default=None)
    # Уникальность идентификаторов каналов: NULL не конфликтуют (Postgres)
    op.create_unique_constraint("uq_clients_phone", "clients", ["phone"])
    op.create_unique_constraint(
        "uq_clients_instagram_username", "clients", ["instagram_username"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_clients_instagram_username", "clients")
    op.drop_constraint("uq_clients_phone", "clients")
    op.drop_column("clients", "note")
    op.drop_column("clients", "source")
    op.drop_column("clients", "instagram_username")
    # Обратный переход требует, чтобы у всех клиентов снова был Telegram.
    # Клиентов без telegram_id удаляем вместе с их данными (FK без cascade).
    conn = op.get_bind()
    manual_ids = [
        str(r[0])
        for r in conn.execute(sa.text("SELECT id FROM clients WHERE telegram_id IS NULL"))
    ]
    if manual_ids:
        ids = ",".join(manual_ids)
        for table in (
            "tattoo_files",
            "tattoo_sessions",
            "tattoo_works",
            "chat_messages",
            "ai_conversations",
        ):
            op.execute(
                f"DELETE FROM {table} WHERE client_id IN ({ids})"  # noqa: S608
            )
        op.execute(f"DELETE FROM clients WHERE id IN ({ids})")  # noqa: S608
    op.alter_column("clients", "telegram_id", existing_type=sa.BigInteger(), nullable=False)
    op.alter_column("clients", "user_id", existing_type=sa.Integer(), nullable=False)