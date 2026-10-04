"""add_payments

Платежи по тату-работам: счёт (pending) и оплата (paid) как факты-события;
договорная цена работы — новая колонка tattoo_works.contract_price. Баланс
«сколько клиент должен» нигде не хранится — вычисляется как
contract_price − Σ платежей со статусом paid.

Revision ID: r2s3t4u5v6w7
Revises: q1r2s3t4u5v6
Create Date: 2026-10-03

"""

import sqlalchemy as sa
from alembic import op

revision = "r2s3t4u5v6w7"
down_revision = "q1r2s3t4u5v6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tattoo_works",
        sa.Column("contract_price", sa.Numeric(12, 2), nullable=True),
    )

    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "business_id", sa.Integer(), sa.ForeignKey("businesses.id"), nullable=False
        ),
        sa.Column(
            "work_id",
            sa.Integer(),
            sa.ForeignKey("tattoo_works.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "session_id",
            sa.Integer(),
            sa.ForeignKey("tattoo_sessions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("external_invoice_id", sa.String(128), nullable=True),
        sa.Column("payment_url", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("master_id", sa.Integer(), sa.ForeignKey("masters.id"), nullable=True),
        sa.Column(
            "confirmed_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True
        ),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_payments_business_id", "payments", ["business_id"])
    op.create_index("ix_payments_work_id", "payments", ["work_id"])


def downgrade() -> None:
    op.drop_index("ix_payments_work_id", table_name="payments")
    op.drop_index("ix_payments_business_id", table_name="payments")
    op.drop_table("payments")
    op.drop_column("tattoo_works", "contract_price")
