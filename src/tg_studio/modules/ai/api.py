from collections.abc import Callable

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from starlette.requests import Request

from tg_studio.api.admin_deps import OwnerBusinessAIDep
from tg_studio.api.auth import CurrentUserDep
from tg_studio.api.deps import SessionDep
from tg_studio.db.models import AIMessage, AIConversation, Business, Client
from tg_studio.modules.ai.client import ConversationNotFoundError, chat
from tg_studio.modules.ai.schemas import AIChatRequest, AIChatResponse
from tg_studio.modules.ai.sse import stream_ai_chat_sse
from tg_studio.modules.ai.system_prompt import build_client_prompt, build_system_prompt
from tg_studio.modules.ai.tools import AGENT_TOOLS, BOOKINGS_TOOLS

router = APIRouter(prefix="/admin/ai-chat", tags=["AI Chat"])

# Разделение по юзерам: "owner" — аналитика владельца (диалог на user),
# "client" — песочница от лица клиента (диалог на client, владелец выбирает
# кого тестить). Разговоры в БД разведены (AIConversation.user_id/client_id).
VARIANT_PROMPTS: dict[str, Callable[[Business], str]] = {
    "owner": build_system_prompt,
    "client": build_client_prompt,
}

# Тула — тоже per-variant: клиентскому агенту SQL физически недоступен
# (не в промпте — в списке tools API-вызова, поэтому вызов невозможен в принципе).
VARIANT_TOOLS: dict[str, list[dict]] = {
    "owner": AGENT_TOOLS,
    "client": BOOKINGS_TOOLS,
}


async def _resolve_identity(
    variant: str, client_id: int | None, user, session
) -> tuple[int | None, int | None]:
    """(user_id, client_id) для выбранного варианта."""
    if variant == "owner":
        return user.id, None
    if client_id is None:
        raise HTTPException(status_code=400, detail="client_id обязателен для варианта client")
    if await session.get(Client, client_id) is None:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    return None, client_id


async def _identity_for_variant(
    body: AIChatRequest, user, session
) -> tuple[int | None, int | None]:
    return await _resolve_identity(body.variant, body.client_id, user, session)


@router.get("/clients")
async def list_ai_clients(
    session: SessionDep,
    _business: OwnerBusinessAIDep,
) -> list[dict]:
    """Клиенты для селектора «от лица кого тестим» (только id/имя)."""
    rows = await session.execute(
        select(Client.id, Client.full_name, Client.username).order_by(Client.full_name)
    )
    return [{"id": r.id, "full_name": r.full_name, "username": r.username} for r in rows]


_HISTORY_LIMIT = 50
_TITLE_MAX_LEN = 60


def _conversation_title(conv: AIConversation) -> str:
    first_user = next(
        (m.content for m in conv.messages if m.role == "user" and m.content), None
    )
    text = (first_user or "—").strip()
    return text[:_TITLE_MAX_LEN]


@router.get("/conversations")
async def list_ai_conversations(
    variant: str,
    business: OwnerBusinessAIDep,
    user: CurrentUserDep,
    session: SessionDep,
    client_id: int | None = None,
) -> list[dict]:
    """Диалоги выбранного варианта (owner — диалоги юзера, client — диалоги клиента)."""
    user_id, resolved_client_id = await _resolve_identity(variant, client_id, user, session)
    rows = await session.execute(
        select(AIConversation)
        .where(
            AIConversation.business_id == business.id,
            AIConversation.user_id == user_id,
            AIConversation.client_id == resolved_client_id,
        )
        .options(selectinload(AIConversation.messages))
        .order_by(AIConversation.updated_at.desc())
        .limit(_HISTORY_LIMIT)
    )
    convs = rows.scalars().all()
    return [
        {
            "id": c.id,
            "title": _conversation_title(c),
            "message_count": sum(1 for m in c.messages if m.role in ("user", "assistant") and m.content),
            "updated_at": c.updated_at.isoformat() if c.updated_at else None,
        }
        for c in convs
    ]


@router.get("/conversations/{conversation_id}/messages")
async def get_ai_conversation_messages(
    conversation_id: int,
    variant: str,
    business: OwnerBusinessAIDep,
    user: CurrentUserDep,
    session: SessionDep,
    client_id: int | None = None,
) -> list[dict]:
    """Сообщения одного диалога (только читаемый текст user/assistant)."""
    user_id, resolved_client_id = await _resolve_identity(variant, client_id, user, session)
    conv = await session.get(
        AIConversation,
        conversation_id,
        options=[selectinload(AIConversation.messages)],
    )
    if (
        conv is None
        or conv.business_id != business.id
        or conv.user_id != user_id
        or conv.client_id != resolved_client_id
    ):
        raise HTTPException(status_code=404, detail="Диалог не найден")
    return [
        {"role": m.role, "content": m.content or ""}
        for m in conv.messages
        if m.role in ("user", "assistant") and m.content
    ]


@router.post("", response_model=AIChatResponse)
async def ai_chat(
    body: AIChatRequest,
    business: OwnerBusinessAIDep,
    user: CurrentUserDep,
    session: SessionDep,
):
    if not body.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    user_id, client_id = await _identity_for_variant(body, user, session)
    try:
        result = await chat(
            session=session,
            business=business,
            user_id=user_id,
            client_id=client_id,
            user_message=body.message.strip(),
            conversation_id=body.conversation_id,
            confirmed=body.confirmed,
            build_prompt=VARIANT_PROMPTS[body.variant],
            tools=VARIANT_TOOLS[body.variant],
        )
    except ConversationNotFoundError:
        raise HTTPException(status_code=404, detail="Conversation not found") from None
    return AIChatResponse(
        response=result.reply,
        conversation_id=result.conversation_id,
        awaiting_confirmation=result.awaiting_confirmation,
    )


@router.post(
    "/stream",
    response_class=StreamingResponse,
    summary="AI chat (SSE) — agentic cycle progress and response chunking",
)
async def ai_chat_stream(
    body: AIChatRequest,
    business: OwnerBusinessAIDep,
    user: CurrentUserDep,
    session: SessionDep,
    http_request: Request,
) -> StreamingResponse:
    if not body.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    user_id, client_id = await _identity_for_variant(body, user, session)
    return StreamingResponse(
        stream_ai_chat_sse(
            session=session,
            business=business,
            user_id=user_id,
            client_id=client_id,
            body=body,
            build_prompt=VARIANT_PROMPTS[body.variant],
            tools=VARIANT_TOOLS[body.variant],
            is_disconnected=http_request.is_disconnected,
        ),
        media_type="text/event-stream",
        # X-Accel-Buffering: nginx не буферизует стрим; без этого deltas
        # прийдут одним куском после конца цикла
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )
