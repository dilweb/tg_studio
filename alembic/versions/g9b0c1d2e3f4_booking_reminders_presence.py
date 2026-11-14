"""Напоминания в Telegram и подтверждение присутствия мастером.

bookings.payment_reminder_sent_at / booking_reminder_sent_at — дедуп напоминаний
«оплатите до …» и «скоро бронь»; bookings.presence_confirmed_at — мастер нажал
«Подтвердить» в напоминании (аналитика присутствия владельца).

Revision ID: g9b0c1d2e3f4
Revises: f8a9b0c1d2e3
Create Date: 2026-11-14
"""

import sqlalchemy as sa

from alembic import op

revision = "g9b0c1d2e3f4"
down_revision = "f8a9b0c1d2e3"
branch_labels = None
depends_on = None

_COLUMNS = ("presence_confirmed_at", "payment_reminder_sent_at", "booking_reminder_sent_at")


def upgrade() -> None:
    for name in _COLUMNS:
        op.add_column("bookings", sa.Column(name, sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    for name in _COLUMNS:
        op.drop_column("bookings", name)
