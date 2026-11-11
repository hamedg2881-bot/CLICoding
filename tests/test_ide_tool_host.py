import hashlib
import json

import pytest

from cds_ai_ide_adapter.tool_host import ToolApproval, ToolExecutionStore, WorkspaceToolHost


def make_host(tmp_path, approval=None):
    root = tmp_path / "workspace"
    root.mkdir(exist_ok=True)
    store = ToolExecutionStore(tmp_path / "tool-ledger.sqlite3")
    return root, WorkspaceToolHost(root, "workspace-test", store, approval=approval)


def call(name, arguments, call_id="call_1"):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def result(message):
    return json.loads(message["content"])


def test_workspace_read_list_and_search_are_scoped_and_hide_secret_files(tmp_path):
    root, host = make_host(tmp_path)
    (root / "README.md").write_text("CDS_AI project\nsecond line\n", encoding="utf-8")
    (root / ".env").write_text("TOKEN=do-not-read", encoding="utf-8")
    (root / ".env.example").write_text("TOKEN=placeholder", encoding="utf-8")

    listed = result(host.execute_tool_call(call("workspace_list_files", {})))
    assert "README.md" in listed["files"]
    assert ".env" not in listed["files"]
    assert ".env.example" in listed["files"]

    read = result(host.execute_tool_call(call("workspace_read_file", {"path": "README.md"})))
    assert read["content"].startswith("CDS_AI project")
    assert read["sha256"] == hashlib.sha256(
        (root / "README.md").read_bytes()
    ).hexdigest()

    searched = result(host.execute_tool_call(call(
        "workspace_search_text", {"query": "second"}
    )))
    assert searched["matches"][0]["path"] == "README.md"
    assert searched["matches"][0]["line"] == 2


def test_workspace_tools_reject_traversal_symlinks_and_secret_reads(tmp_path):
    root, host = make_host(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (root / "escape.txt").symlink_to(outside)

    traversal = result(host.execute_tool_call(call(
        "workspace_read_file", {"path": "../outside.txt"}, "call_traversal"
    )))
    symlink = result(host.execute_tool_call(call(
        "workspace_read_file", {"path": "escape.txt"}, "call_symlink"
    )))
    secret = result(host.execute_tool_call(call(
        "workspace_read_file", {"path": ".env"}, "call_secret"
    )))

    assert traversal["status"] == "error"
    assert symlink["status"] == "error"
    assert secret["status"] == "error"


def test_workspace_write_requires_approval_and_is_idempotent(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    target = root / "notes.txt"
    target.write_text("before", encoding="utf-8")
    before_sha = hashlib.sha256(target.read_bytes()).hexdigest()
    approvals: list[ToolApproval] = []
    host = WorkspaceToolHost(
        root,
        "workspace-write",
        ToolExecutionStore(tmp_path / "write-ledger.sqlite3"),
        approval=lambda request: approvals.append(request) or True,
    )
    invocation = call(
        "workspace_write_file",
        {
            "path": "notes.txt",
            "content": "after",
            "expected_sha256": before_sha,
        },
        "write_1",
    )

    first = result(host.execute_tool_call(invocation))
    duplicate = result(host.execute_tool_call(invocation))

    assert first["status"] == "ok"
    assert duplicate == first
    assert target.read_text(encoding="utf-8") == "after"
    assert len(approvals) == 1
    assert approvals[0].workspace_id == "workspace-write"
    assert approvals[0].details["before_sha256"] == before_sha


def test_workspace_write_defaults_to_denied_and_checks_stale_hash(tmp_path):
    root, host = make_host(tmp_path)
    target = root / "notes.txt"
    target.write_text("current", encoding="utf-8")
    current_sha = hashlib.sha256(target.read_bytes()).hexdigest()

    denied = result(host.execute_tool_call(call(
        "workspace_write_file",
        {"path": "notes.txt", "content": "changed", "expected_sha256": current_sha},
    )))
    assert denied["status"] == "approval_denied"
    assert target.read_text(encoding="utf-8") == "current"

    approved_host = WorkspaceToolHost(
        root,
        "workspace-approved",
        ToolExecutionStore(tmp_path / "approved-ledger.sqlite3"),
        approval=lambda request: True,
    )
    stale = result(approved_host.execute_tool_call(call(
        "workspace_write_file",
        {"path": "notes.txt", "content": "changed", "expected_sha256": "0" * 64},
    )))
    assert stale["status"] == "conflict"
    assert target.read_text(encoding="utf-8") == "current"


def test_workspace_tool_catalog_has_no_general_shell_tool():
    root = __import__("pathlib").Path.cwd()
    host = WorkspaceToolHost(
        root,
        "catalog-test",
        ToolExecutionStore(":memory:") if False else ToolExecutionStore("/tmp/cds-ai-test-ledger.sqlite3"),
    )
    names = {item["function"]["name"] for item in host.definitions}
    assert "workspace_read_file" in names
    assert "workspace_write_file" in names
    assert not any("shell" in name or name == "run_command" for name in names)
