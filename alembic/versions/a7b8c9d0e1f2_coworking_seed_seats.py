"""Коворкинг: 4 места для бизнесов, у которых мест нет (docs/coworking/01, 02).

Места создавались только при регистрации владельца, поэтому бизнесы, заведённые
до коворкинга, остались без мест. Мягко удалённые места считаются (is_active=false
остаётся строкой) — такие бизнесы не трогаем.

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-11-09
"""

from alembic import op

revision = "a7b8c9d0e1f2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for i in range(4):
        op.execute(
            f"""
            INSERT INTO seats (business_id, name, sort_order, is_active)
            SELECT b.id, 'Место {i + 1}', {i}, true FROM businesses b
            WHERE NOT EXISTS (SELECT 1 FROM seats s WHERE s.business_id = b.id)
            """
        )


def downgrade() -> None:
    pass
