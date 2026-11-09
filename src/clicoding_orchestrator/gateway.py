from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx


@dataclass(frozen=True)
class Completion:
    content: str
    model: str


class ModelGateway(Protocol):
    async def complete(self, messages: list[dict[str, str]]) -> Completion: ...


class GatewayError(RuntimeError):
    pass


class OpenAICompatibleGateway:
    """Transport adapter only; it does not own conversation or task state."""

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

    async def complete(self, messages: list[dict[str, str]]) -> Completion:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(self.url, json=payload, headers=headers)
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
