from clicoding_orchestrator.policy import CDS_AI_SYSTEM_PROMPT, effective_system_messages


def test_cds_ai_policy_is_appended_after_client_system_messages():
    messages = effective_system_messages([
        {"role": "system", "content": "Client-provided context"},
        {"role": "user", "content": "not a system message"},
    ])
    assert messages[0] == {"role": "system", "content": "Client-provided context"}
    assert messages[-1]["content"] == CDS_AI_SYSTEM_PROMPT
    assert "You are CDS_AI" in messages[-1]["content"]
    assert "same language" in messages[-1]["content"]
    assert "right-to-left" in messages[-1]["content"]
    assert "Separate verified facts from assumptions" in messages[-1]["content"]
    assert "every explicit requirement" in messages[-1]["content"]
    assert "never present partial work as fully completed" in messages[-1]["content"]


def test_cds_ai_policy_is_applied_without_client_system_prompt():
    messages = effective_system_messages(None)
    assert len(messages) == 1
    assert messages[0]["content"] == CDS_AI_SYSTEM_PROMPT
