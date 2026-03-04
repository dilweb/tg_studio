"""add_business

Revision ID: 0e4fe979c91b
Revises: 9d6cf7cbc1e8
Create Date: 2026-03-02

"""
from alembic import op
import sqlalchemy as sa

revision = "0e4fe979c91b"
down_revision = "9d6cf7cbc1e8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "businesses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("phone", sa.String(20), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_telegram_id"),
    )

    op.add_column("masters", sa.Column("business_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_masters_business", "masters", "businesses", ["business_id"], ["id"])

    op.add_column("services", sa.Column("business_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_services_business", "services", "businesses", ["business_id"], ["id"])


def downgrade() -> None:
    op.drop_constraint("fk_services_business", "services", type_="foreignkey")
    op.drop_column("services", "business_id")
    op.drop_constraint("fk_masters_business", "masters", type_="foreignkey")
    op.drop_column("masters", "business_id")
    op.drop_table("businesses")
