"""remove_work_schedule

Расписание по дням недели дублировало Google Calendar (мастера ведут своё
время в нём), а единственный живой потребитель — длительность события при
создании записи. Заменяем: masters.default_duration_minutes + явная
длительность в каждой записи.

Revision ID: m7n8o9p0q1r2
Revises: l6m7n8o9p0q1
Create Date: 2026-09-21

"""
from alembic import op
import sqlalchemy as sa

revision = "m7n8o9p0q1r2"
down_revision = "l6m7n8o9p0q1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "masters",
        sa.Column("default_duration_minutes", sa.Integer(), nullable=True),
    )
    # переносим прежние слоты в дефолт мастера — берём максимум, чтобы
    # дефолт не урезал длинные сеансы (час уже не проблема, 3 часа в 60 — проблема)
    op.execute(
        """
        UPDATE masters m
        SET default_duration_minutes = sub.max_slot
        FROM (
            SELECT master_id, MAX(slot_duration_minutes) AS max_slot
            FROM work_schedules
            GROUP BY master_id
        ) sub
        WHERE m.id = sub.master_id
        """
    )
    op.drop_table("work_schedules")


def downgrade() -> None:
    op.create_table(
        "work_schedules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("master_id", sa.Integer(), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("start_time", sa.String(5), nullable=False),
        sa.Column("end_time", sa.String(5), nullable=False),
        sa.Column("slot_duration_minutes", sa.Integer(), nullable=False, server_default="60"),
        sa.ForeignKeyConstraint(["master_id"], ["masters.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("master_id", "weekday", name="uq_master_weekday"),
    )
    op.drop_column("masters", "default_duration_minutes")
    # по-дневный график восстановить неоткуда — таблица вернётся пустой,
    # дефолтную длительность мастеров этот downgrade теряет безвозвратно
