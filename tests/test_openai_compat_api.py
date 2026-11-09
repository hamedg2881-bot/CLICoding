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


def test_openai_chat_rejects_streaming_and_unknown_model(tmp_path):
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

    assert streaming.status_code == 400
    assert unknown.status_code == 404


def test_configured_bearer_key_protects_api_but_not_health(tmp_path):
    gateway = FakeGateway()
    database = Database(str(tmp_path / "auth.sqlite3"))
    app = create_app(
        settings=Settings(
            database_path=str(tmp_path / "auth.sqlite3"),
            api_key="test-only-secret",
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
        "/v1/models", headers={"Authorization": "Bearer test-only-secret"}
    )
    assert authorized.status_code == 200
    assert "test-only-secret" not in authorized.text
