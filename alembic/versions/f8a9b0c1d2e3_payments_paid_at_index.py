"""Индекс payments.paid_at: отчёты 07 выбирают оплаченные счета за период.

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
Create Date: 2026-11-11
"""

from alembic import op

revision = "f8a9b0c1d2e3"
down_revision = "e7f8a9b0c1d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_payments_paid_at", "payments", ["paid_at"])


def downgrade() -> None:
    op.drop_index("ix_payments_paid_at", table_name="payments")
