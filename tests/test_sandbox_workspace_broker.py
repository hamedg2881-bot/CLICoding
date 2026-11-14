import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from clicoding_orchestrator.database import Database

_broker_path = Path(__file__).parents[1] / "sandbox" / "clicoding_sandbox_broker.py"
_broker_spec = importlib.util.spec_from_file_location("clicoding_sandbox_broker", _broker_path)
broker = importlib.util.module_from_spec(_broker_spec)
_broker_spec.loader.exec_module(broker)


def create_approved_workspace(path):
    database = Database(str(path))
    approval = database.create_workspace_sandbox_approval(
        "developer-1",
        "broker-test",
        "build",
        "Demo/Demo.csproj",
        [
            {
                "path": "Demo/Demo.csproj",
                "content": "<Project Sdk=\"Microsoft.NET.Sdk\" />",
            },
            {"path": "Demo/Program.cs", "content": "Console.WriteLine(1);"},
        ],
    )
    database.decide_workspace_sandbox_approval(
        approval["id"], "developer-1", "approve"
    )
    return database, approval


def test_broker_claims_workspace_snapshot_once_and_verifies_digest(tmp_path, monkeypatch):
    database_path = tmp_path / "broker.sqlite3"
    _, approval = create_approved_workspace(database_path)
    monkeypatch.setattr(broker, "DATABASE_PATH", str(database_path))

    claimed = broker.claim_workspace_approval(approval["id"])
    assert claimed["action"] == "build"
    assert claimed["project_path"] == "Demo/Demo.csproj"
    assert claimed["snapshot_sha256"] == approval["snapshot_sha256"]
    assert len(claimed["files"]) == 2

    with pytest.raises(ValueError, match="already been consumed"):
        broker.claim_workspace_approval(approval["id"])


def test_broker_rejects_tampered_workspace_content_before_claim(tmp_path, monkeypatch):
    database_path = tmp_path / "broker-tamper.sqlite3"
    database, approval = create_approved_workspace(database_path)
    monkeypatch.setattr(broker, "DATABASE_PATH", str(database_path))

    with database.session() as connection:
        row = connection.execute(
            "SELECT files_json FROM sandbox_workspace_approvals WHERE id = ?",
            (approval["id"],),
        ).fetchone()
        files = json.loads(row["files_json"])
        files[0]["content"] += "tampered"
        files[0]["bytes"] = len(files[0]["content"].encode())
        connection.execute(
            "UPDATE sandbox_workspace_approvals SET files_json = ? WHERE id = ?",
            (json.dumps(files), approval["id"]),
        )

    with pytest.raises(ValueError, match="file digest mismatch"):
        broker.claim_workspace_approval(approval["id"])


