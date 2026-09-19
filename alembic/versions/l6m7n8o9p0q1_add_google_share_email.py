"""add google_share_email

Личный Google-адрес владельца, с которым расшарены календари сервисного
аккаунта (ACL): без правила доступа записи видны только внутри SA.

Revision ID: l6m7n8o9p0q1
Revises: k5l6m7n8o9p0
Create Date: 2026-09-19 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "l6m7n8o9p0q1"
down_revision: Union[str, Sequence[str], None] = "k5l6m7n8o9p0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("businesses", sa.Column("google_share_email", sa.String(256), nullable=True))


def downgrade() -> None:
    op.drop_column("businesses", "google_share_email")
