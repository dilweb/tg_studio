from typing import Literal

from pydantic import AliasChoices, BaseModel, Field


class AIChatRequest(BaseModel):
    message: str
    conversation_id: int | None = None
    confirmed: bool = Field(
        default=False,
        validation_alias=AliasChoices("confirmed", "confirmation"),
    )
    # Чей диалог: "owner" — аналитика владельца (user_id), "client" — песочница
    # от лица клиента (client_id). Диалоги разведены в БД (AIConversation).
    variant: Literal["owner", "client"] = "owner"
    client_id: int | None = None  # обязателен при variant="client"


class AIChatResponse(BaseModel):
    response: str
    conversation_id: int
    awaiting_confirmation: bool = False
