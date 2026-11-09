"""Коворкинг: склад и продажи (docs/coworking/06-stock-sales.md).

- supplies += purchase_price/sale_price (цена закупки и продажи);
- supply_movements += actor_user_id/sale_id/booking_id (автор, продажа, бронь);
- sales + sale_items — продажи товаров: позиции фиксируют цены на момент продажи;
- payments.sale_id, ledger_entries.sale_id, upsells.sale_id → sales.id.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-11-08
"""

import sqlalchemy as sa
from alembic import op

revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Цены позиции склада
    op.add_column("supplies", sa.Column("purchase_price", sa.Numeric(12, 2), nullable=True))
    op.add_column("supplies", sa.Column("sale_price", sa.Numeric(12, 2), nullable=True))

    # Движения: автор + ссылки на продажу/бронь
    op.add_column(
        "supply_movements",
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
    )
    # sale_id добавим после создания sales (FK требует существующей таблицы)
    op.add_column(
        "supply_movements",
        sa.Column(
            "booking_id",
            sa.Integer(),
            sa.ForeignKey("bookings.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # Продажи
    op.create_table(
        "sales",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "business_id", sa.Integer(), sa.ForeignKey("businesses.id"), nullable=False
        ),
        sa.Column("master_id", sa.Integer(), sa.ForeignKey("masters.id"), nullable=True),
        sa.Column("buyer_name", sa.String(256), nullable=True),
        sa.Column("buyer_phone", sa.String(32), nullable=True),
        sa.Column("total_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False, server_default="KZT"),
        # balance | cash | card
        sa.Column("payment_method", sa.String(16), nullable=False),
        # оплачена | отменена | возвращена
        sa.Column("status", sa.String(16), nullable=False, server_default="оплачена"),
        sa.Column(
            "admin_id", sa.Integer(), sa.ForeignKey("studio_admins.id"), nullable=True
        ),
        sa.Column(
            "created_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False
        ),
        sa.Column(
            "booking_id",
            sa.Integer(),
            sa.ForeignKey("bookings.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_sales_business_admin", "sales", ["business_id", "admin_id"])
    op.create_index("ix_sales_business_master", "sales", ["business_id", "master_id"])
    op.create_index("ix_sales_created_at", "sales", ["created_at"])

    op.create_table(
        "sale_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sale_id", sa.Integer(), sa.ForeignKey("sales.id"), nullable=False),
        sa.Column("supply_id", sa.Integer(), sa.ForeignKey("supplies.id"), nullable=False),
        sa.Column("qty", sa.Numeric(12, 2), nullable=False),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("unit_cost", sa.Numeric(12, 2), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
    )
    op.create_index("ix_sale_items_sale_id", "sale_items", ["sale_id"])

    # Внешние ключи supply_movements.sale_id после создания sales
    op.add_column("supply_movements", sa.Column("sale_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_supply_movements_sale_id",
        "supply_movements",
        "sales",
        ["sale_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_supply_movements_sale_id", "supply_movements", ["sale_id"])

    # Привязка денег к продаже
    op.add_column("payments", sa.Column("sale_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_payments_sale_id", "payments", "sales", ["sale_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index("ix_payments_sale_id", "payments", ["sale_id"])

    # Колонка зарезервирована в 04, здесь только добавляем настоящий FK.
    op.create_foreign_key(
        "fk_ledger_entries_sale_id",
        "ledger_entries",
        "sales",
        ["sale_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Колонка зарезервирована в 05, здесь только добавляем настоящий FK.
    op.create_foreign_key(
        "fk_upsells_sale_id", "upsells", "sales", ["sale_id"], ["id"], ondelete="SET NULL"
    )


def downgrade() -> None:
    op.drop_constraint("fk_upsells_sale_id", "upsells", type_="foreignkey")
    op.drop_column("upsells", "sale_id")

    op.drop_constraint("fk_ledger_entries_sale_id", "ledger_entries", type_="foreignkey")
    op.drop_column("ledger_entries", "sale_id")

    op.drop_index("ix_payments_sale_id", table_name="payments")
    op.drop_constraint("fk_payments_sale_id", "payments", type_="foreignkey")
    op.drop_column("payments", "sale_id")

    op.drop_index("ix_supply_movements_sale_id", table_name="supply_movements")
    op.drop_constraint("fk_supply_movements_sale_id", "supply_movements", type_="foreignkey")

    op.drop_index("ix_sale_items_sale_id", table_name="sale_items")
    op.drop_table("sale_items")

    op.drop_index("ix_sales_created_at", table_name="sales")
    op.drop_index("ix_sales_business_master", table_name="sales")
    op.drop_index("ix_sales_business_admin", table_name="sales")
    op.drop_table("sales")

    op.drop_column("supply_movements", "booking_id")
    op.drop_column("supply_movements", "sale_id")
    op.drop_column("supply_movements", "actor_user_id")

    op.drop_column("supplies", "sale_price")
    op.drop_column("supplies", "purchase_price")
