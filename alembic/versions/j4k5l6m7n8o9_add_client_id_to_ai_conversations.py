"""add_client_id_to_ai_conversations

Revision ID: j4k5l6m7n8o9
Revises: i3j4k5l6m7n8
Create Date: 2026-09-18 00:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'j4k5l6m7n8o9'
down_revision: Union[str, Sequence[str], None] = 'i3j4k5l6m7n8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('ai_conversations', sa.Column('client_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_ai_conversations_client_id', 'ai_conversations', 'clients', ['client_id'], ['id']
    )
    op.create_index('ix_ai_conversations_client_id', 'ai_conversations', ['client_id'])
    op.alter_column('ai_conversations', 'user_id', nullable=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column('ai_conversations', 'user_id', nullable=False)
    op.drop_index('ix_ai_conversations_client_id', table_name='ai_conversations')
    op.drop_constraint('fk_ai_conversations_client_id', 'ai_conversations', type_='foreignkey')
    op.drop_column('ai_conversations', 'client_id')
