"""Ledger: один topup на счёт (уникальный частичный индекс).

Дедуп вебхука/sync/ручной отметки на уровне БД: вторая проводка topup с тем же
payment_id невозможна. Если в таблице уже есть дубли, индекс не создастся —
перед накаткой посмотрите:
  SELECT payment_id, count(*) FROM ledger_entries
  WHERE kind = 'topup' GROUP BY payment_id HAVING count(*) > 1;

Revision ID: e7f8a9b0c1d2
Revises: a7b8c9d0e1f2
Create Date: 2026-11-11
"""

import sqlalchemy as sa
from alembic import op

revision = "e7f8a9b0c1d2"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_ledger_entries_topup_payment",
        "ledger_entries",
        ["payment_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'topup'"),
    )


def downgrade() -> None:
    op.drop_index("uq_ledger_entries_topup_payment", table_name="ledger_entries")
