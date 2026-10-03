"""drop_legacy_bookings_tables

Легаси-бронирования вычищены: реальная запись живёт только в Google
Calendar (booking-модуль удалён, сеансы ведутся через tattoo-модуль).
Локальные таблицы bookings/time_slots и enum bookingstatus никем не
читаются — дропаем.

Revision ID: o0p1q2r3s4t5
Revises: n8o9p0q1r2s3
Create Date: 2026-10-03

"""
import sqlalchemy as sa
from alembic import op

revision = "o0p1q2r3s4t5"
down_revision = "n8o9p0q1r2s3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("bookings")
    op.drop_table("time_slots")
    op.execute("DROP TYPE IF EXISTS bookingstatus")


def downgrade() -> None:
    op.execute("CREATE TYPE bookingstatus AS ENUM ('pending', 'confirmed', 'cancelled', 'completed')")
    op.create_table(
        "time_slots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("master_id", sa.Integer(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_available", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.ForeignKeyConstraint(["master_id"], ["masters.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_time_slots_master_starts", "time_slots", ["master_id", "starts_at"])
    op.create_table(
        "bookings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("client_id", sa.Integer(), nullable=False),
        sa.Column("master_id", sa.Integer(), nullable=False),
        sa.Column("slot_id", sa.Integer(), nullable=True),
        sa.Column("business_id", sa.Integer(), nullable=False),
        sa.Column(
            "status", sa.Enum("pending", "confirmed", "cancelled", "completed", name="bookingstatus"),
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("duration_hours", sa.Integer(), nullable=True),
        sa.Column("total_amount", sa.Numeric(10, 2), nullable=True),
        sa.Column("cancel_deadline_at", sa.DateTime(), nullable=True),
        sa.Column("project_deadline", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("google_event_id", sa.String(256), nullable=True),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"]),
        sa.ForeignKeyConstraint(["master_id"], ["masters.id"]),
        sa.ForeignKeyConstraint(["slot_id"], ["time_slots.id"]),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    # содержимое таблиц восстановить неоткуда — вернутся пустые