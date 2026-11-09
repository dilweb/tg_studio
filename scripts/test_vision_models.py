"""Тест мультимодальных моделей на экспертность в тату-консультации.

Качает реальные фото (переписка из Telegram + портфолио из MinIO), прогоняет
их через кандидатов OpenRouter с одним и тем же «экспертным» промптом,
складывает ответы в report.md для сравнения.

Запуск: docker compose run --rm api python scripts/test_vision_models.py
"""

import asyncio
import base64
import io
import sys

from openai import AsyncOpenAI

from tg_studio.config import settings
from tg_studio.db.session import async_session_factory
from tg_studio.modules.storage import s3
from tg_studio.modules.chat import service as chat_service

# Кандидаты: текущая мультимодалка + типовые альтернативы OpenRouter
CANDIDATES = [
    "anthropic/claude-haiku-5.5",  # текущая
    "google/gemini-2.5-flash",
    "openai/gpt-5-mini",
    "qwen/qwen3-vl-235b-a22b-instruct",
]

# Фото для теста: chat_messages id (качаются из Telegram) + ключи MinIO
CHAT_PHOTO_IDS = [62, 63, 54, 53]
PORTFOLIO_KEYS = [
    "masters/portfolio/5/portfolio_654b803e42c045c2baff9d535780c52f.jpg",
    "masters/portfolio/5/portfolio_eac7c10bba9142829aec6738f8d1cab8.jpeg",
]

# Экспертный промпт — то, чего не хватает нынешнему «ресепшену»:
# не «кота звёздочкой», а стиль, техника, нюансы, вопросы мастеру
EXPERT_PROMPT = """\
Ты — опытный тату-мастер и консультант студии. Клиент прислал фото. \
Дай экспертную консультацию (5-8 предложений, по-русски):
1) Стиль татуировки/эскиза — точно, с терминологией (например: блэкворк, \
орнаментал, графикa/dotwork, неотрэдишнл, реализм, лайнворк, боссхайм...), \
не ограничивайся «минимализм», если стиль сложнее.
2) Техника и характеристики: плотность линий, штриховка/вейвворк, \
покрытие, цветность, вероятная сложность исполнения.
3) Особенности: как ляжет на кожу/зону, выцветание, тонкие линии, \
что потребует мастер.
4) Что уточнить у клиента для записи (1-2 вопроса по делу).
Не diagnose кожу/здоровье. Пиши плотно, без воды."""

QUESTION = "Вот фото. Что это за стиль, дай консультацию как эксперт."


async def collect_images() -> list[tuple[str, str, bytes]]:
    """(метка, mime, байты) — фото переписки из Telegram + портфолио из MinIO."""
    images: list[tuple[str, str, bytes]] = []
    async with async_session_factory() as session:
        for msg_id in CHAT_PHOTO_IDS:
            msg = await chat_service.get_message_file(session, msg_id)
            if msg is None:
                print(f"  msg {msg_id}: not found, skip", file=sys.stderr)
                continue
            try:
                data = await chat_service.download_photo(msg.telegram_file_id)
            except Exception as exc:  # noqa: BLE001
                print(f"  msg {msg_id}: download failed: {exc}", file=sys.stderr)
                continue
            mime = chat_service.FILE_MEDIA_TYPES.get(msg.file_kind or "", "image/jpeg")
            images.append((f"chat#{msg_id}", mime, data))
    for key in PORTFOLIO_KEYS:
        data = s3.get_object(key)
        if data is None:
            print(f"  s3 {key}: not found, skip", file=sys.stderr)
            continue
        mime = "image/jpeg" if key.endswith((".jpg", ".jpeg")) else "image/png"
        images.append((f"s3:{key.rsplit('/', 1)[-1]}", mime, data))
    return images


async def ask(client: AsyncOpenAI, model: str, mime: str, data: bytes) -> str:
    b64 = base64.b64encode(data).decode("ascii")
    resp = await client.chat.completions.create(
        model=model,
        max_tokens=1500,
        messages=[
            {"role": "system", "content": EXPERT_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": QUESTION},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                ],
            },
        ],
    )
    return resp.choices[0].message.content or ""


async def main() -> None:
    images = await collect_images()
    print(f"images collected: {[label for label, _, _ in images]}", file=sys.stderr)
    if not images:
        sys.exit("no images to test")
    # одно фото режем до ~1200px по большей стороне, чтобы не жечь токены
    try:
        from PIL import Image

        shrunk: list[tuple[str, str, bytes]] = []
        for label, mime, data in images:
            img = Image.open(io.BytesIO(data))
            img.thumbnail((1200, 1200))
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="JPEG", quality=85)
            shrunk.append((label, "image/jpeg", buf.getvalue()))
        images = shrunk
    except Exception as exc:  # noqa: BLE001
        print(f"PIL unavailable ({exc}), sending originals", file=sys.stderr)

    client = AsyncOpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url)
    lines = ["# Тест мультимодалок: экспертная тату-консультация", ""]
    for model in CANDIDATES:
        lines += [f"## {model}", ""]
        for label, mime, data in images:
            try:
                answer = await ask(client, model, mime, data)
            except Exception as exc:  # noqa: BLE001
                answer = f"_ОШИБКА: {exc}_"
            lines += [f"### {label}", "", answer, "", "---", ""]
            print(f"{model} × {label}: {'OK' if not answer.startswith('_ОШИБКА') else 'FAIL'}", file=sys.stderr)
    report = "\n".join(lines)
    from pathlib import Path

    out = Path(__file__).resolve().parent / "vision_report.md"
    out.write_text(report)
    print(f"written {out}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
