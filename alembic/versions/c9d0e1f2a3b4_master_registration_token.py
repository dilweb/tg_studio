"""master_registration_token

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-03-07

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, Sequence[str], None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "masters",
        sa.Column("registration_token", sa.String(64), nullable=True),
    )
    op.create_unique_constraint(
        "masters_registration_token_key",
        "masters",
        ["registration_token"],
    )


def downgrade() -> None:
    op.drop_constraint("masters_registration_token_key", "masters", type_="unique")
    op.drop_column("masters", "registration_token")
