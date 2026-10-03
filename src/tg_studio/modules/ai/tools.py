"""
OpenAI function-calling tool definitions.

Two tools: execute_analytics_sql (LLM writes SQL against the business DB)
and get_bookings (live schedule from Google Calendar, filter-based).
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
                "If the question is about how many masters/bookings there are and there are few records — "
                "select columns for enumeration (name, status), not just COUNT(*). "
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
                            "- SELECT id, full_name FROM masters "
                            "WHERE business_id = :business_id ORDER BY id LIMIT 50"
                        ),
                    },
                },
                "required": ["sql_query"],
                "additionalProperties": False,
            },
        },
    },
]

BOOKINGS_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_bookings",
            "description": (
                "The live studio schedule from Google Calendar: who is booked and when. "
                "Returns only BUSY intervals with labels — free time must be inferred. "
                "All parameters are optional filters; omit everything to get all bookings "
                "for the next 2 weeks. Use this for schedule/free-time questions; "
                "bookings are NOT in the DB — never use SQL for them."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "master": {
                        "type": "string",
                        "description": (
                            "Fuzzy master name, e.g. 'Eugene' matches 'Eugene Smirnov'. "
                            "Omit for all masters."
                        ),
                    },
                    "date_from": {
                        "type": "string",
                        "description": (
                            "Start date YYYY-MM-DD, studio timezone Asia/Almaty. Default: today."
                        ),
                    },
                    "date_to": {
                        "type": "string",
                        "description": (
                            "End date YYYY-MM-DD inclusive. Default: date_from + 14 days; "
                            "the window is clamped to 31 days."
                        ),
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        },
    },
]

AGENT_TOOLS: list[dict] = ANALYTICS_TOOLS + BOOKINGS_TOOLS
