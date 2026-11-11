from __future__ import annotations

import json
import math
from typing import Any


# Vision token cost varies by model and image resolution. Reserve a conservative
# fixed amount per attached image until a runtime-specific tokenizer is available.
IMAGE_TOKEN_RESERVE = 4096


class ContextBudgetExceeded(ValueError):
    def __init__(self, estimated_tokens: int, input_budget_tokens: int):
        self.estimated_tokens = estimated_tokens
        self.input_budget_tokens = input_budget_tokens
        super().__init__(
            "conversation context exceeds the configured worker budget "
            f"(estimated {estimated_tokens} tokens; input budget {input_budget_tokens})"
        )


def _conservative_text_tokens(value: str) -> int:
    # Two UTF-8 bytes per token is intentionally conservative for multilingual
    # content; this is a safety estimate, not a model-specific tokenizer.
    return math.ceil(len(value.encode("utf-8")) / 2)


def estimate_message_tokens(message: dict[str, Any]) -> int:
    total = 8  # role and message framing overhead
    content = message.get("content")
    if isinstance(content, str):
        total += _conservative_text_tokens(content)
    elif isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text" and isinstance(part.get("text"), str):
                total += _conservative_text_tokens(part["text"])
            elif part.get("type") == "image_url":
                total += IMAGE_TOKEN_RESERVE
    elif content is not None:
        total += _conservative_text_tokens(str(content))

    for key in ("tool_calls", "tool_call_id", "name"):
        if key in message:
            total += _conservative_text_tokens(
                json.dumps(message[key], ensure_ascii=False, separators=(",", ":"))
            )
    return total


def estimate_context_tokens(messages: list[dict[str, Any]]) -> int:
    return sum(estimate_message_tokens(message) for message in messages)


def ensure_context_budget(
    messages: list[dict[str, Any]],
    *,
    context_window_tokens: int,
    reserved_output_tokens: int,
) -> int:
    if context_window_tokens < 1024:
        raise ValueError("context_window_tokens must be at least 1024")
    if reserved_output_tokens < 1 or reserved_output_tokens >= context_window_tokens:
        raise ValueError("reserved_output_tokens must be positive and below the context window")
    estimate = estimate_context_tokens(messages)
    input_budget = context_window_tokens - reserved_output_tokens
    if estimate > input_budget:
        raise ContextBudgetExceeded(estimate, input_budget)
    return estimate
