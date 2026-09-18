"""split_tattoo_projects_into_works_and_sessions

Revision ID: h2i3j4k5l6m7
Revises: 2fb8e778874a
Create Date: 2026-09-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'h2i3j4k5l6m7'
down_revision: Union[str, Sequence[str], None] = '2fb8e778874a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'tattoo_works',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('client_id', sa.Integer(), nullable=False),
        sa.Column('master_id', sa.Integer(), nullable=False),
        sa.Column('business_id', sa.Integer(), nullable=False),
        sa.Column('size', sa.String(length=64), nullable=False),
        sa.Column('complexity', sa.String(length=64), nullable=False),
        sa.Column('style', sa.String(length=64), nullable=False),
        sa.Column('placement', sa.String(length=256), nullable=False),
        sa.Column(
            'status',
            sa.Enum('in_progress', 'completed', 'cancelled', name='tattooworkstatus'),
            nullable=False,
        ),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['client_id'], ['clients.id']),
        sa.ForeignKeyConstraint(['master_id'], ['masters.id']),
        sa.ForeignKeyConstraint(['business_id'], ['businesses.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_tattoo_works_client_id'), 'tattoo_works', ['client_id'], unique=False)
    op.create_index(op.f('ix_tattoo_works_master_id'), 'tattoo_works', ['master_id'], unique=False)
    op.create_index(op.f('ix_tattoo_works_business_id'), 'tattoo_works', ['business_id'], unique=False)

    op.create_table(
        'tattoo_sessions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('work_id', sa.Integer(), nullable=False),
        sa.Column('session_date', sa.DateTime(timezone=True), nullable=False),
        sa.Column('quoted_cost', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('cost', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column(
            'status',
            sa.Enum(
                'planned', 'in_progress', 'completed', 'cancelled',
                name='tattooprojectstatus', create_type=False,
            ),
            nullable=False,
        ),
        sa.Column('google_event_id', sa.String(length=256), nullable=True),
        sa.Column('sketch_file_id', sa.String(length=256), nullable=True),
        sa.Column('result_file_id', sa.String(length=256), nullable=True),
        sa.Column('is_final_session', sa.Boolean(), nullable=False),
        sa.Column('llm_verdict', sa.String(length=32), nullable=True),
        sa.Column('llm_observed_size', sa.String(length=64), nullable=True),
        sa.Column('llm_observed_color', sa.String(length=64), nullable=True),
        sa.Column('llm_observed_style', sa.String(length=64), nullable=True),
        sa.Column('llm_notes', sa.Text(), nullable=True),
        sa.Column('alert_sent_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['work_id'], ['tattoo_works.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_tattoo_sessions_work_id'), 'tattoo_sessions', ['work_id'], unique=False)

    op.drop_index(op.f('ix_tattoo_projects_master_id'), table_name='tattoo_projects')
    op.drop_index(op.f('ix_tattoo_projects_business_id'), table_name='tattoo_projects')
    op.drop_table('tattoo_projects')


def downgrade() -> None:
    """Downgrade schema."""
    op.create_table(
        'tattoo_projects',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('master_id', sa.Integer(), nullable=False),
        sa.Column('business_id', sa.Integer(), nullable=False),
        sa.Column('size', sa.String(length=64), nullable=False),
        sa.Column('complexity', sa.String(length=64), nullable=False),
        sa.Column('placement', sa.String(length=256), nullable=False),
        sa.Column('session_date', sa.DateTime(timezone=True), nullable=False),
        sa.Column('cost', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column(
            'status',
            sa.Enum(
                'planned', 'in_progress', 'completed', 'cancelled',
                name='tattooprojectstatus', create_type=False,
            ),
            nullable=False,
        ),
        sa.Column('google_event_id', sa.String(length=256), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['business_id'], ['businesses.id']),
        sa.ForeignKeyConstraint(['master_id'], ['masters.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_tattoo_projects_business_id'), 'tattoo_projects', ['business_id'], unique=False)
    op.create_index(op.f('ix_tattoo_projects_master_id'), 'tattoo_projects', ['master_id'], unique=False)

    op.drop_index(op.f('ix_tattoo_sessions_work_id'), table_name='tattoo_sessions')
    op.drop_table('tattoo_sessions')

    op.drop_index(op.f('ix_tattoo_works_business_id'), table_name='tattoo_works')
    op.drop_index(op.f('ix_tattoo_works_master_id'), table_name='tattoo_works')
    op.drop_index(op.f('ix_tattoo_works_client_id'), table_name='tattoo_works')
    op.drop_table('tattoo_works')

    sa.Enum(name='tattooworkstatus').drop(op.get_bind(), checkfirst=True)
