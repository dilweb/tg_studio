"""add_freedom_pay_to_business_and_payment

Revision ID: f1a2b3c4d5e6
Revises: 102961f3dfe1
Create Date: 2026-03-03 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, Sequence[str], None] = '102961f3dfe1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Business: Freedom Pay credentials
    op.add_column('businesses', sa.Column('freedom_pay_merchant_id', sa.Integer(), nullable=True))
    op.add_column('businesses', sa.Column('freedom_pay_secret_key', sa.String(256), nullable=True))

    # Payment: gateway_order_id, gateway (rename from kaspi_order_id)
    op.add_column('payments', sa.Column('gateway_order_id', sa.String(128), nullable=True))
    op.add_column('payments', sa.Column('gateway', sa.String(32), nullable=True))
    op.execute("UPDATE payments SET gateway_order_id = kaspi_order_id, gateway = 'kaspi' WHERE kaspi_order_id IS NOT NULL")
    op.drop_constraint('payments_kaspi_order_id_key', 'payments', type_='unique')
    op.drop_column('payments', 'kaspi_order_id')
    op.create_unique_constraint('payments_gateway_order_id_key', 'payments', ['gateway_order_id'])


def downgrade() -> None:
    # Payment: restore kaspi_order_id
    op.add_column('payments', sa.Column('kaspi_order_id', sa.String(128), nullable=True))
    op.execute("UPDATE payments SET kaspi_order_id = gateway_order_id WHERE gateway_order_id IS NOT NULL")
    op.drop_constraint('payments_gateway_order_id_key', 'payments', type_='unique')
    op.drop_column('payments', 'gateway_order_id')
    op.drop_column('payments', 'gateway')
    op.create_unique_constraint('payments_kaspi_order_id_key', 'payments', ['kaspi_order_id'])

    # Business
    op.drop_column('businesses', 'freedom_pay_secret_key')
    op.drop_column('businesses', 'freedom_pay_merchant_id')
