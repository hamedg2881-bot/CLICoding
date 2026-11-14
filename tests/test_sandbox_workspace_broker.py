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