def test_broker_rechecks_unsafe_paths_even_if_database_row_is_tampered(tmp_path, monkeypatch):
    database_path = tmp_path / "broker-path.sqlite3"
    database, approval = create_approved_workspace(database_path)
    monkeypatch.setattr(broker, "DATABASE_PATH", str(database_path))

    with database.session() as connection:
        row = connection.execute(
            "SELECT files_json FROM sandbox_workspace_approvals WHERE id = ?",
            (approval["id"],),
        ).fetchone()
        files = json.loads(row["files_json"])
        files[0]["path"] = "../Demo.csproj"
        manifest = [
            {
                "path": item["path"],
                "sha256": item["sha256"],
                "bytes": item["bytes"],
            }
            for item in files
        ]
        digest = hashlib.sha256(
            json.dumps(
                sorted(manifest, key=lambda item: item["path"]),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        connection.execute(
            "UPDATE sandbox_workspace_approvals SET files_json = ?, snapshot_sha256 = ? WHERE id = ?",
            (json.dumps(files), digest, approval["id"]),
        )

    with pytest.raises(ValueError, match="path is unsafe"):
        broker.claim_workspace_approval(approval["id"])


def test_broker_cancels_pending_workspace_approval_without_execution(tmp_path, monkeypatch):
    database_path = tmp_path / "broker-cancel-pending.sqlite3"
    database, approval = create_approved_workspace(database_path)
    # Recreate a pending state to model cancellation while the approval dialog is open.
    with database.session() as connection:
        connection.execute(
            "UPDATE sandbox_workspace_approvals SET status = 'pending' WHERE id = ?",
            (approval["id"],),
        )
    monkeypatch.setattr(broker, "DATABASE_PATH", str(database_path))

    result = broker.cancel_workspace_approval(approval["id"])
    assert result["status"] == "cancelled"
    assert database.get_workspace_sandbox_approval(approval["id"])["status"] == "rejected"


def test_broker_cancels_approved_workspace_before_runner_claim(tmp_path, monkeypatch):
    database_path = tmp_path / "broker-cancel-before-run.sqlite3"
    database, approval = create_approved_workspace(database_path)
    monkeypatch.setattr(broker, "DATABASE_PATH", str(database_path))

    result = broker.cancel_workspace_approval(approval["id"])
    assert result["status"] == "cancelled"
    with pytest.raises(ValueError, match="not approved"):
        broker.claim_workspace_approval(approval["id"])


def test_broker_stops_running_workspace_container_by_approval_label(tmp_path, monkeypatch):
    database_path = tmp_path / "broker-cancel-running.sqlite3"
    database, approval = create_approved_workspace(database_path)
    monkeypatch.setattr(broker, "DATABASE_PATH", str(database_path))
    with database.session() as connection:
        connection.execute(
            """
            CREATE TABLE sandbox_execution_claims (
                approval_id TEXT PRIMARY KEY,
                source_sha256 TEXT NOT NULL,
                status TEXT NOT NULL,
                claimed_at TEXT NOT NULL,
                finished_at TEXT
            )
            """
        )
        connection.execute(
            "INSERT INTO sandbox_execution_claims VALUES (?, ?, 'running', ?, NULL)",
            (approval["id"], approval["snapshot_sha256"], "2026-01-01T00:00:00+00:00"),
        )

    class FakeProcess:
        pid = 12345

        def wait(self, timeout=None):
            return -15

        def poll(self):
            return -15

    monkeypatch.setattr(broker, "RUNNING_EXECUTIONS", {approval["id"]: FakeProcess()})
    monkeypatch.setattr(broker.os, "killpg", lambda *args: None)
    monkeypatch.setattr(
        broker.subprocess,
        "run",
        lambda args, **kwargs: broker.subprocess.CompletedProcess(
            args, 0, stdout=b"", stderr=b""
        ),
    )
    monkeypatch.setattr(broker.time, "sleep", lambda *_args: None)

    result = broker.cancel_workspace_approval(approval["id"])
    assert result["status"] == "cancel_requested"
    assert result["container_stopped"] is True
    with database.session() as connection:
        claim = connection.execute(
            "SELECT status FROM sandbox_execution_claims WHERE approval_id = ?",
            (approval["id"],),
        ).fetchone()
    assert claim["status"] == "cancel_requested"


def test_broker_persists_cancelled_status_when_cancel_races_runner_result(tmp_path, monkeypatch):
    database_path = tmp_path / "broker-cancel-result.sqlite3"
    database, approval = create_approved_workspace(database_path)
    monkeypatch.setattr(broker, "DATABASE_PATH", str(database_path))
    with database.session() as connection:
        connection.execute(
            """
            CREATE TABLE sandbox_execution_claims (
                approval_id TEXT PRIMARY KEY,
                source_sha256 TEXT NOT NULL,
                status TEXT NOT NULL,
                claimed_at TEXT NOT NULL,
                finished_at TEXT
            )
            """
        )
        connection.execute(
            "INSERT INTO sandbox_execution_claims VALUES (?, ?, 'cancel_requested', ?, NULL)",
            (approval["id"], approval["snapshot_sha256"], "2026-01-01T00:00:00+00:00"),
        )

    broker.persist_result(
        approval["id"],
        approval["snapshot_sha256"],
        {"status": "failed", "exit_code": 137},
    )
    with database.session() as connection:
        row = connection.execute(
            "SELECT status, result_json FROM sandbox_execution_results WHERE approval_id = ?",
            (approval["id"],),
        ).fetchone()
    assert row["status"] == "cancelled"
    assert json.loads(row["result_json"])["status"] == "cancelled"
