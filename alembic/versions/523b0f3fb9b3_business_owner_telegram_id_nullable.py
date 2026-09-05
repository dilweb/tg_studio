"""business_owner_telegram_id_nullable

Revision ID: 523b0f3fb9b3
Revises: f3a4b5c6d7e8
Create Date: 2026-04-20 15:39:25.017176

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '523b0f3fb9b3'
down_revision: Union[str, Sequence[str], None] = 'f3a4b5c6d7e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('businesses', 'owner_telegram_id',
               existing_type=sa.BIGINT(),
               nullable=True)


def downgrade() -> None:
    op.alter_column('businesses', 'owner_telegram_id',
               existing_type=sa.BIGINT(),
               nullable=False)
