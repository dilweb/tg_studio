"""
Dynamic database schema introspection for the AI system prompt.

Uses SQLAlchemy's inspector to extract table/column information at runtime,
replacing the need for hardcoded schema descriptions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from .schema import FORBIDDEN_COLUMNS

# Human-readable type names for the LLM
_TYPE_NAMES = {
    "INTEGER": "int",
    "BIGINT": "bigint",
    "SMALLINT": "int",
    "VARCHAR": "varchar",
    "TEXT": "text",
    "BOOLEAN": "bool",
    "TIMESTAMP": "datetime",
    "TIMESTAMPTZ": "timestamptz",
    "NUMERIC": "decimal",
    "FLOAT": "float",
    "DOUBLE": "float",
    "DATE": "date",
    "TIME": "time",
    "UUID": "uuid",
    "JSON": "json",
    "JSONB": "jsonb",
    "ENUM": "enum",
}


@dataclass
class ColumnInfo:
    """Information about a single column."""
    name: str
    type_name: str
    nullable: bool
    is_pk: bool
    comment: str | None = None


@dataclass
class TableInfo:
    """Information about a single table."""
    name: str
    columns: list[ColumnInfo] = field(default_factory=list)
    foreign_keys: list[dict[str, str]] = field(default_factory=list)
    comment: str | None = None


@dataclass
class SchemaInfo:
    """Complete schema information for the LLM prompt."""
    tables: list[TableInfo] = field(default_factory=list)

    def to_prompt_text(self, *, include_fk: bool = True, include_types: bool = True) -> str:
        """Generate a human-readable schema description for the LLM."""
        lines = ["## Database schema (PostgreSQL)\n"]
        lines.append(
            "All queries MUST include the `:business_id` placeholder — "
            "it is substituted by the system. "
            "Use it as `WHERE business_id = :business_id`.\n"
        )

        for table in self.tables:
            lines.append(f"### {table.name}")
            for col in table.columns:
                parts = [f"- {col.name}"]
                if include_types:
                    parts.append(f"({col.type_name})")
                if col.is_pk:
                    parts[-1] += ", PK"
                if col.comment:
                    parts.append(f"— {col.comment}")
                lines.append(" ".join(parts))

            if include_fk and table.foreign_keys:
                for fk in table.foreign_keys:
                    lines.append(
                        f"  → {fk['constrained_columns']} references {fk['referred_table']}.{fk['referred_columns']}"
                    )
            lines.append("")

        return "\n".join(lines)


def _simplify_type(db_type: str) -> str:
    """Convert a database type name to a simpler, LLM-friendly name."""
    upper = db_type.upper()
    for key, value in _TYPE_NAMES.items():
        if key in upper:
            return value
    return db_type.lower()


def _get_table_comment(inspector: Any, table_name: str) -> str | None:
    """Get a table comment if available."""
    try:
        comment = inspector.get_table_comment(table_name)
        return comment.get("text") if comment else None
    except (NotImplementedError, KeyError):
        return None


def _get_column_comment(col: dict) -> str | None:
    """Get a column comment if available."""
    return col.get("comment")


def introspect_schema(
    engine: Engine,
    *,
    schema: str | None = None,
    exclude_tables: set[str] | None = None,
    forbidden_columns: set[str] | None = None,
) -> SchemaInfo:
    """
    Introspect the database schema and return a SchemaInfo object.

    Args:
        engine: SQLAlchemy engine to introspect.
        schema: Schema name to introspect (default: default schema).
        exclude_tables: Table names to exclude from introspection.
        forbidden_columns: Column names to exclude from all tables.

    Returns:
        SchemaInfo object with table and column information.
    """
    inspector = inspect(engine)
    exclude = exclude_tables or set()
    forbidden = forbidden_columns or FORBIDDEN_COLUMNS

    result = SchemaInfo()

    for table_name in sorted(inspector.get_table_names(schema=schema)):
        if table_name in exclude:
            continue

        # Skip Alembic's version table
        if table_name == "alembic_version":
            continue

        table_info = TableInfo(
            name=table_name,
            comment=_get_table_comment(inspector, table_name),
        )

        # Get columns
        columns = inspector.get_columns(table_name, schema=schema)
        pk_cols = {col["name"] for col in inspector.get_pk_constraint(table_name, schema=schema).get("constrained_columns", [])}

        for col in columns:
            if col["name"] in forbidden:
                continue

            table_info.columns.append(ColumnInfo(
                name=col["name"],
                type_name=_simplify_type(str(col["type"])),
                nullable=col.get("nullable", True),
                is_pk=col["name"] in pk_cols,
                comment=_get_column_comment(col),
            ))

        # Get foreign keys
        for fk in inspector.get_foreign_keys(table_name, schema=schema):
            referred_table = fk.get("referred_table", "")
            if referred_table in exclude:
                continue
            table_info.foreign_keys.append({
                "constrained_columns": ", ".join(fk.get("constrained_columns", [])),
                "referred_table": referred_table,
                "referred_columns": ", ".join(fk.get("referred_columns", [])),
            })

        result.tables.append(table_info)

    return result


# Cache for the schema info — regenerated on demand
_schema_cache: SchemaInfo | None = None


def get_schema_info(
    engine: Engine,
    *,
    force_refresh: bool = False,
    exclude_tables: set[str] | None = None,
    forbidden_columns: set[str] | None = None,
) -> SchemaInfo:
    """
    Get cached schema info, or introspect if not cached.

    Args:
        engine: SQLAlchemy engine.
        force_refresh: Force re-introspection even if cached.
        exclude_tables: Tables to exclude.
        forbidden_columns: Columns to exclude.

    Returns:
        SchemaInfo object.
    """
    global _schema_cache
    if _schema_cache is None or force_refresh:
        _schema_cache = introspect_schema(
            engine,
            exclude_tables=exclude_tables,
            forbidden_columns=forbidden_columns,
        )
    return _schema_cache
