"""rename quoted_cost to recommended_price

Смысл поля изменился: это не «озвучено клиенту», а рекомендуемая цена,
рассчитанная по прайсу из параметров работы — для счёта на предоплату.
Фактически договорённая сумма остаётся в cost.

Revision ID: p5q6r7s8t9v0
Revises: p4q5r6s7t8u9
Create Date: 2026-10-03

"""

from alembic import op

revision = "p5q6r7s8t9v0"
down_revision = "p4q5r6s7t8u9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "tattoo_sessions", "quoted_cost", new_column_name="recommended_price"
    )


def downgrade() -> None:
    op.alter_column(
        "tattoo_sessions", "recommended_price", new_column_name="quoted_cost"
    )