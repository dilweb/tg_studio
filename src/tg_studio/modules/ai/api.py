from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from starlette.requests import Request

from tg_studio.api.admin_deps import OwnerBusinessAIDep
from tg_studio.api.auth import CurrentUserDep
from tg_studio.api.deps import SessionDep
from tg_studio.modules.ai.client import ConversationNotFoundError, chat
from tg_studio.modules.ai.schemas import AIChatRequest, AIChatResponse
from tg_studio.modules.ai.sse import stream_ai_chat_sse

router = APIRouter(prefix="/admin/ai-chat", tags=["AI Chat"])


@router.post("", response_model=AIChatResponse)
async def ai_chat(
    body: AIChatRequest,
    business: OwnerBusinessAIDep,
    user: CurrentUserDep,
    session: SessionDep,
):
    if not body.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    try:
        result = await chat(
            session=session,
            business=business,
            user_id=user.id,
            user_message=body.message.strip(),
            conversation_id=body.conversation_id,
            confirmed=body.confirmed,
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
    return StreamingResponse(
        stream_ai_chat_sse(
            session=session,
            business=business,
            user_id=user.id,
            body=body,
            is_disconnected=http_request.is_disconnected,
        ),
        media_type="text/event-stream",
    )
