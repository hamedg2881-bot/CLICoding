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

    fetched = client.get(f"/api/v1/workspace-sandbox-approvals/{approval_id}")
    assert fetched.status_code == 200
    assert len(fetched.json()["files"]) == 2

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
