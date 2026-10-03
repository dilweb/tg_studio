"""work size in cm

Размер работы — не enum (маленький/средний/большой), а два числа:
длина и высота в сантиметрах. Существующие строки бэкфиллим
приблизительно (10/20/30 см), владелец поправит при необходимости.

Revision ID: p2q3r4s5t6u7
Revises: p1q2r3s4t5u6
Create Date: 2026-10-03

"""

import sqlalchemy as sa
from alembic import op

revision = "p2q3r4s5t6u7"
down_revision = "p1q2r3s4t5u6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tattoo_works", sa.Column("size_length_cm", sa.Numeric(6, 1), nullable=True))
    op.add_column("tattoo_works", sa.Column("size_height_cm", sa.Numeric(6, 1), nullable=True))
    op.execute(
        """
        UPDATE tattoo_works
        SET size_length_cm = CASE size WHEN 'маленький' THEN 10.0 WHEN 'средний' THEN 20.0 ELSE 30.0 END,
            size_height_cm = CASE size WHEN 'маленький' THEN 10.0 WHEN 'средний' THEN 20.0 ELSE 30.0 END
        """
    )
    op.drop_column("tattoo_works", "size")
    op.alter_column("tattoo_works", "size_length_cm", nullable=False)
    op.alter_column("tattoo_works", "size_height_cm", nullable=False)


def downgrade() -> None:
    op.add_column("tattoo_works", sa.Column("size", sa.String(64), nullable=True))
    op.execute(
        """
        UPDATE tattoo_works
        SET size = CASE WHEN size_length_cm < 15 THEN 'маленький'
                        WHEN size_length_cm < 25 THEN 'средний'
                        ELSE 'большой' END
        """
    )
    op.alter_column("tattoo_works", "size", nullable=False)
    op.drop_column("tattoo_works", "size_height_cm")
    op.drop_column("tattoo_works", "size_length_cm")
