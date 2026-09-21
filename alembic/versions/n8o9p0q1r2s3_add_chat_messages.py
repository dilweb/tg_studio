"""add_chat_messages

Чаты с клиентами: клиент пишет боту — сообщения падают в систему,
мастер/владелец отвечает из миниаппа через бота. История — таблица
chat_messages (direction from_client/from_master, фото по telegram file_id).

Revision ID: n8o9p0q1r2s3
Revises: m7n8o9p0q1r2
Create Date: 2026-09-21

"""
from alembic import op
import sqlalchemy as sa

revision = "n8o9p0q1r2s3"
down_revision = "m7n8o9p0q1r2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("client_id", sa.Integer(), sa.ForeignKey("clients.id"), nullable=False),
        sa.Column(
            "direction",
            sa.Enum("from_client", "from_master", name="chatdirection", length=16),
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("telegram_file_id", sa.String(256), nullable=True),
        sa.Column("file_kind", sa.String(16), nullable=True),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("read_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_chat_messages_client_id", "chat_messages", ["client_id"])
    op.create_index("ix_chat_messages_created_at", "chat_messages", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_chat_messages_created_at", table_name="chat_messages")
    op.drop_index("ix_chat_messages_client_id", table_name="chat_messages")
    op.drop_table("chat_messages")
    op.execute("DROP TYPE IF EXISTS chatdirection")
