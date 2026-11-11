import json
from fastapi.testclient import TestClient

from clicoding_orchestrator.app import create_app
from clicoding_orchestrator.config import Settings
from clicoding_orchestrator.database import Database
from clicoding_orchestrator.gateway import Completion, GatewayError
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
    assert [model["id"] for model in response.json()["data"]] == ["CDS_AI"]
    assert "private-model-name" not in response.text
    assert "internal-chat" not in response.text


def test_openai_chat_returns_conversation_id_and_preserves_handoff_context(tmp_path):
    client, gateway = make_client(tmp_path)
    first = client.post(
        "/v1/chat/completions",
        json={
            "model": "CDS_AI",
            "messages": [{"role": "user", "content": "Hello"}],
            "stream": False,
        },
    )
    assert first.status_code == 200
    conversation_id = first.json()["conversation_id"]
    assert first.json()["model"] == "CDS_AI"
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
                "task_type": "coding",
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


def test_direct_chat_streams_incremental_deltas_and_persists_complete_answer(tmp_path):
    class StreamingGateway:
        def __init__(self):
            self.messages = []

        async def complete(self, messages):
            raise AssertionError("streaming chat must not call buffered completion")

        async def stream(self, messages):
            self.messages = messages
            yield "Hello"
            yield " "
            yield "world"

    gateway = StreamingGateway()
    database = Database(str(tmp_path / "streaming.sqlite3"))
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
        settings=Settings(database_path=str(tmp_path / "streaming.sqlite3")),
        gateway=gateway,
        database=database,
        registry=registry,
    )
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "clicoding",
            "messages": [{"role": "user", "content": "Say hello"}],
            "stream": True,
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert '"content":"Hello"' in response.text
    assert '"content":" "' in response.text
    assert '"content":"world"' in response.text
    assert '"finish_reason":"stop"' in response.text
    assert response.text.endswith("data: [DONE]\n\n")
    assert gateway.messages == [{"role": "user", "content": "Say hello"}]

    with database.session() as connection:
        row = connection.execute(
            "SELECT id FROM conversations ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        conversation_id = row["id"]
    conversation = database.get_conversation(conversation_id)
    assert [event["role"] for event in conversation["events"]] == ["user", "assistant"]
    assert conversation["events"][-1]["content"] == "Hello world"
    assert conversation["events"][-1]["producer"] == "internal-chat"


class ToolSequenceGateway:
    def __init__(self, tool_calls):
        self.tool_calls = tool_calls
        self.calls = []

    async def complete(self, messages, *, tools=None, tool_choice=None):
        self.calls.append({
            "messages": messages,
            "tools": tools,
            "tool_choice": tool_choice,
        })
        if len(self.calls) == 1:
            return Completion(
                content="",
                model="tool-model",
                tool_calls=self.tool_calls,
                finish_reason="tool_calls",
            )
        return Completion(content="The file says hello.", model="tool-model")


def tool_definitions():
    return [{
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file in the active workspace",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "minLength": 1}},
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    }]


def test_openai_tool_call_round_trip_persists_and_validates_tool_result(tmp_path):
    tool_calls = [{
        "id": "call_123",
        "type": "function",
        "function": {"name": "read_file", "arguments": '{"path":"README.md"}'},
    }]
    gateway = ToolSequenceGateway(tool_calls)
    database = Database(str(tmp_path / "tools.sqlite3"))
    registry = WorkerRegistry([
        Worker(
            "tool-worker",
            "http://tool-model/v1",
            "tool-model",
            frozenset({"chat", "tool_calling"}),
        )
    ])
    app = create_app(
        settings=Settings(database_path=str(tmp_path / "tools.sqlite3")),
        gateway=gateway,
        database=database,
        registry=registry,
    )
    client = TestClient(app)
    tools = tool_definitions()

    first = client.post(
        "/v1/chat/completions",
        json={
            "model": "CDS_AI",
            "messages": [{"role": "user", "content": "Read README.md"}],
            "tools": tools,
            "tool_choice": "auto",
        },
    )
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["choices"][0]["finish_reason"] == "tool_calls"
    assert first_body["choices"][0]["message"]["content"] is None
    assert first_body["choices"][0]["message"]["tool_calls"] == tool_calls
    conversation_id = first_body["conversation_id"]

    stored = database.list_events(conversation_id)
    assert stored[-1]["metadata"]["tool_calls"] == tool_calls
    assert stored[-1]["role"] == "assistant"

    second = client.post(
        "/v1/chat/completions",
        json={
            "model": "CDS_AI",
            "conversation_id": conversation_id,
            "messages": [{
                "role": "tool",
                "tool_call_id": "call_123",
                "name": "read_file",
                "content": "hello",
            }],
            "tools": tools,
        },
    )
    assert second.status_code == 200
    assert second.json()["choices"][0]["message"]["content"] == "The file says hello."
    assert second.json()["choices"][0]["finish_reason"] == "stop"

    continuation = gateway.calls[1]["messages"]
    assert continuation[-2]["role"] == "assistant"
    assert continuation[-2]["content"] is None
    assert continuation[-2]["tool_calls"] == tool_calls
    assert continuation[-1] == {
        "role": "tool",
        "content": "hello",
        "tool_call_id": "call_123",
        "name": "read_file",
    }


def test_openai_api_rejects_tool_results_without_a_matching_pending_call(tmp_path):
    client, _ = make_client(tmp_path)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "CDS_AI",
            "messages": [{
                "role": "tool",
                "tool_call_id": "call_123",
                "name": "read_file",
                "content": "untrusted result",
            }],
            "tools": tool_definitions(),
        },
    )
    assert response.status_code == 400


