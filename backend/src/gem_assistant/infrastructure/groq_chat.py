"""Groq chat completions (OpenAI-compatible) for the chatbot.

This is the only code that talks to Groq. It never touches GeM: GeM requests go through
SafeFetcher, and the model only reaches GeM by calling the chat tools.
"""

from __future__ import annotations

from typing import Callable

import httpx

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
# What a message may carry back into the conversation. Groq adds its own fields (e.g. the
# model's "reasoning"); those are not sent again.
_KEPT = ("role", "content", "tool_calls")


class ChatModelError(Exception):
    """Something went wrong with the model; the message is meant for the seller."""


class GroqChatModel:
    def __init__(self, api_key: Callable[[], str | None], model: str,
                 transport: httpx.BaseTransport | None = None, timeout_seconds: float = 60.0):
        self._api_key = api_key  # read on every call, so a newly set key works without a restart
        self.model = model
        self._client = httpx.Client(transport=transport, timeout=timeout_seconds)

    @property
    def ready(self) -> bool:
        return bool(self._api_key())

    def complete(self, messages: list[dict], tools: list[dict]) -> dict:
        key = self._api_key()
        if not key:
            raise ChatModelError("The chat has no Groq API key yet. On a server, set GROQ_API_KEY; on a Mac, run: "
                                "gem-assistant set-groq-key")
        try:
            response = self._client.post(GROQ_URL, headers={"Authorization": f"Bearer {key}"}, json={
                "model": self.model, "messages": messages, "tools": tools, "temperature": 0.2,
                "max_completion_tokens": 2048})
        except httpx.HTTPError as error:
            raise ChatModelError("Couldn't reach Groq. Check the internet connection and ask again.") from error
        if response.status_code == 401:
            raise ChatModelError("Groq refused the API key. Set GROQ_API_KEY again (server) or run: "
                                "gem-assistant set-groq-key (Mac)")
        if response.status_code == 429:
            raise ChatModelError("Groq is busy right now (rate limit). Wait a minute and ask again.")
        if response.status_code >= 400:
            raise ChatModelError(f"Groq answered with an error ({response.status_code}). Try again shortly.")
        try:
            message = response.json()["choices"][0]["message"]
            return {k: message[k] for k in _KEPT if message.get(k) is not None}
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as error:
            raise ChatModelError("Groq sent an answer this app couldn't read. Ask again.") from error
