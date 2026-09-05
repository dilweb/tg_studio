"""add_users_table_and_rbac

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-04-17

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e2f3a4b5c6d7"
down_revision: Union[str, Sequence[str], None] = "d1e2f3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

userrole_enum = sa.Enum("owner", "master", "client", name="userrole")


def _column_exists(conn, table: str, column: str) -> bool:
    result = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = :table AND column_name = :column"
    ), {"table": table, "column": column})
    return result.scalar_one_or_none() is not None


def _constraint_exists(conn, name: str) -> bool:
    result = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.table_constraints WHERE constraint_name = :name"
    ), {"name": name})
    return result.scalar_one_or_none() is not None


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Enum (idempotent via PL/pgSQL exception handler)
    conn.execute(sa.text(
        "DO $$ BEGIN "
        "CREATE TYPE userrole AS ENUM ('owner', 'master', 'client'); "
        "EXCEPTION WHEN duplicate_object THEN null; "
        "END $$"
    ))

    # 2. Users table
    conn.execute(sa.text(
        "CREATE TABLE IF NOT EXISTS users ("
        "  id SERIAL PRIMARY KEY,"
        "  email VARCHAR(256) UNIQUE,"
        "  password_hash VARCHAR(256),"
        "  telegram_id BIGINT UNIQUE,"
        "  phone VARCHAR(20),"
        "  first_name VARCHAR(128) NOT NULL,"
        "  last_name VARCHAR(128),"
        "  role userrole NOT NULL DEFAULT 'client',"
        "  is_active BOOLEAN NOT NULL DEFAULT true,"
        "  created_at TIMESTAMP NOT NULL DEFAULT now()"
        ")"
    ))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_users_email ON users (email)"
    ))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_users_telegram_id ON users (telegram_id)"
    ))

    # 3. Add owner_id to businesses
    if not _column_exists(conn, "businesses", "owner_id"):
        op.add_column("businesses", sa.Column("owner_id", sa.Integer(), nullable=True))
    if not _constraint_exists(conn, "fk_businesses_owner_id_users"):
        op.create_foreign_key(
            "fk_businesses_owner_id_users",
            "businesses", "users",
            ["owner_id"], ["id"],
        )
    if not _constraint_exists(conn, "uq_businesses_owner_id"):
        op.create_unique_constraint("uq_businesses_owner_id", "businesses", ["owner_id"])

    # 4. Add user_id to clients
    if not _column_exists(conn, "clients", "user_id"):
        op.add_column("clients", sa.Column("user_id", sa.Integer(), nullable=True))
    if not _constraint_exists(conn, "fk_clients_user_id_users"):
        op.create_foreign_key(
            "fk_clients_user_id_users",
            "clients", "users",
            ["user_id"], ["id"],
        )
    if not _constraint_exists(conn, "uq_clients_user_id"):
        op.create_unique_constraint("uq_clients_user_id", "clients", ["user_id"])

    # 5. Add user_id to masters
    if not _column_exists(conn, "masters", "user_id"):
        op.add_column("masters", sa.Column("user_id", sa.Integer(), nullable=True))
    if not _constraint_exists(conn, "fk_masters_user_id_users"):
        op.create_foreign_key(
            "fk_masters_user_id_users",
            "masters", "users",
            ["user_id"], ["id"],
        )
    if not _constraint_exists(conn, "uq_masters_user_id"):
        op.create_unique_constraint("uq_masters_user_id", "masters", ["user_id"])

    # 6. Backfill: create User rows from existing data (skip if already done)
    already_backfilled = conn.execute(
        sa.text("SELECT EXISTS(SELECT 1 FROM users)")
    ).scalar()

    if not already_backfilled:
        rows = conn.execute(
            sa.text("SELECT id, owner_telegram_id, name, phone FROM businesses")
        ).fetchall()
        for biz_id, tg_id, name, phone in rows:
            result = conn.execute(
                sa.text(
                    "INSERT INTO users (telegram_id, first_name, phone, role) "
                    "VALUES (:tg_id, :name, :phone, 'owner') RETURNING id"
                ),
                {"tg_id": tg_id, "name": name, "phone": phone},
            )
            user_id = result.scalar_one()
            conn.execute(
                sa.text("UPDATE businesses SET owner_id = :uid WHERE id = :bid"),
                {"uid": user_id, "bid": biz_id},
            )

        rows = conn.execute(
            sa.text("SELECT id, telegram_id, full_name, phone FROM clients")
        ).fetchall()
        for client_id, tg_id, full_name, phone in rows:
            existing = conn.execute(
                sa.text("SELECT id FROM users WHERE telegram_id = :tg_id"),
                {"tg_id": tg_id},
            ).scalar_one_or_none()
            if existing:
                user_id = existing
            else:
                result = conn.execute(
                    sa.text(
                        "INSERT INTO users (telegram_id, first_name, phone, role) "
                        "VALUES (:tg_id, :name, :phone, 'client') RETURNING id"
                    ),
                    {"tg_id": tg_id, "name": full_name, "phone": phone},
                )
                user_id = result.scalar_one()
            conn.execute(
                sa.text("UPDATE clients SET user_id = :uid WHERE id = :cid"),
                {"uid": user_id, "cid": client_id},
            )

        rows = conn.execute(
            sa.text("SELECT id, telegram_id, full_name FROM masters WHERE telegram_id IS NOT NULL")
        ).fetchall()
        for master_id, tg_id, full_name in rows:
            existing = conn.execute(
                sa.text("SELECT id FROM users WHERE telegram_id = :tg_id"),
                {"tg_id": tg_id},
            ).scalar_one_or_none()
            if existing:
                user_id = existing
                conn.execute(
                    sa.text("UPDATE users SET role = 'master' WHERE id = :uid AND role = 'client'"),
                    {"uid": user_id},
                )
            else:
                result = conn.execute(
                    sa.text(
                        "INSERT INTO users (telegram_id, first_name, role) "
                        "VALUES (:tg_id, :name, 'master') RETURNING id"
                    ),
                    {"tg_id": tg_id, "name": full_name},
                )
                user_id = result.scalar_one()
            conn.execute(
                sa.text("UPDATE masters SET user_id = :uid WHERE id = :mid"),
                {"uid": user_id, "mid": master_id},
            )

    # 7. Make NOT NULL (only if backfill happened or columns have data)
    has_null_owner = conn.execute(
        sa.text("SELECT EXISTS(SELECT 1 FROM businesses WHERE owner_id IS NULL)")
    ).scalar()
    if not has_null_owner:
        op.alter_column("businesses", "owner_id", nullable=False)

    has_null_client_user = conn.execute(
        sa.text("SELECT EXISTS(SELECT 1 FROM clients WHERE user_id IS NULL)")
    ).scalar()
    if not has_null_client_user:
        op.alter_column("clients", "user_id", nullable=False)


def downgrade() -> None:
    conn = op.get_bind()

    if _constraint_exists(conn, "uq_masters_user_id"):
        op.drop_constraint("uq_masters_user_id", "masters", type_="unique")
    if _constraint_exists(conn, "fk_masters_user_id_users"):
        op.drop_constraint("fk_masters_user_id_users", "masters", type_="foreignkey")
    if _column_exists(conn, "masters", "user_id"):
        op.drop_column("masters", "user_id")

    if _constraint_exists(conn, "uq_clients_user_id"):
        op.drop_constraint("uq_clients_user_id", "clients", type_="unique")
    if _constraint_exists(conn, "fk_clients_user_id_users"):
        op.drop_constraint("fk_clients_user_id_users", "clients", type_="foreignkey")
    if _column_exists(conn, "clients", "user_id"):
        op.drop_column("clients", "user_id")

    if _constraint_exists(conn, "uq_businesses_owner_id"):
        op.drop_constraint("uq_businesses_owner_id", "businesses", type_="unique")
    if _constraint_exists(conn, "fk_businesses_owner_id_users"):
        op.drop_constraint("fk_businesses_owner_id_users", "businesses", type_="foreignkey")
    if _column_exists(conn, "businesses", "owner_id"):
        op.drop_column("businesses", "owner_id")

    op.execute("DROP TABLE IF EXISTS users")
    op.execute("DROP TYPE IF EXISTS userrole")