def test_openai_api_requires_a_tool_capable_worker_before_dispatch(tmp_path):
    gateway = FakeGateway()
    database = Database(str(tmp_path / "no-tools.sqlite3"))
    registry = WorkerRegistry([
        Worker("chat-only", "http://chat/v1", "chat", frozenset({"chat"}))
    ])
    app = create_app(
        settings=Settings(database_path=str(tmp_path / "no-tools.sqlite3")),
        gateway=gateway,
        database=database,
        registry=registry,
    )
    response = TestClient(app).post(
        "/v1/chat/completions",
        json={
            "model": "CDS_AI",
            "messages": [{"role": "user", "content": "Read README"}],
            "tools": tool_definitions(),
        },
    )
    assert response.status_code == 503
    assert gateway.calls == []


class StreamingToolGateway:
    def __init__(self):
        self.complete_calls = []

    async def stream_events(
        self, messages, *, tools=None, tool_choice=None, options=None
    ):
        yield {"tool_calls": [{
            "index": 0,
            "id": "call_stream_1",
            "type": "function",
            "function": {"name": "read_", "arguments": '{"path":'},
        }]}
        yield {"tool_calls": [{
            "index": 0,
            "function": {"name": "file", "arguments": '"README.md"}'},
        }]}
        yield {"finish_reason": "tool_calls"}
        yield {"done": True}

    async def complete(self, messages, *, tools=None, tool_choice=None, options=None):
        self.complete_calls.append(messages)
        return Completion(content="I read README.md.", model="tool-model")


