from pydantic import AliasChoices, BaseModel, Field


class AIChatRequest(BaseModel):
    message: str
    conversation_id: int | None = None
    confirmed: bool = Field(
        default=False,
        validation_alias=AliasChoices("confirmed", "confirmation"),
    )


class AIChatResponse(BaseModel):
    response: str
    conversation_id: int
    awaiting_confirmation: bool = False
