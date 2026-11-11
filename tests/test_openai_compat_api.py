from fastapi.testclient import TestClient

from clicoding_orchestrator.app import create_app
from clicoding_orchestrator.config import Settings
from clicoding_orchestrator.database import Database
from clicoding_orchestrator.gateway import Completion
from clicoding_orchestrator.registry import Worker, WorkerRegistry


class FakeGateway:
    def __init__(self):
        self.calls = []

    async def complete(self, messages):
        self.calls.append(messages)
        return Completion(content=f"reply-{len(self.calls)}", model="internal-model")


def make_client(tmp_path):
    gateway = FakeGateway()
    database = Database(str(tmp_path / "openai.sqlite3"))
    registry = WorkerRegistry(
        [
            Worker(
                "internal-chat",
                "http://internal/v1",
                "private-model-name",
                frozenset({"chat"}),
            )
        ]
    )
    app = create_app(
        settings=Settings(database_path=str(tmp_path / "openai.sqlite3")),
        gateway=gateway,
        database=database,
        registry=registry,
    )
    return TestClient(app), gateway


def test_public_models_exposes_only_stable_alias(tmp_path):
    client, _ = make_client(tmp_path)
    response = client.get("/v1/models")

    assert response.status_code == 200
    assert [model["id"] for model in response.json()["data"]] == ["clicoding"]
    assert "private-model-name" not in response.text
    assert "internal-chat" not in response.text


def test_openai_chat_returns_conversation_id_and_preserves_handoff_context(tmp_path):
    client, gateway = make_client(tmp_path)
    first = client.post(
        "/v1/chat/completions",
        json={
            "model": "clicoding",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": False,
        },
    )
    assert first.status_code == 200
    conversation_id = first.json()["conversation_id"]
    assert first.json()["model"] == "clicoding"
    assert first.json()["choices"][0]["message"]["content"] == "reply-1"

    second = client.post(
        "/v1/chat/completions",
        json={
            "model": "clicoding",
            "conversation_id": conversation_id,
            "messages": [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "reply-1"},
                {"role": "user", "content": "Continue this"},
            ],
            "stream": False,
        },
    )
    assert second.status_code == 200
    assert second.json()["conversation_id"] == conversation_id
    assert gateway.calls[1] == [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "reply-1"},
        {"role": "user", "content": "Continue this"},
    ]


def test_openai_chat_applies_system_messages_and_imports_initial_history(tmp_path):
    client, gateway = make_client(tmp_path)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "clicoding",
            "messages": [
                {"role": "system", "content": "Be concise"},
                {"role": "user", "content": "Earlier question"},
                {"role": "assistant", "content": "Earlier answer"},
                {"role": "user", "content": "Current question"},
            ],
        },
    )
    assert response.status_code == 200
    assert gateway.calls[0] == [
        {"role": "system", "content": "Be concise"},
        {"role": "user", "content": "Earlier question"},
        {"role": "assistant", "content": "Earlier answer"},
        {"role": "user", "content": "Current question"},
    ]


def test_openai_chat_supports_sse_and_rejects_unknown_model(tmp_path):
    client, _ = make_client(tmp_path)
    streaming = client.post(
        "/v1/chat/completions",
        json={
            "model": "clicoding",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": True,
        },
    )
    unknown = client.post(
        "/v1/chat/completions",
        json={
            "model": "internal-chat",
            "messages": [{"role": "user", "content": "Hello"}],
        },
    )

    assert streaming.status_code == 200
    assert streaming.headers["content-type"].startswith("text/event-stream")
    assert '"object":"chat.completion.chunk"' in streaming.text
    assert '"content":"reply-1"' in streaming.text
    assert "data: [DONE]" in streaming.text
    assert unknown.status_code == 404


