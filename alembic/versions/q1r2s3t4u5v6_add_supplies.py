"""add_supplies

Склад расходников: позиции с текущим остатком (supplies) и журнал движений
(supply_movements) — списания мастеров, закупки, ревизии. Списание может быть
привязано к тату-работе/сеансу (work_id/session_id, nullable) — база для
будущей себестоимости сеанса.

Revision ID: q1r2s3t4u5v6
Revises: p6q7r8s9t0v1
Create Date: 2026-10-03

"""

import sqlalchemy as sa
from alembic import op

revision = "q1r2s3t4u5v6"
down_revision = "p6q7r8s9t0v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "supplies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "business_id", sa.Integer(), sa.ForeignKey("businesses.id"), nullable=False
        ),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("unit", sa.String(16), nullable=False),
        sa.Column("quantity", sa.Numeric(12, 2), nullable=False),
        sa.Column("min_quantity", sa.Numeric(12, 2), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_supplies_business_id", "supplies", ["business_id"])

    op.create_table(
        "supply_movements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "business_id", sa.Integer(), sa.ForeignKey("businesses.id"), nullable=False
        ),
        sa.Column("supply_id", sa.Integer(), sa.ForeignKey("supplies.id"), nullable=False),
        sa.Column("delta", sa.Numeric(12, 2), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("master_id", sa.Integer(), sa.ForeignKey("masters.id"), nullable=True),
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
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_supply_movements_business_id", "supply_movements", ["business_id"])
    op.create_index("ix_supply_movements_supply_id", "supply_movements", ["supply_id"])


def downgrade() -> None:
    op.drop_index("ix_supply_movements_supply_id", table_name="supply_movements")
    op.drop_index("ix_supply_movements_business_id", table_name="supply_movements")
    op.drop_table("supply_movements")
    op.drop_index("ix_supplies_business_id", table_name="supplies")
    op.drop_table("supplies")
