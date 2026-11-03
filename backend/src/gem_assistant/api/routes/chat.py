from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse

from ...infrastructure.groq_chat import ChatModelError
from ...services.chat_service import ChatService
from ...services.chat_tools import ChatTools
from ..schemas import ChatIn

router = APIRouter(prefix="/api/chat", tags=["chat"])

KEEPALIVE_SECONDS = 10.0
_log = logging.getLogger(__name__)
_answers = ThreadPoolExecutor(max_workers=4, thread_name_prefix="chat")


@router.get("")
def chat_status(request: Request) -> dict:
    """Whether the chat can answer. Never returns the key."""
    return {"ready": getattr(request.app.state.container.chat_model, "ready", True)}


@router.post("")
def chat(body: ChatIn, request: Request) -> StreamingResponse:
    """An answer can take a minute (model rounds, Groq's rate limit, a GeM download), and proxies drop
    a connection that is silent for a while (Fly: 60 s). So a newline goes out every few seconds while
    it works, then the JSON. An error comes as {"error": ...}: the 200 has been sent by then."""
    container = request.app.state.container
    work = _answers.submit(ChatService(container.chat_model, ChatTools(container)).reply,
                           body.messages, body.added_bid_id)

    def answer():
        while True:
            try:
                reply = work.result(timeout=KEEPALIVE_SECONDS)
            except TimeoutError:
                yield "\n"
                continue
            except ChatModelError as error:
                yield json.dumps({"error": str(error)})
            except Exception:
                _log.exception("The chat answer failed")
                yield json.dumps({"error": "Something went wrong on the server. Ask again."})
            else:
                yield json.dumps(jsonable_encoder({"messages": reply.messages, "cards": reply.cards}),
                                 ensure_ascii=False)
            return

    return StreamingResponse(answer(), media_type="application/json")
