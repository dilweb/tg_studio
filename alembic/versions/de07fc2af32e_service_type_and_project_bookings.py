"""service_type_and_project_bookings

Revision ID: de07fc2af32e
Revises: a691c7eaf137
Create Date: 2026-03-03 07:48:14.024165

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'de07fc2af32e'
down_revision: Union[str, Sequence[str], None] = 'a691c7eaf137'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Bookings: project support
    op.add_column('bookings', sa.Column('project_deadline', sa.DateTime(), nullable=True))
    op.alter_column('bookings', 'slot_id', existing_type=sa.INTEGER(), nullable=True)
    op.alter_column('bookings', 'duration_hours', existing_type=sa.INTEGER(), nullable=True)

    # Services: price_per_hour -> price, add service_type
    op.execute("CREATE TYPE servicetype AS ENUM ('appointment', 'project')")
    op.add_column('services', sa.Column('service_type', sa.Enum('appointment', 'project', name='servicetype'), nullable=True))
    op.execute("UPDATE services SET service_type = 'appointment'")
    op.alter_column('services', 'service_type', nullable=False, server_default='appointment')

    op.add_column('services', sa.Column('price', sa.Numeric(precision=10, scale=2), nullable=True))
    op.execute("UPDATE services SET price = price_per_hour")
    op.alter_column('services', 'price', nullable=False)
    op.drop_column('services', 'price_per_hour')
    op.alter_column('services', 'service_type', server_default=None)


def downgrade() -> None:
    """Downgrade schema."""
    # Services: revert to price_per_hour
    op.add_column('services', sa.Column('price_per_hour', sa.Numeric(precision=10, scale=2), nullable=True))
    op.execute("UPDATE services SET price_per_hour = price")
    op.alter_column('services', 'price_per_hour', nullable=False)
    op.drop_column('services', 'price')
    op.drop_column('services', 'service_type')
    op.execute("DROP TYPE servicetype")

    # Bookings: revert
    op.alter_column('bookings', 'slot_id', existing_type=sa.INTEGER(), nullable=False)
    op.alter_column('bookings', 'duration_hours', existing_type=sa.INTEGER(), nullable=False)
    op.drop_column('bookings', 'project_deadline')
