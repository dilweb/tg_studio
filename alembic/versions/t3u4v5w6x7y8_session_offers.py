"""session_offers

Распределение записей между мастерами: работа создаётся без мастера
(tattoo_works.master_id → NULL) и уходит по очереди офферов — специализация
∩ стиль, загрузка за месяц, эскалация владельцу. Специализации мастеров —
новая колонка masters.specializations (JSON, пусто = универсал).

Revision ID: t3u4v5w6x7y8
Revises: r2s3t4u5v6w7
Create Date: 2026-10-04

"""

import sqlalchemy as sa
from alembic import op

revision = "t3u4v5w6x7y8"
down_revision = "r2s3t4u5v6w7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "masters",
        sa.Column(
            "specializations",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )
    op.alter_column("tattoo_works", "master_id", existing_type=sa.Integer(), nullable=True)

    op.create_table(
        "session_offers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "business_id", sa.Integer(), sa.ForeignKey("businesses.id"), nullable=False
        ),
        sa.Column(
            "work_id",
            sa.Integer(),
            sa.ForeignKey("tattoo_works.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("master_id", sa.Integer(), sa.ForeignKey("masters.id"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="queued"),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_session_offers_business_id", "session_offers", ["business_id"])
    op.create_index("ix_session_offers_work_id", "session_offers", ["work_id"])
    op.create_index("ix_session_offers_master_id", "session_offers", ["master_id"])


def downgrade() -> None:
    op.drop_index("ix_session_offers_master_id", table_name="session_offers")
    op.drop_index("ix_session_offers_work_id", table_name="session_offers")
    op.drop_index("ix_session_offers_business_id", table_name="session_offers")
    op.drop_table("session_offers")
    # Работы без мастера к моменту даунгрейда быть не должно — очередь
    # должна распределить их до отката (иначе ALTER упадёт на NULL).
    op.alter_column("tattoo_works", "master_id", existing_type=sa.Integer(), nullable=False)
    op.drop_column("masters", "specializations")
