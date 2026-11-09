"""Коворкинг: администраторы, смены, апсейлы, выплаты (docs/coworking/05-admin-upsells.md).

- PG enum userrole += 'admin' (нетранзакционный ALTER TYPE ADD VALUE, как в u5v6w7x8y9z0);
- studio_admins — администраторы студии (назначает владелец по telegram_id);
- shifts — смены администраторов (план + факт + утверждённые часы);
- upsells — апсейл-записи: привязка продажи к автору-админу (sale_id зарезервирован под 06);
- payouts — доначисления админам (начислено/выплачено);
- bookings.requires_confirmation + visit_* — бронь вне покрытия и факт посещения.

Revision ID: c3d4e5f6a7b8
Revises: b1c2d3e4f5a6
Create Date: 2026-11-08
"""

import sqlalchemy as sa
from alembic import op

revision = "c3d4e5f6a7b8"
down_revision = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Роль admin в PG enum — первым стейтментом: ALTER TYPE ADD VALUE нельзя
    # внутри транзакции с другими обращениями к типу (см. u5v6w7x8y9z0)
    op.execute("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'admin'")

    op.create_table(
        "studio_admins",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "business_id", sa.Integer(), sa.ForeignKey("businesses.id"), nullable=False
        ),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, unique=True
        ),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("full_name", sa.String(256), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_studio_admins_business_id", "studio_admins", ["business_id"])
    op.create_index(
        "ix_studio_admins_telegram_id", "studio_admins", ["telegram_id"]
    )

    op.create_table(
        "shifts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "business_id", sa.Integer(), sa.ForeignKey("businesses.id"), nullable=False
        ),
        sa.Column(
            "admin_id", sa.Integer(), sa.ForeignKey("studio_admins.id"), nullable=False
        ),
        # План
        sa.Column("planned_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("planned_end", sa.DateTime(timezone=True), nullable=False),
        # Факт
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        # Утверждённые оплачиваемые часы (утверждает владелец)
        sa.Column("approved_hours", sa.Numeric(6, 2), nullable=True),
        sa.Column("approved_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_shifts_business_admin", "shifts", ["business_id", "admin_id"])
    op.create_index("ix_shifts_admin_start", "shifts", ["admin_id", "planned_start"])

    op.create_table(
        "upsells",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "business_id", sa.Integer(), sa.ForeignKey("businesses.id"), nullable=False
        ),
        sa.Column(
            "admin_id", sa.Integer(), sa.ForeignKey("studio_admins.id"), nullable=False
        ),
        # Продажа (06): sale_id — ссылка на sale; пока 06 нет — NULL, а детали в note
        sa.Column("sale_id", sa.Integer(), nullable=True),
        # Кому продано: мастер (sale мастеру) или разовый контакт клиента мастера
        sa.Column("master_id", sa.Integer(), sa.ForeignKey("masters.id"), nullable=True),
        sa.Column("buyer_name", sa.String(256), nullable=True),
        sa.Column("buyer_phone", sa.String(32), nullable=True),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False, server_default="KZT"),
        sa.Column("booking_id", sa.Integer(), sa.ForeignKey("bookings.id", ondelete="SET NULL"), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_upsells_business_admin", "upsells", ["business_id", "admin_id"])
    op.create_index("ix_upsells_admin_created", "upsells", ["admin_id", "created_at"])

    op.create_table(
        "payouts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "business_id", sa.Integer(), sa.ForeignKey("businesses.id"), nullable=False
        ),
        sa.Column(
            "admin_id", sa.Integer(), sa.ForeignKey("studio_admins.id"), nullable=False
        ),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False, server_default="KZT"),
        # "accrued" | "paid"
        sa.Column("status", sa.String(16), nullable=False, server_default="accrued"),
        # Ручное разовое доначисление или % от апсейлов за период
        sa.Column("percent", sa.Numeric(5, 4), nullable=True),
        sa.Column("period_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("period_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("paid_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_payouts_business_admin", "payouts", ["business_id", "admin_id"])
    op.create_index("ix_payouts_admin_status", "payouts", ["admin_id", "status"])

    op.add_column(
        "bookings",
        sa.Column("requires_confirmation", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "bookings",
        sa.Column("confirmed_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
    )
    op.add_column(
        "bookings",
        sa.Column("visit_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "bookings",
        sa.Column("visit_ended_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("bookings", "visit_ended_at")
    op.drop_column("bookings", "visit_started_at")
    op.drop_column("bookings", "confirmed_by_user_id")
    op.drop_column("bookings", "requires_confirmation")

    op.drop_index("ix_payouts_admin_status", table_name="payouts")
    op.drop_index("ix_payouts_business_admin", table_name="payouts")
    op.drop_table("payouts")

    op.drop_index("ix_upsells_admin_created", table_name="upsells")
    op.drop_index("ix_upsells_business_admin", table_name="upsells")
    op.drop_table("upsells")

    op.drop_index("ix_shifts_admin_start", table_name="shifts")
    op.drop_index("ix_shifts_business_admin", table_name="shifts")
    op.drop_table("shifts")

    op.drop_index("ix_studio_admins_telegram_id", table_name="studio_admins")
    op.drop_index("ix_studio_admins_business_id", table_name="studio_admins")
    op.drop_table("studio_admins")

    # userrole.admin не удаляем: Postgres не умеет убирать значения enum
    # без пересоздания типа, а лишнее значение безвредно (как в u5v6w7x8y9z0).
