"""Коворкинг: места, часы студии, блокировки, брони + история (docs/coworking/02).

seats — рабочие места (MVP: 4, сущность допускает N); studio_hours — часы
работы по дням недели; seat_blocks — блокировки мест/студии (занятые
интервалы); bookings — брони мест мастерами; booking_events — append-only
история изменений брони. Защита от пересечений активных броней одного места —
advisory lock по seat_id на уровне приложения (db/locks.lock_seat_slot),
индексы (seat_id, start) и (master_id, start) — под проверку пересечений.

Revision ID: y9z0a1b2c3d4
Revises: x8y9z0a1b2c3
Create Date: 2026-11-08
"""

import sqlalchemy as sa
from alembic import op

revision = "y9z0a1b2c3d4"
down_revision = "x8y9z0a1b2c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Настройки коворкинга (лимиты мест, шаг/мин. длительность, буфер, TTL hold)
    # — JSON на businesses, чтобы 03 расширял без новой миграции
    op.add_column(
        "businesses",
        sa.Column("coworking_settings", sa.JSON(), nullable=True),
    )

    op.create_table(
        "seats",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "business_id", sa.Integer(), sa.ForeignKey("businesses.id"), nullable=False
        ),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("equipment", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_seats_business_id", "seats", ["business_id"])

    op.create_table(
        "studio_hours",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "business_id", sa.Integer(), sa.ForeignKey("businesses.id"), nullable=False
        ),
        # 0 = понедельник … 6 = воскресенье
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("open_time", sa.Time(), nullable=False),
        sa.Column("close_time", sa.Time(), nullable=False),
    )
    op.create_index("ix_studio_hours_business_id", "studio_hours", ["business_id"])

    op.create_table(
        "seat_blocks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "business_id", sa.Integer(), sa.ForeignKey("businesses.id"), nullable=False
        ),
        # NULL = блокировка всей студии (все места)
        sa.Column("seat_id", sa.Integer(), sa.ForeignKey("seats.id"), nullable=True),
        sa.Column("start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(256), nullable=False),
        sa.Column(
            "created_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_seat_blocks_business_id", "seat_blocks", ["business_id"])
    # Поиск пересечений блокировок по месту в диапазоне дат
    op.create_index("ix_seat_blocks_seat_start", "seat_blocks", ["seat_id", "start"])

    op.create_table(
        "bookings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "business_id", sa.Integer(), sa.ForeignKey("businesses.id"), nullable=False
        ),
        sa.Column("seat_id", sa.Integer(), sa.ForeignKey("seats.id"), nullable=False),
        sa.Column("master_id", sa.Integer(), sa.ForeignKey("masters.id"), nullable=False),
        sa.Column("start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="ожидает_оплаты"),
        sa.Column("hold_deadline", sa.DateTime(timezone=True), nullable=True),
        # Снимок расчёта цены (03/04) — смена тарифов не трогает существующие брони
        sa.Column("price_snapshot", sa.JSON(), nullable=True),
        sa.Column("paid_amount", sa.Numeric(12, 2), nullable=False, server_default="0"),
        # Фактические отметки (05)
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_bookings_business_id", "bookings", ["business_id"])
    op.create_index("ix_bookings_seat_start", "bookings", ["seat_id", "start"])
    op.create_index("ix_bookings_master_start", "bookings", ["master_id", "start"])

    op.create_table(
        "booking_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "business_id", sa.Integer(), sa.ForeignKey("businesses.id"), nullable=False
        ),
        sa.Column("booking_id", sa.Integer(), sa.ForeignKey("bookings.id"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_booking_events_business_id", "booking_events", ["business_id"])
    op.create_index("ix_booking_events_booking_id", "booking_events", ["booking_id"])
    op.create_index("ix_booking_events_created_at", "booking_events", ["created_at"])


def downgrade() -> None:
    op.drop_column("businesses", "coworking_settings")
    op.drop_index("ix_booking_events_created_at", table_name="booking_events")
    op.drop_index("ix_booking_events_booking_id", table_name="booking_events")
    op.drop_index("ix_booking_events_business_id", table_name="booking_events")
    op.drop_table("booking_events")
    op.drop_index("ix_bookings_master_start", table_name="bookings")
    op.drop_index("ix_bookings_seat_start", table_name="bookings")
    op.drop_index("ix_bookings_business_id", table_name="bookings")
    op.drop_table("bookings")
    op.drop_index("ix_seat_blocks_seat_start", table_name="seat_blocks")
    op.drop_index("ix_seat_blocks_business_id", table_name="seat_blocks")
    op.drop_table("seat_blocks")
    op.drop_index("ix_studio_hours_business_id", table_name="studio_hours")
    op.drop_table("studio_hours")
    op.drop_index("ix_seats_business_id", table_name="seats")
    op.drop_table("seats")
