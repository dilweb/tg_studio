"""
Context window compression — summarises old messages when conversation grows too long.
"""

import logging

from openai import AsyncOpenAI

from tg_studio.config import settings

logger = logging.getLogger(__name__)

MAX_MESSAGES_BEFORE_COMPRESSION = 30
KEEP_RECENT_MESSAGES = 10

SUMMARISE_PROMPT = (
    "Ниже приведена история диалога между пользователем и бизнес-аналитиком. "
    "Сжато резюмируй ключевые факты, цифры и выводы из этого диалога. "
    "Пиши на русском языке. Сохрани только важную информацию, "
    "которая может понадобиться для продолжения разговора. "
    "Максимум 300 слов."
)


async def summarise_messages(
    client: AsyncOpenAI,
    messages: list[dict],
    existing_summary: str | None = None,
) -> str:
    text_parts: list[str] = []
    if existing_summary:
        text_parts.append(f"Предыдущее резюме:\n{existing_summary}\n")

    for msg in messages:
        role = msg.get("role", "?")
        content = msg.get("content", "")
        if content:
            text_parts.append(f"[{role}]: {content}")

    conversation_text = "\n".join(text_parts)

    resp = await client.chat.completions.create(
        model=settings.llm_model,
        temperature=0,
        messages=[
            {"role": "system", "content": SUMMARISE_PROMPT},
            {"role": "user", "content": conversation_text},
        ],
        max_tokens=600,
    )
    summary = resp.choices[0].message.content or ""
    logger.info("Compressed %d messages into %d-char summary", len(messages), len(summary))
    return summary.strip()


def needs_compression(message_count: int) -> bool:
    return message_count > MAX_MESSAGES_BEFORE_COMPRESSION
