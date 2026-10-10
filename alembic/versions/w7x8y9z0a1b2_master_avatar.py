"""masters.avatar_stored_path — фото профиля мастера (аватар).

Revision ID: w7x8y9z0a1b2
Revises: v6w7x8y9z0a1
Create Date: 2026-10-10
"""

from alembic import op
import sqlalchemy as sa

revision = "w7x8y9z0a1b2"
down_revision = "v6w7x8y9z0a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "masters",
        sa.Column("avatar_stored_path", sa.String(256), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("masters", "avatar_stored_path")
