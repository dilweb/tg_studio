"""
OpenAI function-calling tool definitions.
Single flexible tool: execute_analytics_sql — the LLM writes the SQL,
we validate and run it in a read-only transaction.

All tables have business_id directly where applicable.
"""

ANALYTICS_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "execute_analytics_sql",
            "description": (
                "Execute one analytical SQL query (SELECT) against the business data. "
                "In SQL you MUST use the :business_id placeholder (literally that string) — "
                "the system will substitute the actual value automatically. "
                "For staff names use ILIKE with %. "
                "If the result is zero or ambiguous, make another call — "
                "fetch the staff list or a different clarifying SELECT. "
                "If the question is about how many services/staff there are and there are few records — "
                "select columns for enumeration (name, price, status), not just COUNT(*). "
                "Result: columns, rows (up to 100), row_count, truncated."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sql_query": {
                        "type": "string",
                        "description": (
                            "A SQL SELECT query. It must contain :business_id. "
                            "Examples:\n"
                            "- SELECT id, name, price, is_active FROM services "
                            "WHERE business_id = :business_id ORDER BY name LIMIT 50"
                        ),
                    },
                },
                "required": ["sql_query"],
                "additionalProperties": False,
            },
        },
    },
]
