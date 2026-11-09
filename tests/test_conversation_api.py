from fastapi.testclient import TestClient

from clicoding_orchestrator.app import create_app
from clicoding_orchestrator.config import Settings
from clicoding_orchestrator.database import Database
from clicoding_orchestrator.gateway import Completion


class FakeGateway:
    def __init__(self):
        self.calls = []

    async def complete(self, messages):
        self.calls.append(messages)
        return Completion(
            content=f"answer-{len(self.calls)}",
            model=f"test-worker-{len(self.calls)}",
        )


def make_client(tmp_path):
    gateway = FakeGateway()
    database = Database(str(tmp_path / "conversations.sqlite3"))
    settings = Settings(database_path=str(tmp_path / "conversations.sqlite3"))
    app = create_app(settings=settings, gateway=gateway, database=database)
    return TestClient(app), gateway


def test_conversation_history_survives_multiple_turns_and_worker_change(tmp_path):
    client, gateway = make_client(tmp_path)

    created = client.post("/api/v1/conversations")
    assert created.status_code == 201
    conversation_id = created.json()["id"]

    first = client.post(
        f"/api/v1/conversations/{conversation_id}/turns",
        json={"content": "Inspect this project"},
    )
    assert first.status_code == 200
    assert first.json()["assistant_content"] == "answer-1"

    second = client.post(
        f"/api/v1/conversations/{conversation_id}/turns",
        json={"content": "Now continue and fix the issue"},
    )
    assert second.status_code == 200
    assert second.json()["assistant_content"] == "answer-2"

    # The second worker call receives the first user message and assistant result.
    assert gateway.calls[1] == [
        {"role": "user", "content": "Inspect this project"},
        {"role": "assistant", "content": "answer-1"},
        {"role": "user", "content": "Now continue and fix the issue"},
    ]

    history = client.get(f"/api/v1/conversations/{conversation_id}")
    assert history.status_code == 200
    assert [event["role"] for event in history.json()["events"]] == [
        "user", "assistant", "user", "assistant"
    ]
    assert history.json()["events"][1]["producer"] == "test-worker-1"
    assert history.json()["events"][3]["producer"] == "test-worker-2"


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

