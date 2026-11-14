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

def test_compaction_cutoff_preserves_the_latest_complete_user_turns():
    from clicoding_orchestrator.context import select_compaction_cutoff

    events = []
    for index in range(5):
        events.extend([
            {"role": "user", "content": f"question-{index}"},
            {"role": "assistant", "content": f"answer-{index}"},
        ])
    cutoff = select_compaction_cutoff(events, keep_recent_user_turns=3)
    assert cutoff == 4
    assert events[cutoff]["role"] == "user"
    assert [event["content"] for event in events[cutoff:]] == [
        "question-2", "answer-2", "question-3", "answer-3", "question-4", "answer-4"
    ]


def test_compaction_cutoff_does_not_summarize_when_recent_history_is_short():
    from clicoding_orchestrator.context import select_compaction_cutoff

    assert select_compaction_cutoff([
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "answer"},
    ]) is None


def test_summary_redaction_masks_common_credential_shapes():
    from clicoding_orchestrator.context import redact_sensitive_text

    value = (
        "api_key=super-secret-value password:topsecret "
        "Bearer abcdefghijklmnopqrstuvwxyz ghp_123456789012345678901234"
    )
    redacted = redact_sensitive_text(value)
    assert "super-secret-value" not in redacted
    assert "topsecret" not in redacted
    assert "abcdefghijklmnopqrstuvwxyz" not in redacted
    assert "ghp_123456789012345678901234" not in redacted



def test_summary_redaction_handles_json_assignments_and_nested_tool_metadata():
    from clicoding_orchestrator.context import redact_sensitive_data, redact_sensitive_text

    text = redact_sensitive_text('{"api_key":"json-secret-value","password":"another-secret"}')
    assert "json-secret-value" not in text
    assert "another-secret" not in text

    metadata = {
        "tool_calls": [{
            "function": {
                "name": "configure",
                "arguments": '{"access_token":"nested-secret","safe":"kept"}',
            }
        }]
    }
    redacted = redact_sensitive_data(metadata)
    assert "nested-secret" not in redacted["tool_calls"][0]["function"]["arguments"]
    assert "kept" in redacted["tool_calls"][0]["function"]["arguments"]
