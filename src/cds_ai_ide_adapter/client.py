from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from typing import Any

import httpx

from .tool_host import WorkspaceToolHost


class CDSAIClientError(RuntimeError):
    pass


@dataclass(frozen=True)
class CDSAIChatResult:
    conversation_id: str
    message: dict[str, Any]
    tool_rounds: int
    response: dict[str, Any]


class CDSAIClient:
    """Minimal async OpenAI-compatible client with a workstation-local tool loop."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        timeout_seconds: float = 180.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("base_url must be an HTTP(S) URL")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        normalized = base_url.rstrip("/")
        self.url = (
            normalized if normalized.endswith("/chat/completions")
            else f"{normalized}/chat/completions"
        )
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        tool_host: WorkspaceToolHost,
        *,
        model: str = "CDS_AI",
        conversation_id: str | None = None,
        task_type: str = "chat",
        tool_choice: str | dict[str, Any] = "auto",
        max_tool_rounds: int = 8,
    ) -> CDSAIChatResult:
        if not messages or messages[-1].get("role") != "user":
            raise ValueError("messages must end with a user message")
        if max_tool_rounds < 0 or max_tool_rounds > 32:
            raise ValueError("max_tool_rounds must be between 0 and 32")
        system_messages = [message for message in messages if message.get("role") == "system"]
        next_messages = messages
        tool_rounds = 0
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            transport=self.transport,
        ) as client:
            while True:
                payload: dict[str, Any] = {
                    "model": model,
                    "messages": next_messages,
                    "task_type": task_type,
                    "stream": False,
                    "tools": tool_host.definitions,
                    "tool_choice": tool_choice,
                }
                if conversation_id is not None:
                    payload["conversation_id"] = conversation_id
                try:
                    response = await client.post(self.url, json=payload, headers=headers)
                except httpx.HTTPError as exc:
                    raise CDSAIClientError("CDS_AI request transport failed") from exc
                if response.is_error:
                    try:
                        error_body = response.json()
                        detail = error_body.get("detail", "request rejected")
                    except (ValueError, AttributeError):
                        detail = "request rejected"
                    raise CDSAIClientError(
                        f"CDS_AI returned HTTP {response.status_code}: {str(detail)[:1000]}"
                    )
                try:
                    body = response.json()
                    choice = body["choices"][0]
                    message = choice["message"]
                except (ValueError, KeyError, IndexError, TypeError) as exc:
                    raise CDSAIClientError("CDS_AI returned an invalid chat-completion response") from exc

                conversation_id = body.get("conversation_id") or conversation_id
                if not isinstance(conversation_id, str) or not conversation_id:
                    raise CDSAIClientError("CDS_AI response did not include a conversation_id")

                tool_calls = message.get("tool_calls") or []
                if not tool_calls:
                    return CDSAIChatResult(
                        conversation_id=conversation_id,
                        message=message,
                        tool_rounds=tool_rounds,
                        response=body,
                    )
                if len(tool_calls) != 1:
                    raise CDSAIClientError("CDS_AI returned more than one tool call in a turn")
                if tool_rounds >= max_tool_rounds:
                    raise CDSAIClientError("maximum workstation tool-call rounds exceeded")

                tool_rounds += 1
                cancellation_event = threading.Event()
                tool_task = asyncio.create_task(asyncio.to_thread(
                    tool_host.execute_tool_call,
                    tool_calls[0],
                    conversation_id,
                    cancellation_event,
                ))
                try:
                    tool_message = await asyncio.shield(tool_task)
                except asyncio.CancelledError:
                    # A worker thread cannot be force-cancelled. Signal it so
                    # pending file writes stop at their next safe commit point.
                    cancellation_event.set()
                    raise
                next_messages = [*system_messages, tool_message]
