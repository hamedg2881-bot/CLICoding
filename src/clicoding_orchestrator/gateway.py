from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
import json
from typing import Protocol

import httpx


@dataclass(frozen=True)
class Completion:
    content: str
    model: str


class ModelGateway(Protocol):
    async def complete(self, messages: list[dict[str, object]]) -> Completion: ...

    def stream(self, messages: list[dict[str, object]]) -> AsyncIterator[str]: ...


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
    ):
        self.url = f"{base_url.rstrip('/')}/chat/completions"
        self.model = model
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def complete(self, messages: list[dict[str, object]]) -> Completion:
        payload = {"model": self.model, "messages": messages, "stream": False}
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(self.url, json=payload, headers=self._headers())
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise GatewayError(f"Upstream model request failed: {exc}") from exc

        try:
            message = data["choices"][0]["message"]
            content = message["content"]
            returned_model = str(data.get("model") or self.model)
        except (KeyError, IndexError, TypeError) as exc:
            raise GatewayError("Upstream returned an invalid chat-completion response") from exc

        if not isinstance(content, str):
            raise GatewayError("The first slice supports text-only assistant responses")
        return Completion(content=content, model=returned_model)

    async def stream(self, messages: list[dict[str, object]]) -> AsyncIterator[str]:
        """Yield actual assistant text deltas from an upstream OpenAI SSE stream."""
        payload = {"model": self.model, "messages": messages, "stream": True}
        saw_done = False
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                async with client.stream(
                    "POST", self.url, json=payload, headers=self._headers()
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
