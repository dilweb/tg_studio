"""merge_business_and_ai_conversations

Revision ID: 8947b8295f70
Revises: 523b0f3fb9b3, b2c3d4e5f6a7
Create Date: 2026-04-22 14:56:38.918443

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8947b8295f70'
down_revision: Union[str, Sequence[str], None] = ('523b0f3fb9b3', 'b2c3d4e5f6a7')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
