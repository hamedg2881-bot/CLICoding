import pytest
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

    async def run(
        self, conversation_id, user_request, conversation_context="", user_content=None
    ):
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
            "task_type": "coding",
            "messages": [{"role": "user", "content": "Analyze this task"}],
        },
    )
    assert first.status_code == 200
    first_body = first.json()
    conversation_id = first_body["conversation_id"]
    assert first_body["model"] == "CDS_AI"
    assert first_body["choices"][0]["message"]["content"] == "orchestrated-1"
    assert first_body["x_clicoding"]["orchestrated"] is True
    assert first_body["x_clicoding"]["execution_id"] == "execution-1"

    second = client.post(
        "/v1/chat/completions",
        json={
            "model": "clicoding",
            "task_type": "coding",
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


def test_deterministic_routing_enforces_worker_limit_across_conversations(tmp_path):
    import asyncio
    import httpx

    async def scenario():
        active = 0
        maximum_active = 0

        class SlowGateway:
            async def complete(self, messages):
                nonlocal active, maximum_active
                active += 1
                maximum_active = max(maximum_active, active)
                await asyncio.sleep(0.02)
                active -= 1
                return Completion("ok", "chat")

        database = Database(str(tmp_path / "deterministic-limits.sqlite3"))
        registry = WorkerRegistry([
            Worker(
                "chat", "http://chat/v1", "chat", frozenset({"chat"}),
                max_concurrency=1,
            )
        ])
        app = create_app(
            settings=Settings(database_path=str(tmp_path / "deterministic-limits.sqlite3")),
            gateway=SlowGateway(),
            database=database,
            registry=registry,
        )
        first = database.create_conversation()["id"]
        second = database.create_conversation()["id"]
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            responses = await asyncio.gather(
                client.post(
                    f"/api/v1/conversations/{first}/turns",
                    json={"content": "first"},
                ),
                client.post(
                    f"/api/v1/conversations/{second}/turns",
                    json={"content": "second"},
                ),
            )
        assert [response.status_code for response in responses] == [200, 200]
        assert maximum_active == 1

    asyncio.run(scenario())


def test_trusted_openwebui_user_header_scopes_conversation_ownership(tmp_path):
    shared_key = "s" * 40
    database = Database(str(tmp_path / "owners.sqlite3"))
    app = create_app(
        settings=Settings(
            database_path=str(tmp_path / "owners.sqlite3"),
            api_key=shared_key,
            trust_user_header=True,
        ),
        gateway=FakeGateway(),
        database=database,
        registry=WorkerRegistry([
            Worker("chat", "http://chat/v1", "chat-model", frozenset({"chat"}))
        ]),
    )
    client = TestClient(app)
    alice = {
        "Authorization": f"Bearer {shared_key}",
        "X-CLICoding-User-ID": "alice",
    }
    bob = {
        "Authorization": f"Bearer {shared_key}",
        "X-CLICoding-User-ID": "bob",
    }

    created = client.post("/api/v1/conversations", headers=alice)
    assert created.status_code == 201
    conversation_id = created.json()["id"]
    assert created.json()["owner_id"] == "alice"

    assert client.get(
        f"/api/v1/conversations/{conversation_id}", headers=alice
    ).status_code == 200
    assert client.get(
        f"/api/v1/conversations/{conversation_id}", headers=bob
    ).status_code == 404
    assert client.post(
        f"/api/v1/conversations/{conversation_id}/turns",
        headers=bob,
        json={"content": "cross-user access"},
    ).status_code == 404


def test_per_user_api_tokens_isolate_conversations_and_global_key_is_admin(tmp_path):
    admin_key = "a" * 40
    alice_key = "b" * 40
    bob_key = "c" * 40
    database = Database(str(tmp_path / "user-tokens.sqlite3"))
    app = create_app(
        settings=Settings(
            database_path=str(tmp_path / "user-tokens.sqlite3"),
            api_key=admin_key,
            user_api_keys={"alice": alice_key, "bob": bob_key},
        ),
        gateway=FakeGateway(),
        database=database,
        registry=WorkerRegistry([
            Worker("chat", "http://chat/v1", "chat-model", frozenset({"chat"}))
        ]),
    )
    client = TestClient(app)
    alice_headers = {"Authorization": f"Bearer {alice_key}"}
    bob_headers = {"Authorization": f"Bearer {bob_key}"}
    admin_headers = {"Authorization": f"Bearer {admin_key}"}

    created = client.post("/api/v1/conversations", headers=alice_headers)
    conversation_id = created.json()["id"]
    assert created.json()["owner_id"] == "alice"
    assert client.get(
        f"/api/v1/conversations/{conversation_id}", headers=bob_headers
    ).status_code == 404
    assert client.get(
        f"/api/v1/conversations/{conversation_id}", headers=admin_headers
    ).status_code == 200
    assert client.get("/api/v1/workers", headers=alice_headers).status_code == 403
    assert client.get("/api/v1/workers", headers=admin_headers).status_code == 200


def test_conversation_owner_migration_keeps_legacy_records_in_shared_namespace(tmp_path):
    import sqlite3

    path = tmp_path / "legacy-owner.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE conversations (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO conversations (id, created_at, updated_at) VALUES (?, ?, ?)",
        ("legacy-conversation", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
    )
    connection.commit()
    connection.close()

    database = Database(str(path))
    restored = database.get_conversation("legacy-conversation")
    assert restored is not None
    assert restored["owner_id"] == "shared"
    assert database.conversation_exists("legacy-conversation", owner_id="shared")
    assert not database.conversation_exists("legacy-conversation", owner_id="alice")
    with pytest.raises(PermissionError):
        database.get_or_create_conversation("legacy-conversation", owner_id="alice")



def test_conversation_summary_is_versioned_and_never_rewrites_original_events(tmp_path):
    database = Database(str(tmp_path / "summaries.sqlite3"))
    conversation_id = database.create_conversation()["id"]
    for role, content in [
        ("user", "first question"),
        ("assistant", "first answer"),
        ("user", "second question"),
        ("assistant", "second answer"),
    ]:
        database.append_event(conversation_id, role, content)

    first = database.save_summary(
        conversation_id, 2, "Summary of first turn", "summary-model"
    )
    assert database.get_latest_summary(conversation_id) == first
    second = database.save_summary(
        conversation_id, 4, "Updated summary of both turns", "summary-model"
    )
    assert database.get_latest_summary(conversation_id) == second
    assert [event["content"] for event in database.list_events(conversation_id)] == [
        "first question", "first answer", "second question", "second answer"
    ]


def test_conversation_summary_rejects_non_monotonic_or_unknown_boundaries(tmp_path):
    import pytest

    database = Database(str(tmp_path / "summary-boundary.sqlite3"))
    conversation_id = database.create_conversation()["id"]
    database.append_event(conversation_id, "user", "question")
    database.append_event(conversation_id, "assistant", "answer")
    database.save_summary(conversation_id, 2, "Summary", "model")

    with pytest.raises(ValueError, match="advance"):
        database.save_summary(conversation_id, 1, "Older summary", "model")
    with pytest.raises(ValueError, match="existing conversation event"):
        database.save_summary(conversation_id, 99, "Unknown boundary", "model")



def test_rewriting_conversation_history_invalidates_only_affected_summaries(tmp_path):
    database = Database(str(tmp_path / "summary-invalidation.sqlite3"))
    conversation_id = database.create_conversation()["id"]
    for index in range(6):
        database.append_event(
            conversation_id,
            "user" if index % 2 == 0 else "assistant",
            f"event-{index}",
        )
    database.save_summary(conversation_id, 2, "summary through 2", "model")
    database.save_summary(conversation_id, 4, "summary through 4", "model")

    deleted = database.invalidate_summaries_from_sequence(conversation_id, 4)

    assert deleted == 1
    assert database.get_latest_summary(conversation_id)["through_sequence"] == 2
