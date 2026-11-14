from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
import json
from typing import Any, Protocol

import httpx


@dataclass(frozen=True)
class Completion:
    content: str
    model: str
    tool_calls: list[dict[str, Any]] | None = None
    finish_reason: str = "stop"


class ModelGateway(Protocol):
    async def complete(
        self,
        messages: list[dict[str, object]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
    ) -> Completion: ...

    def stream(
        self, messages: list[dict[str, object]], *, options: dict[str, Any] | None = None
    ) -> AsyncIterator[str]: ...


class GatewayError(RuntimeError):
    pass


class OpenAICompatibleGateway:
    """OpenAI-compatible transport; conversation and task state live elsewhere."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout_seconds: float = 180.0,
        client: httpx.AsyncClient | None = None,
    ):
        self.url = f"{base_url.rstrip('/')}/chat/completions"
        self.model = model
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        # Shared clients are owned by the application lifespan, never by a turn.
        self._client = client

    @asynccontextmanager
    async def _client_context(self):
        if self._client is not None:
            yield self._client
            return
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            yield client

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def complete(
        self,
        messages: list[dict[str, object]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
    ) -> Completion:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
        }
        if tools is not None:
            payload["tools"] = tools
            payload["parallel_tool_calls"] = False
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        if options:
            allowed_options = {
                "temperature", "max_tokens", "top_p",
                "presence_penalty", "frequency_penalty", "stop",
            }
            unknown = set(options) - allowed_options
            if unknown:
                raise GatewayError(f"Unsupported generation options: {sorted(unknown)}")
            payload.update(options)
        try:
            async with self._client_context() as client:
                response = await client.post(
                    self.url, json=payload, headers=self._headers(), timeout=self.timeout_seconds
                )
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise GatewayError(f"Upstream model request failed: {exc}") from exc

        try:
            choice = data["choices"][0]
            message = choice["message"]
            content = message.get("content")
            raw_tool_calls = message.get("tool_calls")
            returned_model = str(data.get("model") or self.model)
            finish_reason = str(choice.get("finish_reason") or "stop")
        except (KeyError, IndexError, AttributeError, TypeError) as exc:
            raise GatewayError("Upstream returned an invalid chat-completion response") from exc

        if content is not None and not isinstance(content, str):
            raise GatewayError("Upstream returned a non-text assistant content value")
        if raw_tool_calls is not None and not isinstance(raw_tool_calls, list):
            raise GatewayError("Upstream returned invalid tool_calls")
        if content is None and not raw_tool_calls:
            raise GatewayError("Upstream returned neither assistant content nor tool calls")
        return Completion(
            content=content or "",
            model=returned_model,
            tool_calls=raw_tool_calls,
            finish_reason=finish_reason,
        )

    async def stream_events(
        self,
        messages: list[dict[str, object]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield structured OpenAI SSE deltas, including partial function tool calls."""
        payload: dict[str, Any] = {"model": self.model, "messages": messages, "stream": True}
        if tools is not None:
            payload["tools"] = tools
            payload["parallel_tool_calls"] = False
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        if options:
            allowed_options = {
                "temperature", "max_tokens", "top_p",
                "presence_penalty", "frequency_penalty", "stop",
            }
            unknown = set(options) - allowed_options
            if unknown:
                raise GatewayError(f"Unsupported generation options: {sorted(unknown)}")
            payload.update(options)

        saw_done = False
        try:
            async with self._client_context() as client:
                async with client.stream(
                    "POST", self.url, json=payload, headers=self._headers(),
                    timeout=self.timeout_seconds,
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line or line.startswith(":") or not line.startswith("data:"):
                            continue
                        raw = line[5:].strip()
                        if raw == "[DONE]":
                            saw_done = True
                            break
                        try:
                            event = json.loads(raw)
                        except json.JSONDecodeError as exc:
                            raise GatewayError("Upstream returned malformed streaming data") from exc
                        if isinstance(event, dict) and event.get("error"):
                            raise GatewayError("Upstream model reported a streaming error")
                        choices = event.get("choices", []) if isinstance(event, dict) else []
                        if not choices:
                            continue
                        choice = choices[0]
                        delta = choice.get("delta", {})
                        if not isinstance(delta, dict):
                            raise GatewayError("Upstream returned an invalid streaming delta")
                        content = delta.get("content")
                        if isinstance(content, str) and content:
                            yield {"content": content}
                        tool_deltas = delta.get("tool_calls")
                        if tool_deltas is not None:
                            if not isinstance(tool_deltas, list):
                                raise GatewayError("Upstream returned invalid tool-call deltas")
                            yield {"tool_calls": tool_deltas}
                        finish_reason = choice.get("finish_reason")
                        if finish_reason is not None:
                            yield {"finish_reason": str(finish_reason)}
        except GatewayError:
            raise
        except httpx.HTTPError as exc:
            raise GatewayError(f"Upstream streaming request failed: {exc}") from exc

        if not saw_done:
            raise GatewayError("Upstream streaming response ended before [DONE]")
        yield {"done": True}

    async def stream(
        self,
        messages: list[dict[str, object]],
        *,
        options: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        """Yield actual assistant text deltas from an upstream OpenAI SSE stream."""
        payload: dict[str, Any] = {"model": self.model, "messages": messages, "stream": True}
        if options:
            allowed_options = {
                "temperature", "max_tokens", "top_p",
                "presence_penalty", "frequency_penalty", "stop",
            }
            unknown = set(options) - allowed_options
            if unknown:
                raise GatewayError(f"Unsupported generation options: {sorted(unknown)}")
            payload.update(options)
        saw_done = False
        try:
            async with self._client_context() as client:
                async with client.stream(
                    "POST", self.url, json=payload, headers=self._headers(),
                    timeout=self.timeout_seconds,
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line or line.startswith(":") or not line.startswith("data:"):
                            continue
                        raw = line[5:].strip()
                        if raw == "[DONE]":
                            saw_done = True
                            break
                        try:
                            event = json.loads(raw)
                        except json.JSONDecodeError as exc:
                            raise GatewayError("Upstream returned malformed streaming data") from exc
                        if isinstance(event, dict) and event.get("error"):
                            raise GatewayError("Upstream model reported a streaming error")
                        choices = event.get("choices", []) if isinstance(event, dict) else []
                        if not choices:
                            continue
                        delta = choices[0].get("delta", {})
                        content = delta.get("content") if isinstance(delta, dict) else None
                        if isinstance(content, str) and content:
                            yield content
        except GatewayError:
            raise
        except httpx.HTTPError as exc:
            raise GatewayError(f"Upstream streaming request failed: {exc}") from exc

        if not saw_done:
            raise GatewayError("Upstream streaming response ended before [DONE]")
