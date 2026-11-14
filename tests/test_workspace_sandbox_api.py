import asyncio
import json

from fastapi.testclient import TestClient

from clicoding_orchestrator.app import create_app
from clicoding_orchestrator.config import Settings
from clicoding_orchestrator.database import Database


def workspace_payload():
    return {
        "workspace_id": "api-sample",
        "action": "build",
        "project_path": "Demo/Demo.csproj",
        "files": [
            {
                "path": "Demo/Demo.csproj",
                "content": "<Project Sdk=\"Microsoft.NET.Sdk\"><PropertyGroup><TargetFramework>net10.0</TargetFramework></PropertyGroup></Project>",
            },
            {"path": "Demo/Program.cs", "content": "Console.WriteLine(\"hello\");\n"},
        ],
    }


def test_workspace_sandbox_api_creates_reads_and_decides_owner_scoped_approval(tmp_path):
    database_path = str(tmp_path / "workspace-api.sqlite3")
    database = Database(database_path)
    client = TestClient(
        create_app(
            settings=Settings(database_path=database_path),
            database=database,
        )
    )

    created = client.post("/api/v1/workspace-sandbox-approvals", json=workspace_payload())
    assert created.status_code == 201, created.text
    approval_id = created.json()["id"]
    assert created.json()["status"] == "pending"
    assert created.json()["action"] == "build"
    assert len(created.json()["snapshot_sha256"]) == 64
    assert "content" not in created.json()["files"][0]

    fetched = client.get(f"/api/v1/workspace-sandbox-approvals/{approval_id}")
    assert fetched.status_code == 200
    assert len(fetched.json()["files"]) == 2
    assert all("content" not in item for item in fetched.json()["files"])

    decided = client.post(
        f"/api/v1/workspace-sandbox-approvals/{approval_id}/decision",
        json={"decision": "approve"},
    )
    assert decided.status_code == 200
    assert decided.json()["status"] == "approved"

    duplicate = client.post(
        f"/api/v1/workspace-sandbox-approvals/{approval_id}/decision",
        json={"decision": "reject"},
    )
    assert duplicate.status_code == 409


def test_workspace_sandbox_api_rejects_traversal_and_unknown_fields(tmp_path):
    database_path = str(tmp_path / "workspace-api-invalid.sqlite3")
    client = TestClient(
        create_app(
            settings=Settings(database_path=database_path),
            database=Database(database_path),
        )
    )
    payload = workspace_payload()
    payload["files"][0]["path"] = "../escape.csproj"
    rejected = client.post("/api/v1/workspace-sandbox-approvals", json=payload)
    assert rejected.status_code == 422

    payload = workspace_payload()
    payload["unexpected"] = "not allowed"
    rejected = client.post("/api/v1/workspace-sandbox-approvals", json=payload)
    assert rejected.status_code == 422


def test_workspace_sandbox_api_enforces_request_body_limit(tmp_path):
    database_path = str(tmp_path / "workspace-api-size.sqlite3")
    client = TestClient(
        create_app(
            settings=Settings(database_path=database_path),
            database=Database(database_path),
        )
    )
    response = client.post(
        "/api/v1/workspace-sandbox-approvals",
        content=b"x" * (8 * 1024 * 1024 + 1),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413


def test_workspace_sandbox_api_forwards_execute_request_to_existing_broker(tmp_path, monkeypatch):
    database_path = str(tmp_path / "workspace-api-execute.sqlite3")
    database = Database(database_path)
    client = TestClient(
        create_app(settings=Settings(database_path=database_path), database=database)
    )
    created = client.post("/api/v1/workspace-sandbox-approvals", json=workspace_payload())
    approval_id = created.json()["id"]
    client.post(
        f"/api/v1/workspace-sandbox-approvals/{approval_id}/decision",
        json={"decision": "approve"},
    )
    sent = []

    class FakeReader:
        async def readline(self):
            return json.dumps({
                "status": "succeeded",
                "exit_code": 0,
                "snapshot_sha256": created.json()["snapshot_sha256"],
            }).encode() + b"\n"

    class FakeWriter:
        def write(self, value):
            sent.append(json.loads(value))

        async def drain(self):
            return None

        def close(self):
            return None

        async def wait_closed(self):
            return None

    async def fake_open_unix_connection(*args, **kwargs):
        return FakeReader(), FakeWriter()

    monkeypatch.setattr(asyncio, "open_unix_connection", fake_open_unix_connection)
    executed = client.post(
        f"/api/v1/workspace-sandbox-approvals/{approval_id}/execute"
    )
    assert executed.status_code == 200
    assert executed.json()["status"] == "succeeded"
    assert sent == [{"approval_id": approval_id}]


def test_workspace_sandbox_api_forwards_cancel_request_to_existing_broker(tmp_path, monkeypatch):
    database_path = str(tmp_path / "workspace-api-cancel.sqlite3")
    database = Database(database_path)
    client = TestClient(
        create_app(settings=Settings(database_path=database_path), database=database)
    )
    created = client.post("/api/v1/workspace-sandbox-approvals", json=workspace_payload())
    approval_id = created.json()["id"]
    client.post(
        f"/api/v1/workspace-sandbox-approvals/{approval_id}/decision",
        json={"decision": "approve"},
    )
    sent = []

    class FakeReader:
        async def readline(self):
            return json.dumps({
                "status": "cancel_requested",
                "approval_id": approval_id,
                "container_stopped": True,
            }).encode() + b"\n"

    class FakeWriter:
        def write(self, value):
            sent.append(json.loads(value))

        async def drain(self):
            return None

        def close(self):
            return None

        async def wait_closed(self):
            return None

    async def fake_open_unix_connection(*args, **kwargs):
        return FakeReader(), FakeWriter()

    monkeypatch.setattr(asyncio, "open_unix_connection", fake_open_unix_connection)
    cancelled = client.post(
        f"/api/v1/workspace-sandbox-approvals/{approval_id}/cancel"
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancel_requested"
    assert sent == [{"cancel_approval_id": approval_id}]
