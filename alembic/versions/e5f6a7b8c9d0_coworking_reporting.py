"""Коворкинг: индексы и события возврата для отчётности (07).

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-11-08
"""

import sqlalchemy as sa
from alembic import op

revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Возврат гостевой продажи без брони имеет sale_id в details, но booking_id=NULL.
    op.alter_column("booking_events", "booking_id", existing_type=sa.Integer(), nullable=True)
    op.create_index("ix_bookings_business_start", "bookings", ["business_id", "start"])
    op.create_index("ix_payments_business_paid_at", "payments", ["business_id", "paid_at"])
    op.create_index("ix_payouts_business_created_at", "payouts", ["business_id", "created_at"])
    op.create_index("ix_supply_movements_business_created_at", "supply_movements", ["business_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_supply_movements_business_created_at", table_name="supply_movements")
    op.drop_index("ix_payouts_business_created_at", table_name="payouts")
    op.drop_index("ix_payments_business_paid_at", table_name="payments")
    op.drop_index("ix_bookings_business_start", table_name="bookings")
    op.execute("DELETE FROM booking_events WHERE booking_id IS NULL")
    op.alter_column("booking_events", "booking_id", existing_type=sa.Integer(), nullable=False)
