"""
Sanitized database schema for the AI system prompt.
Sensitive columns are omitted from the schema so the LLM never learns about them.
If the LLM somehow produces a query referencing them, the results are stripped too.
"""

FORBIDDEN_COLUMNS = frozenset({
    "registration_token",
    "owner_telegram_id",
})

DB_SCHEMA = """\
## Database schema (PostgreSQL)

All queries MUST include the `:business_id` placeholder — it is substituted by the system. \
Use it as `WHERE business_id = :business_id`.

### businesses
- id (int, PK)
- name (varchar)
- description (text)
- phone (varchar)
- is_active (bool)
- created_at (datetime)

### clients
- id (int, PK)
- telegram_id (bigint, unique)
- username (varchar)
- full_name (varchar)
- phone (varchar)
- created_at (datetime)

### masters
- id (int, PK)
- business_id (int, FK → businesses.id)
- telegram_id (bigint, unique, nullable)
- full_name (varchar)
- description (text)
- is_active (bool)

### services
- id (int, PK)
- business_id (int, FK → businesses.id)
- name (varchar)
- description (text)
- service_type (enum: 'appointment', 'project')
- price (decimal)
- is_active (bool)

### master_services (master ↔ service, M2M)
- master_id (int, FK → masters.id, PK)
- service_id (int, FK → services.id, PK)

### work_schedules (master's weekly schedule)
- id (int, PK)
- master_id (int, FK → masters.id)
- weekday (int) — 0 = Monday, 6 = Sunday
- start_time (varchar "HH:MM")
- end_time (varchar "HH:MM")
- slot_duration_minutes (int)

## Key JOIN relationships
- masters ↔ services via master_services (M2M)
- masters → work_schedules (schedule)

## Searching staff by name
- By **partial name**: `full_name ILIKE '%fragment%'` (lowercase pattern), not `=`.
- Do NOT use the `description` field for name searches unless the user explicitly asks by specialization.
- If the result is "0" or ambiguous — **in the next query** fetch the business's staff list \
(`id`, `full_name`, `is_active` if needed, `business_id = :business_id`, `LIMIT 50`), \
compare with the question, clarify with the user if needed, or run the target count for the chosen `id`.
"""
