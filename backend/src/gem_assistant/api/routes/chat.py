from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ...infrastructure.groq_chat import ChatModelError
from ...services.chat_service import ChatService
from ...services.chat_tools import ChatTools
from ..schemas import ChatIn

router = APIRouter(prefix="/api/chat", tags=["chat"])


@router.get("")
def chat_status(request: Request) -> dict:
    """Whether the chat can answer. Never returns the key."""
    return {"ready": getattr(request.app.state.container.chat_model, "ready", True)}


@router.post("")
def chat(body: ChatIn, request: Request) -> dict:
    container = request.app.state.container
    try:
        reply = ChatService(container.chat_model, ChatTools(container)).reply(body.messages)
    except ChatModelError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return {"messages": reply.messages, "cards": reply.cards}
