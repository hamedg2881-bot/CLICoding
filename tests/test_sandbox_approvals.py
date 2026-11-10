import hashlib
import sqlite3

import pytest
from fastapi.testclient import TestClient

from clicoding_orchestrator.app import create_app
from clicoding_orchestrator.config import Settings
from clicoding_orchestrator.database import Database


def _execution_with_task(database: Database, status: str = "succeeded"):
    conversation = database.create_conversation()
    execution = database.create_execution(
        conversation["id"],
        {
            "goal": "Create a small code artifact",
            "acceptance_criteria": ["Code is reviewed before execution"],
            "tasks": [
                {
                    "task_id": "implement",
                    "objective": "Write code",
                    "required_capabilities": ["coding"],
                    "depends_on": [],
                    "expected_output": "Python source",
                }
            ],
        },
    )
    database.update_execution_task(
        execution["id"], "implement", status, output="candidate source"
    )
    return conversation, execution


def test_approval_persists_exact_source_and_digest(tmp_path):
    path = str(tmp_path / "approvals.sqlite3")
    database = Database(path)
    _, execution = _execution_with_task(database)
    source = "print('approved exact source')"

    approval = database.create_sandbox_approval(
        execution["id"], "implement", "python", source
    )
    assert approval["status"] == "pending"
    assert approval["source"] == source
    assert approval["source_sha256"] == hashlib.sha256(source.encode()).hexdigest()
    assert approval["expires_at"] > approval["created_at"]

    reopened = Database(path)
    restored = reopened.get_sandbox_approval(approval["id"])
    assert restored == approval


def test_only_successful_task_can_request_approval(tmp_path):
    database = Database(str(tmp_path / "approvals.sqlite3"))
    _, execution = _execution_with_task(database, status="running")

    with pytest.raises(ValueError, match="only a succeeded task"):
        database.create_sandbox_approval(
            execution["id"], "implement", "python", "print(1)"
        )


def test_approval_is_single_decision_and_source_is_immutable(tmp_path):
    database = Database(str(tmp_path / "approvals.sqlite3"))
    _, execution = _execution_with_task(database)
    approval = database.create_sandbox_approval(
        execution["id"], "implement", "javascript", "console.log(42)"
    )

    decided = database.decide_sandbox_approval(approval["id"], "approve")
    assert decided["status"] == "approved"
    assert decided["source"] == "console.log(42)"

    with pytest.raises(ValueError, match="not pending"):
        database.decide_sandbox_approval(approval["id"], "reject")


def test_expired_approval_cannot_be_approved(tmp_path):
    database = Database(str(tmp_path / "approvals.sqlite3"))
    _, execution = _execution_with_task(database)
    approval = database.create_sandbox_approval(
        execution["id"], "implement", "python", "print(7)"
    )
    with database.session() as connection:
        connection.execute(
            "UPDATE sandbox_approvals SET expires_at = '2000-01-01T00:00:00+00:00' WHERE id = ?",
            (approval["id"],),
        )

    with pytest.raises(ValueError, match="expired"):
        database.decide_sandbox_approval(approval["id"], "approve")
    assert database.get_sandbox_approval(approval["id"])["status"] == "expired"


def test_approval_api_creates_reads_and_decides(tmp_path):
    database = Database(str(tmp_path / "approval-api.sqlite3"))
    _, execution = _execution_with_task(database)
    app = create_app(
        settings=Settings(database_path=str(tmp_path / "approval-api.sqlite3")),
        database=database,
    )
    client = TestClient(app)
    base = f"/api/v1/executions/{execution['id']}/tasks/implement/sandbox-approvals"

    created = client.post(
        base,
        json={"language": "python", "source": "print(2 + 3)"},
    )
    assert created.status_code == 201
    approval_id = created.json()["id"]

    listed = client.get(base)
    assert listed.status_code == 200
    assert len(listed.json()["approvals"]) == 1

    detail = client.get(f"/api/v1/sandbox-approvals/{approval_id}")
    assert detail.status_code == 200
    assert detail.json()["source"] == "print(2 + 3)"
    assert detail.json()["status"] == "pending"

    decided = client.post(
        f"/api/v1/sandbox-approvals/{approval_id}/decision",
        json={"decision": "approve"},
    )
    assert decided.status_code == 200
    assert decided.json()["status"] == "approved"

    duplicate = client.post(
        f"/api/v1/sandbox-approvals/{approval_id}/decision",
        json={"decision": "reject"},
    )
    assert duplicate.status_code == 409
