"""refactor_service_pricing_and_booking

Revision ID: a691c7eaf137
Revises: 874d9091408f
Create Date: 2026-03-02

Что меняем:
- services: убираем duration_minutes и prepayment_amount,
            добавляем price_per_hour, prepayment_percent, cancel_deadline_hours
- bookings: добавляем duration_hours, total_amount, cancel_deadline_at
- paymentstatus enum: добавляем значение 'forfeited'
"""
from alembic import op
import sqlalchemy as sa

revision = "a691c7eaf137"
down_revision = "874d9091408f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- services ---
    op.add_column("services", sa.Column("price_per_hour", sa.Numeric(10, 2), nullable=True))
    op.add_column("services", sa.Column("prepayment_percent", sa.Integer(), nullable=False, server_default="50"))
    op.add_column("services", sa.Column("cancel_deadline_hours", sa.Integer(), nullable=False, server_default="3"))

    # Переносим данные: price → price_per_hour
    op.execute("UPDATE services SET price_per_hour = price")
    op.alter_column("services", "price_per_hour", nullable=False)

    op.drop_column("services", "price")
    op.drop_column("services", "duration_minutes")
    op.drop_column("services", "prepayment_amount")

    # --- bookings ---
    op.add_column("bookings", sa.Column("duration_hours", sa.Integer(), nullable=True))
    op.add_column("bookings", sa.Column("total_amount", sa.Numeric(10, 2), nullable=True))
    op.add_column("bookings", sa.Column("cancel_deadline_at", sa.DateTime(), nullable=True))

    # Заполняем дефолты для уже существующих строк
    op.execute("UPDATE bookings SET duration_hours = 1, total_amount = 0")
    op.alter_column("bookings", "duration_hours", nullable=False)
    op.alter_column("bookings", "total_amount", nullable=False)

    # --- paymentstatus enum ---
    # В PostgreSQL нельзя добавить значение в enum внутри транзакции без COMMIT
    op.execute("ALTER TYPE paymentstatus ADD VALUE IF NOT EXISTS 'forfeited'")


def downgrade() -> None:
    # services
    op.add_column("services", sa.Column("price", sa.Numeric(10, 2), nullable=True))
    op.add_column("services", sa.Column("duration_minutes", sa.Integer(), nullable=True))
    op.add_column("services", sa.Column("prepayment_amount", sa.Numeric(10, 2), nullable=True))

    op.execute("UPDATE services SET price = price_per_hour, duration_minutes = 60, prepayment_amount = price_per_hour * prepayment_percent / 100")
    op.alter_column("services", "price", nullable=False)
    op.alter_column("services", "duration_minutes", nullable=False)
    op.alter_column("services", "prepayment_amount", nullable=False)

    op.drop_column("services", "price_per_hour")
    op.drop_column("services", "prepayment_percent")
    op.drop_column("services", "cancel_deadline_hours")

    # bookings
    op.drop_column("bookings", "duration_hours")
    op.drop_column("bookings", "total_amount")
    op.drop_column("bookings", "cancel_deadline_at")
