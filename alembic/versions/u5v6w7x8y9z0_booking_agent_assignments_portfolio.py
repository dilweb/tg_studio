"""AI-агент записи: chat_assignments, master_portfolio_files, from_ai

Клиентский AI-агент в TG-боте: собирает заказ и эскалирует его мастеру
(chat_assignments — закрепление чата, ChatDirection.from_ai — зеркало
ответов агента в «Чатах»). Портфолио мастеров — master_portfolio_files
(файлы на диске в UPLOAD_DIR, отдаются публичным read-only эндпоинтом).

Revision ID: u5v6w7x8y9z0
Revises: t3u4v5w6x7y8
Create Date: 2026-10-06

"""

import sqlalchemy as sa
from alembic import op

revision = "u5v6w7x8y9z0"
down_revision = "t3u4v5w6x7y8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Postgres enum — ALTER TYPE ADD VALUE нельзя внутри транзакции с
    # другими обращениями к типу; alembic запускает execute вне батчей,
    # поэтому просто выполняем первым стейтментом.
    op.execute("ALTER TYPE chatdirection ADD VALUE IF NOT EXISTS 'from_ai'")

    op.create_table(
        "chat_assignments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("business_id", sa.Integer(), sa.ForeignKey("businesses.id"), nullable=False),
        sa.Column("client_id", sa.Integer(), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column("master_id", sa.Integer(), sa.ForeignKey("masters.id"), nullable=False),
        sa.Column("status", sa.Enum("open", "closed", name="chatassignmentstatus"),
                  nullable=False, server_default="open"),
        sa.Column("order_summary", sa.Text(), nullable=True),
        sa.Column("escalated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_chat_assignments_business_id", "chat_assignments", ["business_id"])
    op.create_index("ix_chat_assignments_client_id", "chat_assignments", ["client_id"])
    op.create_index("ix_chat_assignments_master_id", "chat_assignments", ["master_id"])

    op.create_table(
        "master_portfolio_files",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("master_id", sa.Integer(),
                  sa.ForeignKey("masters.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stored_path", sa.String(512), nullable=False, unique=True),
        sa.Column("original_name", sa.String(256), nullable=False),
        sa.Column("mime", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_master_portfolio_files_master_id", "master_portfolio_files", ["master_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_master_portfolio_files_master_id", table_name="master_portfolio_files")
    op.drop_table("master_portfolio_files")

    op.drop_index("ix_chat_assignments_master_id", table_name="chat_assignments")
    op.drop_index("ix_chat_assignments_client_id", table_name="chat_assignments")
    op.drop_index("ix_chat_assignments_business_id", table_name="chat_assignments")
    op.drop_table("chat_assignments")
    op.execute("DROP TYPE IF EXISTS chatassignmentstatus")

    # chatdirection.from_ai не удаляем: Postgres не умеет убирать значения
    # enum без пересоздания типа, а лишнее значение безвредно.
