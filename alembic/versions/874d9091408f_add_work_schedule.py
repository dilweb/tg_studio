"""add_work_schedule

Revision ID: 874d9091408f
Revises: 0e4fe979c91b
Create Date: 2026-03-02

"""
from alembic import op
import sqlalchemy as sa

revision = "874d9091408f"
down_revision = "0e4fe979c91b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "work_schedules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("master_id", sa.Integer(), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("start_time", sa.String(5), nullable=False),
        sa.Column("end_time", sa.String(5), nullable=False),
        sa.Column("slot_duration_minutes", sa.Integer(), nullable=False, server_default="60"),
        sa.ForeignKeyConstraint(["master_id"], ["masters.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("master_id", "weekday", name="uq_master_weekday"),
    )


def downgrade() -> None:
    op.drop_table("work_schedules")
