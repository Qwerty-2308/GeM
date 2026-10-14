"""Groq chat completions (OpenAI-compatible) for the chatbot.

This is the only code that talks to Groq. It never touches GeM: GeM requests go through
SafeFetcher, and the model only reaches GeM by calling the chat tools.
"""

from __future__ import annotations

import time
from typing import Callable

import httpx

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
# What a message may carry back into the conversation. Groq adds its own fields (e.g. the
# model's "reasoning"); those are not sent again.
_KEPT = ("role", "content", "tool_calls")
# Free Groq keys allow 8,000 tokens a minute; a short wait beats an error in front of the seller.
_LONGEST_WAIT_SECONDS = 30.0


class ChatModelError(Exception):
    """Something went wrong with the model; the message is meant for the seller."""


class GroqChatModel:
    def __init__(self, api_key: Callable[[], str | None], model: str,
                 transport: httpx.BaseTransport | None = None, timeout_seconds: float = 60.0,
                 sleep: Callable[[float], None] = time.sleep):
        self._api_key = api_key  # read on every call, so a newly set key works without a restart
        self.model = model
        self._client = httpx.Client(transport=transport, timeout=timeout_seconds)
        self._sleep = sleep

    @property
    def ready(self) -> bool:
        return bool(self._api_key())

    def complete(self, messages: list[dict], tools: list[dict]) -> dict:
        key = self._api_key()
        if not key:
            raise ChatModelError("The chat has no Groq API key yet. On a server, set GROQ_API_KEY; on a Mac, run: "
                                "gem-assistant set-groq-key")
        response = self._post(key, messages, tools)
        if response.status_code == 429:
            wait = _seconds(response.headers.get("retry-after"))
            if wait is not None and wait <= _LONGEST_WAIT_SECONDS:  # once: a model that loops can't stall here
                self._sleep(wait)
                response = self._post(key, messages, tools)
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

    def _post(self, key: str, messages: list[dict], tools: list[dict]) -> httpx.Response:
        try:
            return self._client.post(GROQ_URL, headers={"Authorization": f"Bearer {key}"}, json={
                "model": self.model, "messages": messages, "tools": tools, "temperature": 0.2,
                "max_completion_tokens": 2048})
        except httpx.HTTPError as error:
            raise ChatModelError("Couldn't reach Groq. Check the internet connection and ask again.") from error


def _seconds(value: str | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None
