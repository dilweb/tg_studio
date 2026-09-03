"""add_business_id_to_bookings

Revision ID: a1b2c3d4e5f6
Revises: f3a4b5c6d7e8
Create Date: 2026-04-22

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "f3a4b5c6d7e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add nullable business_id column
    op.add_column("bookings", sa.Column("business_id", sa.Integer(), nullable=True))

    # Backfill business_id from masters table
    op.execute(
        """
        UPDATE bookings b
        SET business_id = m.business_id
        FROM masters m
        WHERE b.master_id = m.id
        """
    )

    # Now make it non-nullable
    op.alter_column("bookings", "business_id", nullable=False)

    # Add foreign key and index
    op.create_foreign_key(
        "fk_bookings_business_id", "bookings", "businesses", ["business_id"], ["id"]
    )
    op.create_index("ix_bookings_business_id", "bookings", ["business_id"])


def downgrade() -> None:
    op.drop_index("ix_bookings_business_id", table_name="bookings")
    op.drop_constraint("fk_bookings_business_id", "bookings", type_="foreignkey")
    op.drop_column("bookings", "business_id")
