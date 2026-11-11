import pytest

from clicoding_orchestrator.context import (
    IMAGE_TOKEN_RESERVE,
    ContextBudgetExceeded,
    ensure_context_budget,
    estimate_context_tokens,
)


def test_context_estimate_is_conservative_for_multilingual_text():
    messages = [
        {"role": "system", "content": "You are CDS_AI."},
        {"role": "user", "content": "سلام، لطفاً این پروژه را بررسی کن."},
    ]
    estimate = estimate_context_tokens(messages)
    assert estimate >= len("سلام، لطفاً این پروژه را بررسی کن.".encode("utf-8")) // 2
    assert ensure_context_budget(
        messages,
        context_window_tokens=4096,
        reserved_output_tokens=512,
    ) == estimate


def test_image_payload_is_counted_as_image_tokens_not_base64_text():
    image = "data:image/png;base64," + ("A" * 2_000_000)
    estimate = estimate_context_tokens([{
        "role": "user",
        "content": [{"type": "image_url", "image_url": {"url": image}}],
    }])
    assert estimate == 8 + IMAGE_TOKEN_RESERVE


def test_context_budget_rejects_without_silently_truncating_messages():
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "x" * 5000},
    ]
    with pytest.raises(ContextBudgetExceeded) as exc:
        ensure_context_budget(
            messages,
            context_window_tokens=2048,
            reserved_output_tokens=256,
        )
    assert exc.value.estimated_tokens > exc.value.input_budget_tokens
    assert len(messages) == 2


def test_context_budget_rejects_invalid_limits():
    with pytest.raises(ValueError):
        ensure_context_budget([], context_window_tokens=512, reserved_output_tokens=64)
    with pytest.raises(ValueError):
        ensure_context_budget([], context_window_tokens=2048, reserved_output_tokens=2048)
