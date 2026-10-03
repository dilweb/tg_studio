"""add tattoo_files

Фото сеансов (эскизы/результаты) хранятся на диске (UPLOAD_DIR),
в БД — метаданные и относительный путь.

Revision ID: p1q2r3s4t5u6
Revises: o0p1q2r3s4t5
Create Date: 2026-10-03

"""

import sqlalchemy as sa
from alembic import op

revision = "p1q2r3s4t5u6"
down_revision = "o0p1q2r3s4t5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tattoo_files",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("sketch", "result", name="tattoofilekind"),
            nullable=False,
        ),
        sa.Column("stored_path", sa.String(512), nullable=False),
        sa.Column("original_name", sa.String(256), nullable=False),
        sa.Column("mime", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["tattoo_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tattoo_files_session_id", "tattoo_files", ["session_id"])
    op.create_index("ix_tattoo_files_stored_path", "tattoo_files", ["stored_path"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_tattoo_files_stored_path", table_name="tattoo_files")
    op.drop_index("ix_tattoo_files_session_id", table_name="tattoo_files")
    op.drop_table("tattoo_files")
    op.execute("DROP TYPE IF EXISTS tattoofilekind")
