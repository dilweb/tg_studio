"""remove services catalog

Каталог услуг (Service + MasterService) удалён: запись идёт через AI-ассистента,
тип работы хранится в Google Calendar extendedProperties свободной строкой.
Легаси-колонка bookings.service_id тоже удаляется.

Revision ID: k5l6m7n8o9p0
Revises: j4k5l6m7n8o9
Create Date: 2026-09-19 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "k5l6m7n8o9p0"
down_revision: Union[str, Sequence[str], None] = "j4k5l6m7n8o9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # FK master_services → masters/services умирают вместе с таблицей (безымянные, авто-нейминг Postgres)
    op.drop_table("master_services")
    # Безымянный FK bookings.service_id → services.id Postgres снесёт вместе с колонкой
    op.drop_column("bookings", "service_id")
    op.drop_table("services")
    # enum servicetype переживает дроп таблиц (создан в de07fc2af32e, колонка снята в 2fb8e778874a)
    op.execute("DROP TYPE IF EXISTS servicetype")


def downgrade() -> None:
    # Best-effort: данные каталога теряются безвозвратно.
    # Схема как на head (price Numeric после de07fc2af32e; business_id NOT NULL после 102961f3dfe1).
    op.create_table(
        "services",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("business_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("price", sa.Numeric(10, 2), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "master_services",
        sa.Column("master_id", sa.Integer(), nullable=False),
        sa.Column("service_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["master_id"], ["masters.id"]),
        sa.ForeignKeyConstraint(["service_id"], ["services.id"]),
        sa.PrimaryKeyConstraint("master_id", "service_id"),
    )
    # nullable: легаси-записи не имеют услуг, на которые можно сослаться
    op.add_column("bookings", sa.Column("service_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_bookings_service_id", "bookings", "services", ["service_id"], ["id"])