def test_openwebui_chat_headers_preserve_session_and_isolate_users(tmp_path):
    client, _ = make_client(tmp_path)
    headers = {
        "X-CLICoding-Chat-ID": "open-webui-chat-123",
        "X-CLICoding-User-ID": "open-webui-user-a",
    }

    first = client.post(
        "/v1/chat/completions",
        headers=headers,
        json={
            "model": "clicoding",
            "messages": [{"role": "user", "content": "First turn"}],
        },
    )
    second = client.post(
        "/v1/chat/completions",
        headers=headers,
        json={
            "model": "clicoding",
            "messages": [{"role": "user", "content": "Second turn"}],
        },
    )
    other_user = client.post(
        "/v1/chat/completions",
        headers={
            "X-CLICoding-Chat-ID": "open-webui-chat-123",
            "X-CLICoding-User-ID": "open-webui-user-b",
        },
        json={
            "model": "clicoding",
            "messages": [{"role": "user", "content": "Separate user"}],
        },
    )

    assert first.status_code == second.status_code == other_user.status_code == 200
    first_id = first.json()["conversation_id"]
    assert second.json()["conversation_id"] == first_id
    assert other_user.json()["conversation_id"] != first_id


def test_openwebui_session_headers_must_be_provided_together(tmp_path):
    client, _ = make_client(tmp_path)
    response = client.post(
        "/v1/chat/completions",
        headers={"X-CLICoding-Chat-ID": "chat-only"},
        json={
            "model": "clicoding",
            "messages": [{"role": "user", "content": "Hello"}],
        },
    )
    assert response.status_code == 400


def test_configured_bearer_key_protects_api_but_not_health(tmp_path):
    gateway = FakeGateway()
    database = Database(str(tmp_path / "auth.sqlite3"))
    app = create_app(
        settings=Settings(
            database_path=str(tmp_path / "auth.sqlite3"),
            api_key="test-only-secret-0123456789-abcdef-012345",
        ),
        gateway=gateway,
        database=database,
    )
    client = TestClient(app)

    assert client.get("/health").status_code == 200
    assert client.get("/v1/models").status_code == 401
    assert client.post(
        "/v1/chat/completions",
        json={"model": "clicoding", "messages": [{"role": "user", "content": "Hi"}]},
    ).status_code == 401

    authorized = client.get(
        "/v1/models", headers={"Authorization": "Bearer test-only-secret-0123456789-abcdef-012345"}
    )
    assert authorized.status_code == 200
    assert "test-only-secret-0123456789-abcdef-012345" not in authorized.text
    lowercase_scheme = client.get(
        "/v1/models",
        headers={"Authorization": "bearer test-only-secret-0123456789-abcdef-012345"},
    )
    assert lowercase_scheme.status_code == 200


def test_settings_rejects_short_inbound_api_key():
    import pytest

    with pytest.raises(ValueError, match="at least 32"):
        Settings(api_key="too-short")



def test_openai_image_payload_is_forwarded_to_vision_worker_and_persisted(tmp_path):
    gateway = FakeGateway()
    database = Database(str(tmp_path / "image.sqlite3"))
    registry = WorkerRegistry(
        [
            Worker(
                "multimodal-primary",
                "http://vision/v1",
                "qwen3.6-35b-a3b",
                frozenset({"chat", "vision"}),
            )
        ]
    )
    app = create_app(
        settings=Settings(database_path=str(tmp_path / "image.sqlite3")),
        gateway=gateway,
        database=database,
        registry=registry,
    )
    client = TestClient(app)
    image_uri = "data:image/png;base64,aGVsbG8="
    content = [
        {"type": "text", "text": "Describe this screenshot"},
        {"type": "image_url", "image_url": {"url": image_uri}},
    ]

    response = client.post(
        "/v1/chat/completions",
        json={"model": "clicoding", "messages": [{"role": "user", "content": content}]},
    )

    assert response.status_code == 200
    assert gateway.calls[0][-1]["content"] == content
    conversation_id = response.json()["conversation_id"]
    saved = database.list_events(conversation_id)
    assert saved[0]["content"].startswith("__CLICODING_MULTIMODAL_V1__")
    restored = client.get(f"/api/v1/conversations/{conversation_id}")
    assert restored.json()["events"][0]["content"] == content

    follow_up = client.post(
        "/v1/chat/completions",
        json={
            "model": "clicoding",
            "conversation_id": conversation_id,
            "messages": [{"role": "user", "content": "Explain the highlighted section"}],
        },
    )
    assert follow_up.status_code == 200
    assert any(
        part.get("type") == "image_url"
        for message in gateway.calls[1]
        if isinstance(message.get("content"), list)
        for part in message["content"]
    )


def test_openai_image_payload_rejects_remote_urls(tmp_path):
    client, _ = make_client(tmp_path)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "clicoding",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": "https://example.com/image.png"}}
                    ],
                }
            ],
        },
    )
    assert response.status_code == 422


