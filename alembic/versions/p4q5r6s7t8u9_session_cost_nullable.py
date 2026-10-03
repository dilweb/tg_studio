"""session cost nullable

cost — фактически взятая сумма, она известна только после сеанса.
Сеанс сначала планируется (дата/озвученная цена), поэтому cost
должен быть nullable, как и quoted_cost.

Revision ID: p4q5r6s7t8u9
Revises: p3q4r5s6t7u8
Create Date: 2026-10-03

"""

import sqlalchemy as sa
from alembic import op

revision = "p4q5r6s7t8u9"
down_revision = "p3q4r5s6t7u8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "tattoo_sessions", "cost", existing_type=sa.Numeric(10, 2), nullable=True
    )


def downgrade() -> None:
    # Обратное несовместимо с фактами: заполняем пропуски нулём, чтобы поставить NOT NULL
    op.execute("UPDATE tattoo_sessions SET cost = 0 WHERE cost IS NULL")
    op.alter_column(
        "tattoo_sessions", "cost", existing_type=sa.Numeric(10, 2), nullable=False
    )