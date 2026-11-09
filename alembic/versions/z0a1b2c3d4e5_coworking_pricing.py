"""Коворкинг: ценовые правила, скидки, флаг резидента мастера (docs/coworking/03).

price_rules — ставка за час на интервал времени суток (область: место или все,
дни: все/будни/выходные/конкретная дата; приоритет при пересечении правил);
discount_rules — скидки (% или фикс, резидент / от N часов);
masters.is_resident — флаг резидента для resident-скидок.

Revision ID: z0a1b2c3d4e5
Revises: y9z0a1b2c3d4
Create Date: 2026-11-08
"""

import sqlalchemy as sa
from alembic import op

revision = "z0a1b2c3d4e5"
down_revision = "y9z0a1b2c3d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "masters",
        sa.Column("is_resident", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    op.create_table(
        "price_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "business_id", sa.Integer(), sa.ForeignKey("businesses.id"), nullable=False
        ),
        # NULL = все места студии
        sa.Column("seat_id", sa.Integer(), sa.ForeignKey("seats.id"), nullable=True),
        sa.Column("name", sa.String(128), nullable=False),
        # "все" | "будни" | "выходные" | "дата"
        sa.Column("day_kind", sa.String(16), nullable=False, server_default="все"),
        # Только для day_kind="дата" — локальная дата студии
        sa.Column("specific_date", sa.Date(), nullable=True),
        # Интервал времени суток [time_from, time_to), локальное время студии
        sa.Column("time_from", sa.Time(), nullable=False),
        sa.Column("time_to", sa.Time(), nullable=False),
        # Ставка за час
        sa.Column("hourly_rate", sa.Numeric(12, 2), nullable=False),
        # Чем выше, тем раньше применяется при пересечении правил
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_price_rules_business_id", "price_rules", ["business_id"])
    op.create_index("ix_price_rules_seat", "price_rules", ["seat_id"])

    op.create_table(
        "discount_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "business_id", sa.Integer(), sa.ForeignKey("businesses.id"), nullable=False
        ),
        # NULL = все места студии
        sa.Column("seat_id", sa.Integer(), sa.ForeignKey("seats.id"), nullable=True),
        sa.Column("name", sa.String(128), nullable=False),
        # "resident" | "min_hours"
        sa.Column("kind", sa.String(16), nullable=False),
        # Для kind="min_hours": порог длительности в часах
        sa.Column("min_hours", sa.Integer(), nullable=True),
        # Доля от base (0..1) — ровно одно из percent/fixed_amount задано
        sa.Column("percent", sa.Numeric(5, 4), nullable=True),
        sa.Column("fixed_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_discount_rules_business_id", "discount_rules", ["business_id"])
    op.create_index("ix_discount_rules_seat", "discount_rules", ["seat_id"])


def downgrade() -> None:
    op.drop_index("ix_discount_rules_seat", table_name="discount_rules")
    op.drop_index("ix_discount_rules_business_id", table_name="discount_rules")
    op.drop_table("discount_rules")
    op.drop_index("ix_price_rules_seat", table_name="price_rules")
    op.drop_index("ix_price_rules_business_id", table_name="price_rules")
    op.drop_table("price_rules")
    op.drop_column("masters", "is_resident")