def test_openai_api_streams_tool_call_deltas_and_accepts_tool_result(tmp_path):
    gateway = StreamingToolGateway()
    database = Database(str(tmp_path / "stream-tools.sqlite3"))
    conversation_id = "stream-tool-conversation"
    database.get_or_create_conversation(conversation_id)
    registry = WorkerRegistry([
        Worker(
            "tool-worker",
            "http://tool-model/v1",
            "tool-model",
            frozenset({"chat", "tool_calling"}),
        )
    ])
    app = create_app(
        settings=Settings(database_path=str(tmp_path / "stream-tools.sqlite3")),
        gateway=gateway,
        database=database,
        registry=registry,
    )
    client = TestClient(app)
    tools = tool_definitions()

    streamed = client.post(
        "/v1/chat/completions",
        json={
            "model": "CDS_AI",
            "conversation_id": conversation_id,
            "messages": [{"role": "user", "content": "Read README.md"}],
            "tools": tools,
            "tool_choice": "auto",
            "stream": True,
        },
    )
    assert streamed.status_code == 200
    assert "data: [DONE]" in streamed.text
    chunks = [
        json.loads(line[6:])
        for line in streamed.text.splitlines()
        if line.startswith("data: {")
    ]
    tool_chunks = [
        chunk for chunk in chunks
        if chunk["choices"][0]["delta"].get("tool_calls")
    ]
    assert len(tool_chunks) == 2
    assert chunks[-1]["choices"][0]["finish_reason"] == "tool_calls"

    events = database.list_events(conversation_id)
    assert events[-1]["metadata"]["tool_calls"][0]["id"] == "call_stream_1"
    assert events[-1]["metadata"]["tool_calls"][0]["function"] == {
        "name": "read_file",
        "arguments": '{"path":"README.md"}',
    }

    continued = client.post(
        "/v1/chat/completions",
        json={
            "model": "CDS_AI",
            "conversation_id": conversation_id,
            "messages": [{
                "role": "tool",
                "tool_call_id": "call_stream_1",
                "name": "read_file",
                "content": "README contents",
            }],
            "tools": tools,
        },
    )
    assert continued.status_code == 200
    assert continued.json()["choices"][0]["message"]["content"] == "I read README.md."
    assert gateway.complete_calls[0][-1]["role"] == "tool"


class OptionsGateway:
    def __init__(self):
        self.options = None
        self.calls = 0

    async def complete(self, messages, *, options=None):
        self.calls += 1
        self.options = options
        return Completion(content="option-aware reply", model="chat-model")


def test_openai_api_forwards_supported_generation_options(tmp_path):
    gateway = OptionsGateway()
    database = Database(str(tmp_path / "options.sqlite3"))
    registry = WorkerRegistry([
        Worker(
            "chat",
            "http://chat/v1",
            "chat-model",
            frozenset({"chat"}),
            context_window_tokens=4096,
            max_output_tokens=512,
        )
    ])
    app = create_app(
        settings=Settings(database_path=str(tmp_path / "options.sqlite3")),
        gateway=gateway,
        database=database,
        registry=registry,
    )
    response = TestClient(app).post(
        "/v1/chat/completions",
        json={
            "model": "CDS_AI",
            "messages": [{"role": "user", "content": "Hello"}],
            "temperature": 0.2,
            "max_tokens": 256,
            "top_p": 0.8,
            "presence_penalty": 0.1,
            "frequency_penalty": -0.2,
            "stop": ["END"],
        },
    )
    assert response.status_code == 200
    assert gateway.options == {
        "temperature": 0.2,
        "max_tokens": 256,
        "top_p": 0.8,
        "presence_penalty": 0.1,
        "frequency_penalty": -0.2,
        "stop": ["END"],
    }


def test_openai_api_rejects_output_limit_above_worker_budget(tmp_path):
    gateway = OptionsGateway()
    database = Database(str(tmp_path / "output-limit.sqlite3"))
    registry = WorkerRegistry([
        Worker(
            "chat",
            "http://chat/v1",
            "chat-model",
            frozenset({"chat"}),
            context_window_tokens=4096,
            max_output_tokens=512,
        )
    ])
    app = create_app(
        settings=Settings(database_path=str(tmp_path / "output-limit.sqlite3")),
        gateway=gateway,
        database=database,
        registry=registry,
    )
    response = TestClient(app).post(
        "/v1/chat/completions",
        json={
            "model": "CDS_AI",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 1024,
        },
    )
    assert response.status_code == 400
    assert gateway.calls == 0


