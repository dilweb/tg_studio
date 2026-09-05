"""add_google_calendar_fields

Revision ID: g1h2i3j4k5l6
Revises: a1b2c3d4e5f6
Create Date: 2026-08-30

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "g1h2i3j4k5l6"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Business: Google Calendar credentials
    op.add_column("businesses", sa.Column("google_calendar_credentials_json", sa.Text(), nullable=True))
    op.add_column("businesses", sa.Column("google_calendar_email", sa.String(256), nullable=True))

    # Booking: Google Calendar event ID
    op.add_column("bookings", sa.Column("google_event_id", sa.String(256), nullable=True))


def downgrade() -> None:
    op.drop_column("bookings", "google_event_id")
    op.drop_column("businesses", "google_calendar_email")
    op.drop_column("businesses", "google_calendar_credentials_json")