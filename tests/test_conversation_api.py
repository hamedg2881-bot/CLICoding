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
        return Completion(
            content=f"answer-{len(self.calls)}",
            model=f"test-model-{len(self.calls)}",
        )


def make_client(tmp_path, registry=None):
    gateway = FakeGateway()
    database = Database(str(tmp_path / "conversations.sqlite3"))
    settings = Settings(database_path=str(tmp_path / "conversations.sqlite3"))
    app = create_app(
        settings=settings, gateway=gateway, database=database, registry=registry
    )
    return TestClient(app), gateway


def test_conversation_history_survives_worker_handoff(tmp_path):
    registry = WorkerRegistry(
        [
            Worker("chat-worker", "http://chat/v1", "chat-model", frozenset({"chat"})),
            Worker("coder-worker", "http://coder/v1", "coder-model", frozenset({"coding"})),
        ]
    )
    client, gateway = make_client(tmp_path, registry)

    created = client.post("/api/v1/conversations")
    assert created.status_code == 201
    conversation_id = created.json()["id"]

    first = client.post(
        f"/api/v1/conversations/{conversation_id}/turns",
        json={"content": "Hello"},
    )
    assert first.status_code == 200
    assert first.json()["producer"] == "chat-worker"

    second = client.post(
        f"/api/v1/conversations/{conversation_id}/turns",
        json={"content": "Fix the Python function", "task_type": "coding"},
    )
    assert second.status_code == 200
    assert second.json()["producer"] == "coder-worker"

    assert gateway.calls[1] == [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "answer-1"},
        {"role": "user", "content": "Fix the Python function"},
    ]

    history = client.get(f"/api/v1/conversations/{conversation_id}")
    assert history.status_code == 200
    events = history.json()["events"]
    assert [event["role"] for event in events] == [
        "user", "assistant", "user", "assistant"
    ]
    assert events[1]["producer"] == "chat-worker"
    assert events[3]["producer"] == "coder-worker"


def test_unknown_conversation_returns_404(tmp_path):
    client, _ = make_client(tmp_path)
    response = client.post(
        "/api/v1/conversations/not-a-real-id/turns",
        json={"content": "hello"},
    )
    assert response.status_code == 404


def test_conversation_events_survive_database_reopen(tmp_path):
    database_path = str(tmp_path / "persistent.sqlite3")
    first_database = Database(database_path)
    conversation = first_database.create_conversation()
    first_database.append_event(
        conversation["id"], role="user", content="Keep this context", producer="user"
    )

    reopened_database = Database(database_path)
    restored = reopened_database.get_conversation(conversation["id"])

    assert restored is not None
    assert restored["id"] == conversation["id"]
    assert len(restored["events"]) == 1
    assert restored["events"][0]["content"] == "Keep this context"


def test_disabled_worker_is_not_selected_and_returns_503(tmp_path):
    registry = WorkerRegistry(
        [
            Worker(
                "disabled-coder", "http://coder/v1", "coder",
                frozenset({"coding"}), enabled=False
            )
        ]
    )
    client, _ = make_client(tmp_path, registry)
    conversation_id = client.post("/api/v1/conversations").json()["id"]

    response = client.post(
        f"/api/v1/conversations/{conversation_id}/turns",
        json={"content": "Change code", "task_type": "coding"},
    )
    assert response.status_code == 503


class FakeOrchestrator:
    def __init__(self):
        self.calls = []

    async def run(self, conversation_id, user_request, conversation_context=""):
        from types import SimpleNamespace

        self.calls.append((conversation_id, user_request, conversation_context))
        return SimpleNamespace(
            execution_id=f"execution-{len(self.calls)}",
            answer=f"orchestrated-{len(self.calls)}",
        )


def test_openai_compatible_endpoint_uses_orchestrator_and_keeps_one_identity(tmp_path):
    gateway = FakeGateway()
    database = Database(str(tmp_path / "orchestrated.sqlite3"))
    orchestrator = FakeOrchestrator()
    app = create_app(
        settings=Settings(database_path=str(tmp_path / "orchestrated.sqlite3")),
        gateway=gateway,
        database=database,
        orchestrator=orchestrator,
    )
    client = TestClient(app)

    first = client.post(
        "/v1/chat/completions",
        json={
            "model": "clicoding",
            "messages": [{"role": "user", "content": "Analyze this task"}],
        },
    )
    assert first.status_code == 200
    first_body = first.json()
    conversation_id = first_body["conversation_id"]
    assert first_body["model"] == "clicoding"
    assert first_body["choices"][0]["message"]["content"] == "orchestrated-1"
    assert first_body["x_clicoding"]["orchestrated"] is True
    assert first_body["x_clicoding"]["execution_id"] == "execution-1"

    second = client.post(
        "/v1/chat/completions",
        json={
            "model": "clicoding",
            "conversation_id": conversation_id,
            "messages": [
                {"role": "user", "content": "Analyze this task"},
                {"role": "assistant", "content": "orchestrated-1"},
                {"role": "user", "content": "Now continue"},
            ],
        },
    )
    assert second.status_code == 200
    assert second.json()["conversation_id"] == conversation_id
    assert second.json()["choices"][0]["message"]["content"] == "orchestrated-2"
    assert orchestrator.calls[0][0] == conversation_id
    assert orchestrator.calls[1][0] == conversation_id
    assert orchestrator.calls[1][1] == "Now continue"
    assert "user: Analyze this task" in orchestrator.calls[1][2]
    assert "assistant: orchestrated-1" in orchestrator.calls[1][2]
    assert gateway.calls == []

    stored = client.get(f"/api/v1/conversations/{conversation_id}").json()
    assert [event["producer"] for event in stored["events"]] == [
        "user", "orchestrator", "user", "orchestrator"
    ]


def test_in_memory_database_persists_across_repository_sessions():
    database = Database(":memory:")
    conversation = database.create_conversation()
    database.append_event(
        conversation["id"], role="user", content="memory survives", producer="test"
    )
    restored = database.get_conversation(conversation["id"])
    assert restored is not None
    assert restored["events"][0]["content"] == "memory survives"