def test_openai_api_rejects_context_overflow_without_truncating(tmp_path):
    gateway = OptionsGateway()
    database = Database(str(tmp_path / "context-limit.sqlite3"))
    registry = WorkerRegistry([
        Worker(
            "chat",
            "http://chat/v1",
            "chat-model",
            frozenset({"chat"}),
            context_window_tokens=2048,
            max_output_tokens=256,
        )
    ])
    app = create_app(
        settings=Settings(database_path=str(tmp_path / "context-limit.sqlite3")),
        gateway=gateway,
        database=database,
        registry=registry,
    )
    response = TestClient(app).post(
        "/v1/chat/completions",
        json={
            "model": "CDS_AI",
            "messages": [{"role": "user", "content": "x" * 5000}],
        },
    )
    assert response.status_code == 413
    assert gateway.calls == 0


def test_openai_api_preflights_context_overflow_before_streaming(tmp_path):
    gateway = OptionsGateway()
    database = Database(str(tmp_path / "stream-context-limit.sqlite3"))
    registry = WorkerRegistry([
        Worker(
            "chat",
            "http://chat/v1",
            "chat-model",
            frozenset({"chat"}),
            context_window_tokens=2048,
            max_output_tokens=256,
        )
    ])
    app = create_app(
        settings=Settings(database_path=str(tmp_path / "stream-context-limit.sqlite3")),
        gateway=gateway,
        database=database,
        registry=registry,
    )
    response = TestClient(app).post(
        "/v1/chat/completions",
        json={
            "model": "CDS_AI",
            "messages": [{"role": "user", "content": "x" * 5000}],
            "stream": True,
        },
    )
    assert response.status_code == 413
    assert gateway.calls == 0


class FailOnceGateway:
    def __init__(self):
        self.calls = 0

    async def complete(self, messages, *, tools=None, tool_choice=None, options=None):
        self.calls += 1
        if self.calls == 1:
            raise GatewayError("temporary upstream failure")
        return Completion(content="retry succeeded", model="chat-model")


def test_failed_user_turn_retry_reuses_the_unfinished_input_event(tmp_path):
    gateway = FailOnceGateway()
    database = Database(str(tmp_path / "retry.sqlite3"))
    conversation_id = "retry-user-turn"
    database.get_or_create_conversation(conversation_id)
    registry = WorkerRegistry([
        Worker("chat", "http://chat/v1", "chat-model", frozenset({"chat"}))
    ])
    app = create_app(
        settings=Settings(database_path=str(tmp_path / "retry.sqlite3")),
        gateway=gateway,
        database=database,
        registry=registry,
    )
    client = TestClient(app)
    request = {
        "model": "CDS_AI",
        "conversation_id": conversation_id,
        "messages": [{"role": "user", "content": "same request"}],
    }

    failed = client.post("/v1/chat/completions", json=request)
    assert failed.status_code == 502
    assert [event["role"] for event in database.list_events(conversation_id)] == ["user"]

    retried = client.post("/v1/chat/completions", json=request)
    assert retried.status_code == 200
    assert retried.json()["choices"][0]["message"]["content"] == "retry succeeded"
    events = database.list_events(conversation_id)
    assert [event["role"] for event in events] == ["user", "assistant"]
    assert gateway.calls == 2


def test_failed_tool_continuation_retry_reuses_the_exact_tool_result(tmp_path):
    gateway = FailOnceGateway()
    database = Database(str(tmp_path / "retry-tool.sqlite3"))
    conversation_id = "retry-tool-turn"
    database.get_or_create_conversation(conversation_id)
    call = {
        "id": "call_retry_1",
        "type": "function",
        "function": {"name": "read_file", "arguments": '{"path":"README.md"}'},
    }
    database.append_event(conversation_id, "user", "Read README", "user")
    database.append_event(
        conversation_id,
        "assistant",
        "",
        "chat-model",
        metadata={"tool_calls": [call], "finish_reason": "tool_calls"},
    )
    registry = WorkerRegistry([
        Worker(
            "tool-worker",
            "http://tool/v1",
            "tool-model",
            frozenset({"chat", "tool_calling"}),
        )
    ])
    app = create_app(
        settings=Settings(database_path=str(tmp_path / "retry-tool.sqlite3")),
        gateway=gateway,
        database=database,
        registry=registry,
    )
    client = TestClient(app)
    request = {
        "model": "CDS_AI",
        "conversation_id": conversation_id,
        "messages": [{
            "role": "tool",
            "tool_call_id": "call_retry_1",
            "name": "read_file",
            "content": "same tool result",
        }],
        "tools": tool_definitions(),
    }

    failed = client.post("/v1/chat/completions", json=request)
    assert failed.status_code == 502
    retried = client.post("/v1/chat/completions", json=request)
    assert retried.status_code == 200
    events = database.list_events(conversation_id)
    assert [event["role"] for event in events] == ["user", "assistant", "tool", "assistant"]
    assert events[2]["content"] == "same tool result"
    assert gateway.calls == 2


