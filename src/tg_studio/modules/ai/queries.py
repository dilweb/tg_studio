"""
Read-only SQL for LLM analytics: validate, execute, return JSON for the **tool** message.

Validation is minimal: only check that the query is a safe SELECT and contains the placeholder.
"""

import logging
import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tg_studio.db.session import engine

from .schema import FORBIDDEN_COLUMNS

logger = logging.getLogger(__name__)

MAX_ROWS = 100

# Keywords that would mutate data — all blocked
_DANGEROUS_KEYWORDS = re.compile(
    r"\b("
    r"INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|GRANT|REVOKE"
    r"|EXEC\b|EXECUTE|COPY|LOCK|VACUUM|REINDEX|CLUSTER"
    r"|DO\s+\$"
    r")\b",
    re.IGNORECASE,
)

_COMMENT_PATTERNS = re.compile(r"--[^\n]*|/\*[\s\S]*?\*/")


def validate_sql(query: str) -> tuple[bool, str]:
    """
    Validate that the query is a safe read-only SELECT with :business_id.
    Returns (is_valid, error_message).
    """
    cleaned = _COMMENT_PATTERNS.sub(" ", query).strip().rstrip(";").strip()

    if not cleaned:
        return False, "Empty query"

    if not re.match(r"^(SELECT|WITH)\b", cleaned, re.IGNORECASE):
        return False, "Only SELECT queries are allowed (or WITH ... SELECT)"

    if _DANGEROUS_KEYWORDS.search(cleaned):
        return False, "Query contains forbidden operations"

    if ":business_id" not in cleaned:
        return False, (
            "QUERY_ERROR: missing :business_id. "
            "Add a WHERE business_id = :business_id filter and call execute_analytics_sql again."
        )

    return True, ""


def _sanitize_row(columns: list[str], row: tuple) -> dict:
    """Build a dict from a result row, stripping forbidden columns."""
    return {
        col: _format_value(val)
        for col, val in zip(columns, row, strict=False)
        if col not in FORBIDDEN_COLUMNS
    }


def _format_value(val):
    """Convert DB values to JSON-safe types."""
    if val is None:
        return None
    if isinstance(val, float):
        return round(val, 2)
    try:
        return val.isoformat() if hasattr(val, "isoformat") else val
    except Exception:
        return str(val)


async def execute_analytics_sql(
    business_id: int,
    sql_query: str,
) -> dict:
    """
    Execute a validated read-only SQL query on a separate connection
    so that SET TRANSACTION READ ONLY doesn't affect the main session.
    """
    is_valid, error = validate_sql(sql_query)
    if not is_valid:
        logger.warning(
            "AI analytics SQL rejected by validation: %s | query:\n%s",
            error,
            sql_query.strip(),
        )
        return {"error": error, "rows": []}

    logger.info(
        "AI analytics SQL — executing:\n"
        "  business_id=%s\n"
        "  query:\n%s",
        business_id,
        sql_query.strip(),
    )

    async with engine.connect() as conn:
        try:
            await conn.execute(text("SET TRANSACTION READ ONLY"))

            result = await conn.execute(
                text(sql_query),
                {"business_id": business_id},
            )

            columns = list(result.keys())
            raw_rows = result.fetchmany(MAX_ROWS)

            rows = [_sanitize_row(columns, row) for row in raw_rows]
            safe_columns = [c for c in columns if c not in FORBIDDEN_COLUMNS]
            truncated = len(raw_rows) == MAX_ROWS

            logger.info(
                "AI analytics SQL — result: business_id=%s columns=%s row_count=%s truncated=%s "
                "preview_rows=%s",
                business_id,
                safe_columns,
                len(rows),
                truncated,
                rows[:3],
            )

            return {
                "columns": safe_columns,
                "rows": rows,
                "row_count": len(rows),
                "truncated": truncated,
            }
        except Exception as exc:
            logger.exception(
                "AI analytics SQL — execution error (business_id=%s):\n%s",
                business_id,
                sql_query.strip(),
            )
            return {"error": f"Execution error: {exc!s}", "rows": []}


TOOL_REGISTRY: dict[str, callable] = {
    "execute_analytics_sql": execute_analytics_sql,
}
