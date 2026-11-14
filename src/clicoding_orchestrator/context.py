from __future__ import annotations

import json
import math
import re
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



def select_compaction_cutoff(
    events: list[dict[str, Any]],
    *,
    keep_recent_user_turns: int = 3,
) -> int | None:
    """Return a boundary before the oldest retained recent user turn."""
    if keep_recent_user_turns < 1:
        raise ValueError("keep_recent_user_turns must be positive")
    user_positions = [
        index for index, event in enumerate(events)
        if event.get("role") == "user"
    ]
    if len(user_positions) <= keep_recent_user_turns:
        return None
    cutoff = user_positions[-keep_recent_user_turns]
    return cutoff if cutoff > 0 else None


_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret|authorization)([\"']?\s*[:=]\s*[\"']?)([^\"'\s,;]+)"
)
_BEARER_TOKEN = re.compile(r"(?i)Bearer\s+[A-Za-z0-9._~+/=-]{16,}")
_GITHUB_TOKEN = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")
_OPENAI_TOKEN = re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")
_AWS_ACCESS_KEY = re.compile(r"\bAKIA[0-9A-Z]{16}\b")

def redact_sensitive_text(value: str) -> str:
    """Redact common credential-shaped values before storing generated summaries."""
    redacted = _SECRET_ASSIGNMENT.sub(r"\\1\\2[REDACTED]", value)
    redacted = _BEARER_TOKEN.sub("Bearer [REDACTED]", redacted)
    redacted = _GITHUB_TOKEN.sub("[REDACTED_GITHUB_TOKEN]", redacted)
    redacted = _OPENAI_TOKEN.sub("[REDACTED_API_TOKEN]", redacted)
    redacted = _AWS_ACCESS_KEY.sub("[REDACTED_AWS_KEY]", redacted)
    return redacted



def redact_sensitive_data(value: Any) -> Any:
    """Recursively redact credential-like mapping values and nested text."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            normalized = str(key).lower().replace("_", "").replace("-", "")
            if any(
                marker in normalized
                for marker in (
                    "apikey", "accesstoken", "refreshtoken", "token",
                    "password", "passwd", "secret", "authorization",
                    "privatekey", "accesskey",
                )
            ):
                result[key] = "[REDACTED]"
            else:
                result[key] = redact_sensitive_data(item)
        return result
    if isinstance(value, list):
        return [redact_sensitive_data(item) for item in value]
    if isinstance(value, str):
        return redact_sensitive_text(value)
    return value