def test_openai_api_regenerates_only_the_latest_matching_turn(tmp_path):
    client, gateway = make_client(tmp_path)
    database = Database(str(tmp_path / "conversations.sqlite3"))
    conversation_id = database.create_conversation()["id"]
    request = {
        "model": "CDS_AI",
        "conversation_id": conversation_id,
        "messages": [{"role": "user", "content": "Explain the design"}],
    }

    first = client.post("/v1/chat/completions", json=request)
    assert first.status_code == 200
    assert first.json()["choices"][0]["message"]["content"] == "answer-1"

    regenerated = client.post(
        "/v1/chat/completions",
        json=request,
        headers={"X-CLICoding-Operation": "regenerate"},
    )
    assert regenerated.status_code == 200
    assert regenerated.json()["choices"][0]["message"]["content"] == "answer-2"
    events = database.list_events(conversation_id)
    assert [event["role"] for event in events] == ["user", "assistant"]
    assert events[-1]["content"] == "answer-2"
    assert gateway.calls[1] == [{"role": "user", "content": "Explain the design"}]


def test_openai_api_rejects_regeneration_for_nonmatching_latest_input(tmp_path):
    client, _ = make_client(tmp_path)
    database = Database(str(tmp_path / "conversations.sqlite3"))
    conversation_id = database.create_conversation()["id"]
    first = client.post(
        "/v1/chat/completions",
        json={
            "model": "CDS_AI",
            "conversation_id": conversation_id,
            "messages": [{"role": "user", "content": "Original question"}],
        },
    )
    assert first.status_code == 200
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "CDS_AI",
            "conversation_id": conversation_id,
            "messages": [{"role": "user", "content": "Different question"}],
        },
        headers={"X-CLICoding-Operation": "regenerate"},
    )
    assert response.status_code == 409
    assert [event["role"] for event in database.list_events(conversation_id)] == [
        "user", "assistant"
    ]


def test_openai_api_can_regenerate_a_previous_tool_call(tmp_path):
    gateway = ToolSequenceGateway([{
        "id": "call_regen_1",
        "type": "function",
        "function": {"name": "read_file", "arguments": '{"path":"README.md"}'},
    }])
    database = Database(str(tmp_path / "tool-regenerate.sqlite3"))
    conversation_id = database.create_conversation()["id"]
    registry = WorkerRegistry([
        Worker(
            "tool-worker",
            "http://tool/v1",
            "tool-model",
            frozenset({"chat", "tool_calling"}),
        )
    ])
    app = create_app(
        settings=Settings(database_path=str(tmp_path / "tool-regenerate.sqlite3")),
        gateway=gateway,
        database=database,
        registry=registry,
    )
    client = TestClient(app)
    request = {
        "model": "CDS_AI",
        "conversation_id": conversation_id,
        "messages": [{"role": "user", "content": "Read README"}],
        "tools": tool_definitions(),
    }
    first = client.post("/v1/chat/completions", json=request)
    assert first.status_code == 200
    assert first.json()["choices"][0]["finish_reason"] == "tool_calls"

    regenerated = client.post(
        "/v1/chat/completions",
        json=request,
        headers={"X-CLICoding-Operation": "regenerate"},
    )
    assert regenerated.status_code == 200
    assert regenerated.json()["choices"][0]["message"]["content"] == "The file says hello."
    events = database.list_events(conversation_id)
    assert [event["role"] for event in events] == ["user", "assistant"]
    assert events[-1]["content"] == "The file says hello."
