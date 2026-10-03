"""business pricing config

Прайс-конфиг по бизнесу: глобальный коэффициент (%), ставки размера,
коэффициенты стилей/зон, cover-up — калибруется владельцем в разделе «Бизнес».

Revision ID: p6q7r8s9t0v1
Revises: p5q6r7s8t9v0
Create Date: 2026-10-03

"""

import sqlalchemy as sa
from alembic import op

revision = "p6q7r8s9t0v1"
down_revision = "p5q6r7s8t9v0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("businesses", sa.Column("pricing_config", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("businesses", "pricing_config")