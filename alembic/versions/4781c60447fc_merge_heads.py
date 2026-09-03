"""merge_heads

Revision ID: 4781c60447fc
Revises: 8947b8295f70, g1h2i3j4k5l6
Create Date: 2026-08-30 20:08:54.187271

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4781c60447fc'
down_revision: Union[str, Sequence[str], None] = ('8947b8295f70', 'g1h2i3j4k5l6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
