"""add_user_id_to_ai_conversations

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-04-22

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add nullable user_id column
    op.add_column("ai_conversations", sa.Column("user_id", sa.Integer(), nullable=True))

    # Backfill: link existing conversations to the business owner
    # Since each business has one owner, we can join through businesses
    op.execute(
        """
        UPDATE ai_conversations ac
        SET user_id = b.owner_id
        FROM businesses b
        WHERE ac.business_id = b.id
        """
    )

    # Now make it non-nullable
    op.alter_column("ai_conversations", "user_id", nullable=False)

    # Add foreign key and index
    op.create_foreign_key(
        "fk_ai_conversations_user_id", "ai_conversations", "users", ["user_id"], ["id"]
    )
    op.create_index("ix_ai_conversations_user_id", "ai_conversations", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_ai_conversations_user_id", table_name="ai_conversations")
    op.drop_constraint("fk_ai_conversations_user_id", "ai_conversations", type_="foreignkey")
    op.drop_column("ai_conversations", "user_id")
