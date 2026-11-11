"""Коворкинг: удаление legacy тату-модели (docs/coworking/08).

Дропаем работы/сеансы/офферы/чаты/AI/клиентов и связи на них. Dev-среда,
данные тестовые — без сохранения (downgrade не поддерживается).

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-11-09
"""

from alembic import op

revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None

LEGACY_TABLES = [
    "session_offers",
    "tattoo_files",
    "tattoo_sessions",
    "tattoo_works",
    "chat_messages",
    "chat_assignments",
    "ai_messages",
    "ai_conversations",
    "clients",
]
LEGACY_ENUMS = [
    "sessionofferstatus",
    "tattoofilekind",
    "tattooprojectstatus",
    "tattooworkstatus",
    "chatdirection",
    "chatassignmentstatus",
    "clientsource",
]


def upgrade() -> None:
    # Колонки со ссылками на работы/сеансы (FK уходят вместе с колонкой)
    op.drop_column("payments", "work_id")
    op.drop_column("payments", "session_id")
    op.drop_column("supply_movements", "work_id")
    op.drop_column("supply_movements", "session_id")
    op.drop_column("businesses", "pricing_config")
    op.drop_column("masters", "specializations")
    for table in LEGACY_TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    for enum in LEGACY_ENUMS:
        op.execute(f"DROP TYPE IF EXISTS {enum}")


def downgrade() -> None:
    raise NotImplementedError("Legacy-данные удалены безвозвратно (dev-среда, 08-cleanup)")
