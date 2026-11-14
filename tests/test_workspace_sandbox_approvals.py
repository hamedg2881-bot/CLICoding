import hashlib
import json

import pytest

from clicoding_orchestrator.database import Database


def sample_files():
    return [
        {
            "path": "Demo/Program.cs",
            "content": "Console.WriteLine(\"hello\");\n",
        },
        {
            "path": "Demo/Demo.csproj",
            "content": "<Project Sdk=\"Microsoft.NET.Sdk\"><PropertyGroup><TargetFramework>net10.0</TargetFramework></PropertyGroup></Project>",
        },
    ]


def test_workspace_approval_binds_owner_action_project_and_complete_snapshot(tmp_path):
    database = Database(str(tmp_path / "workspace-approval.sqlite3"))
    files = sample_files()

    approval = database.create_workspace_sandbox_approval(
        owner_id="developer-1",
        workspace_id="sample-project",
        action="build",
        project_path="Demo/Demo.csproj",
        files=files,
    )

    assert approval["status"] == "pending"
    assert approval["owner_id"] == "developer-1"
    assert approval["workspace_id"] == "sample-project"
    assert approval["action"] == "build"
    assert approval["project_path"] == "Demo/Demo.csproj"
    assert approval["total_bytes"] == sum(len(item["content"].encode()) for item in files)
    assert len(approval["snapshot_sha256"]) == 64
    assert [item["path"] for item in approval["files"]] == [
        "Demo/Demo.csproj", "Demo/Program.cs"
    ]
    for item in approval["files"]:
        assert item["sha256"] == hashlib.sha256(item["content"].encode()).hexdigest()

    manifest = [
        {"path": item["path"], "sha256": item["sha256"], "bytes": item["bytes"]}
        for item in approval["files"]
    ]
    expected = hashlib.sha256(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert approval["snapshot_sha256"] == expected


def test_workspace_approval_is_owner_scoped_and_single_decision(tmp_path):
    database = Database(str(tmp_path / "workspace-owner.sqlite3"))
    approval = database.create_workspace_sandbox_approval(
        "alice", "repo-a", "test", "Demo/Demo.csproj", sample_files()
    )

    assert database.get_workspace_sandbox_approval(approval["id"], "bob") is None
    assert database.decide_workspace_sandbox_approval(
        approval["id"], "bob", "approve"
    ) is None

    decided = database.decide_workspace_sandbox_approval(
        approval["id"], "alice", "approve"
    )
    assert decided["status"] == "approved"
    with pytest.raises(ValueError, match="not pending"):
        database.decide_workspace_sandbox_approval(
            approval["id"], "alice", "reject"
        )


def test_workspace_approval_expiry_is_enforced(tmp_path):
    database = Database(str(tmp_path / "workspace-expiry.sqlite3"))
    approval = database.create_workspace_sandbox_approval(
        "alice", "repo-a", "build", "Demo/Demo.csproj", sample_files()
    )
    with database.session() as connection:
        connection.execute(
            "UPDATE sandbox_workspace_approvals SET expires_at = ? WHERE id = ?",
            ("2000-01-01T00:00:00+00:00", approval["id"]),
        )

    with pytest.raises(ValueError, match="expired"):
        database.decide_workspace_sandbox_approval(approval["id"], "alice", "approve")
    assert database.get_workspace_sandbox_approval(approval["id"], "alice")["status"] == "expired"


@pytest.mark.parametrize(
    ("files", "project_path", "message"),
    [
        ([{"path": "../escape.csproj", "content": "x"}], "../escape.csproj", "path"),
        ([{"path": "Demo/.env", "content": "SECRET=x"}], "Demo/.env", "blocked"),
        ([{"path": "Demo/id_rsa", "content": "secret"}], "Demo/id_rsa", "blocked"),
        ([{"path": "Demo/Demo.csproj", "content": "x"}], "Missing.csproj", "not present"),
        (
            [
                {"path": "Demo/Demo.csproj", "content": "one"},
                {"path": "demo/demo.csproj", "content": "two"},
            ],
            "Demo/Demo.csproj",
            "duplicate",
        ),
    ],
)
def test_workspace_snapshot_rejects_unsafe_or_incomplete_content(
    tmp_path, files, project_path, message
):
    database = Database(str(tmp_path / "workspace-invalid.sqlite3"))
    with pytest.raises(ValueError, match=message):
        database.create_workspace_sandbox_approval(
            "alice", "repo-a", "build", project_path, files
        )


def test_workspace_snapshot_enforces_file_count_per_file_and_total_limits(tmp_path):
    database = Database(str(tmp_path / "workspace-limits.sqlite3"))
    too_many = [
        {"path": f"f{i}.txt", "content": "x"} for i in range(200)
    ] + [{"path": "Demo.csproj", "content": "x"}]
    with pytest.raises(ValueError, match="1 to 200"):
        database.create_workspace_sandbox_approval(
            "alice", "repo-a", "build", "Demo.csproj", too_many
        )

    with pytest.raises(ValueError, match="1 MiB"):
        database.create_workspace_sandbox_approval(
            "alice", "repo-a", "build", "Demo.csproj",
            [{"path": "Demo.csproj", "content": "x" * (1_000_001)}],
        )

    many_bytes = [
        {"path": f"src/f{i}.cs", "content": "x" * 900_000}
        for i in range(6)
    ] + [{"path": "Demo.csproj", "content": "x"}]
    with pytest.raises(ValueError, match="5 MiB"):
        database.create_workspace_sandbox_approval(
            "alice", "repo-a", "build", "Demo.csproj", many_bytes
        )
