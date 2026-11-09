"""Коворкинг: деньги (docs/coworking/04-money-prepay.md).

ledger_entries — append-only проводки счёта мастера (topup/charge/refund/adjust);
остаток баланса вычисляется как Σ amount, отдельного поля нет;
payments.booking_id — привязка счёта к брони места (100% предоплата).

Revision ID: b1c2d3e4f5a6
Revises: z0a1b2c3d4e5
Create Date: 2026-11-08
"""

import sqlalchemy as sa
from alembic import op

revision = "b1c2d3e4f5a6"
down_revision = "z0a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "masters",
        sa.Column("phone", sa.String(32), nullable=True),
    )
    op.add_column(
        "payments",
        sa.Column(
            "booking_id",
            sa.Integer(),
            sa.ForeignKey("bookings.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_payments_booking_id", "payments", ["booking_id"])

    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "business_id", sa.Integer(), sa.ForeignKey("businesses.id"), nullable=False
        ),
        sa.Column("master_id", sa.Integer(), sa.ForeignKey("masters.id"), nullable=False),
        # "topup" | "charge" | "refund" | "adjust"
        sa.Column("kind", sa.String(16), nullable=False),
        # Со знаком: topup/refund > 0, charge < 0, adjust — любой знак
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False, server_default="KZT"),
        sa.Column(
            "payment_id",
            sa.Integer(),
            sa.ForeignKey("payments.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "booking_id",
            sa.Integer(),
            sa.ForeignKey("bookings.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # Зарезервировано под продажи товаров/услуг админом (06)
        sa.Column("sale_id", sa.Integer(), nullable=True),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Index("ix_ledger_entries_business_id", "business_id"),
        sa.Index("ix_ledger_entries_master_id", "master_id"),
        sa.Index("ix_ledger_entries_master_created", "master_id", "created_at"),
    )


def downgrade() -> None:
    op.drop_column("masters", "phone")
    op.drop_table("ledger_entries")
    op.drop_index("ix_payments_booking_id", table_name="payments")
    op.drop_column("payments", "booking_id")