def test_parallelism_defaults_to_three_and_rejects_more_than_three():
    import pytest

    assert Settings().max_parallel_tasks == 3
    with pytest.raises(ValueError, match="between 1 and 3"):
        Settings(max_parallel_tasks=4)


def test_get_or_create_conversation_is_idempotent(tmp_path):
    database = Database(str(tmp_path / "stable-conversation.sqlite3"))
    first = database.get_or_create_conversation("stable-open-webui-session")
    second = database.get_or_create_conversation("stable-open-webui-session")

    assert first["id"] == second["id"] == "stable-open-webui-session"
    assert first["created_at"] == second["created_at"]


def test_orchestration_failure_is_logged_without_exposing_request_content(tmp_path, caplog):
    import logging

    class BrokenOrchestrator:
        async def run(
            self, conversation_id, user_request, conversation_context="", user_content=None
        ):
            raise RuntimeError("planner transport failed")

    database = Database(str(tmp_path / "orchestration-error.sqlite3"))
    app = create_app(
        settings=Settings(database_path=str(tmp_path / "orchestration-error.sqlite3")),
        database=database,
        orchestrator=BrokenOrchestrator(),
    )
    client = TestClient(app)
    private_request = "PRIVATE_REQUEST_CONTENT_MUST_NOT_BE_LOGGED"

    with caplog.at_level(logging.ERROR, logger="clicoding_orchestrator.app"):
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "clicoding",
                "messages": [{"role": "user", "content": private_request}],
            },
        )

    assert response.status_code == 502
    assert response.json() == {"detail": "Multi-model orchestration failed"}
    assert "Multi-model orchestration failed" in caplog.text
    assert "conversation_id=" in caplog.text
    assert "RuntimeError: planner transport failed" in caplog.text
    assert private_request not in caplog.text
    assert any(record.exc_info for record in caplog.records)


def test_ordinary_chat_bypasses_planner_even_when_orchestration_is_enabled(tmp_path):
    class PlannerMustNotRun:
        async def run(self, *args, **kwargs):
            raise AssertionError("ordinary chat must not invoke the Planner")

    gateway = FakeGateway()
    database = Database(str(tmp_path / "conversation-first.sqlite3"))
    registry = WorkerRegistry(
        [
            Worker(
                "internal-chat",
                "http://internal/v1",
                "private-model-name",
                frozenset({"chat"}),
            )
        ]
    )
    app = create_app(
        settings=Settings(
            database_path=str(tmp_path / "conversation-first.sqlite3"),
            enable_planning=True,
        ),
        gateway=gateway,
        database=database,
        registry=registry,
        orchestrator=PlannerMustNotRun(),
    )
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "clicoding",
            "messages": [{"role": "user", "content": "سلام"}],
        },
    )

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "reply-1"
    assert response.json()["x_clicoding"]["orchestrated"] is False
    assert response.json()["x_clicoding"]["execution_id"] is None
    assert gateway.calls == [[{"role": "user", "content": "سلام"}]]


def test_explicit_specialist_task_uses_orchestrator(tmp_path):
    class RecordingOrchestrator:
        def __init__(self):
            self.calls = []

        async def run(self, conversation_id, user_request, context, user_content=None):
            self.calls.append((conversation_id, user_request, context, user_content))
            from types import SimpleNamespace
            return SimpleNamespace(answer="specialist-result", execution_id="execution-123")

    orchestrator = RecordingOrchestrator()
    gateway = FakeGateway()
    database = Database(str(tmp_path / "specialist-route.sqlite3"))
    registry = WorkerRegistry(
        [
            Worker(
                "internal-chat",
                "http://internal/v1",
                "private-model-name",
                frozenset({"chat"}),
            )
        ]
    )
    app = create_app(
        settings=Settings(database_path=str(tmp_path / "specialist-route.sqlite3")),
        gateway=gateway,
        database=database,
        registry=registry,
        orchestrator=orchestrator,
    )
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "clicoding",
            "task_type": "coding",
            "messages": [{"role": "user", "content": "Implement this function"}],
        },
    )

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "specialist-result"
    assert response.json()["x_clicoding"]["orchestrated"] is True
    assert response.json()["x_clicoding"]["execution_id"] == "execution-123"
    assert len(orchestrator.calls) == 1
    assert gateway.calls == []
