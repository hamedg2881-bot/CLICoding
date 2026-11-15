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


def test_architecture_reference_is_added_only_for_architecture_questions(tmp_path, monkeypatch):
    reference = tmp_path / "CURRENT_DEPLOYMENT.md"
    reference.write_text("# Verified deployment\nWorker alias: qwen-coder", encoding="utf-8")
    monkeypatch.setenv("CLICODING_ARCHITECTURE_CONTEXT_FILE", str(reference))

    messages = effective_system_messages(None, user_content="توضیح معماری و Workerهای سیستم")

    assert len(messages) == 2
    assert "Worker alias: qwen-coder" in messages[0]["content"]
    assert messages[-1]["content"] == CDS_AI_SYSTEM_PROMPT


def test_architecture_reference_is_not_added_to_ordinary_or_image_turns(tmp_path, monkeypatch):
    reference = tmp_path / "CURRENT_DEPLOYMENT.md"
    reference.write_text("Worker alias: qwen-coder", encoding="utf-8")
    monkeypatch.setenv("CLICODING_ARCHITECTURE_CONTEXT_FILE", str(reference))

    ordinary = effective_system_messages(None, user_content="سلام، حالت چطوره؟")
    image_turn = effective_system_messages(
        None,
        user_content=[
            {"type": "text", "text": "این تصویر معماری چیست؟"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
        ],
    )

    assert len(ordinary) == 1
    assert len(image_turn) == 1
