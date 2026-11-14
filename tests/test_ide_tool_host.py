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

    listed = result(host.execute_tool_call(call("workspace_list_files", {}, "list_1")))
    assert "README.md" in listed["files"]
    assert ".env" not in listed["files"]
    assert ".env.example" in listed["files"]

    read = result(host.execute_tool_call(call("workspace_read_file", {"path": "README.md"}, "read_1")))
    assert read["content"].startswith("CDS_AI project")
    assert read["sha256"] == hashlib.sha256(
        (root / "README.md").read_bytes()
    ).hexdigest()

    searched = result(host.execute_tool_call(call(
        "workspace_search_text", {"query": "second"}, "search_1"
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


def test_workspace_tool_catalog_has_no_general_shell_tool(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    host = WorkspaceToolHost(
        root,
        "catalog-test",
        ToolExecutionStore(tmp_path / "catalog-ledger.sqlite3"),
    )
    names = {item["function"]["name"] for item in host.definitions}
    assert "workspace_read_file" in names
    assert "workspace_write_file" in names
    assert not any("shell" in name or name == "run_command" for name in names)


def test_dotnet_build_uses_fixed_command_restricted_environment_and_approval(tmp_path, monkeypatch):
    import subprocess

    root, host = make_host(tmp_path, approval=lambda request: True)
    (root / "Demo.csproj").write_text("<Project />", encoding="utf-8")
    captured = {}
    monkeypatch.setenv("CLICODING_API_KEY", "must-not-enter-child-process")
    monkeypatch.setattr("cds_ai_ide_adapter.tool_host.shutil.which", lambda name: "/usr/bin/dotnet")

    class FakeProcess:
        pid = 12345
        returncode = 0

        def __init__(self, command, **kwargs):
            captured["command"] = command
            captured.update(kwargs)

        def communicate(self, timeout=None):
            return "Build succeeded", ""

    monkeypatch.setattr("cds_ai_ide_adapter.tool_host.subprocess.Popen", FakeProcess)
    output = result(host.execute_tool_call(call(
        "workspace_dotnet_build", {"project": "Demo.csproj"}
    )))

    assert output["status"] == "ok"
    assert captured["command"] == [
        "/usr/bin/dotnet", "build", str(root / "Demo.csproj"), "--no-restore"
    ]
    assert captured["shell"] is False
    assert captured["cwd"] == root
    assert "CLICODING_API_KEY" not in captured["env"]


def test_dotnet_build_is_denied_without_explicit_approval(tmp_path, monkeypatch):
    root, host = make_host(tmp_path)
    (root / "Demo.csproj").write_text("<Project />", encoding="utf-8")
    monkeypatch.setattr(
        "cds_ai_ide_adapter.tool_host.subprocess.Popen",
        lambda *args, **kwargs: pytest.fail("denied command must not execute"),
    )
    output = result(host.execute_tool_call(call(
        "workspace_dotnet_build", {"project": "Demo.csproj"}
    )))
    assert output["status"] == "approval_denied"


def test_idempotency_key_cannot_be_reused_with_different_arguments(tmp_path):
    root, host = make_host(tmp_path)
    (root / "a.txt").write_text("alpha", encoding="utf-8")
    (root / "b.txt").write_text("beta", encoding="utf-8")
    first = result(host.execute_tool_call(call(
        "workspace_read_file", {"path": "a.txt"}, "same_call"
    )))
    second = result(host.execute_tool_call(call(
        "workspace_read_file", {"path": "b.txt"}, "same_call"
    )))
    assert first["content"] == "alpha"
    assert second["status"] == "conflict"


def test_idempotency_scope_isolated_between_conversations(tmp_path):
    root, host = make_host(tmp_path)
    (root / "first.txt").write_text("first", encoding="utf-8")
    invocation = call("workspace_list_files", {}, "reused_call_id")

    first = result(host.execute_tool_call(invocation, conversation_id="conversation-one"))
    (root / "second.txt").write_text("second", encoding="utf-8")
    second = result(host.execute_tool_call(invocation, conversation_id="conversation-two"))

    assert "first.txt" in first["files"]
    assert "second.txt" not in first["files"]
    assert "first.txt" in second["files"]
    assert "second.txt" in second["files"]


def test_workspace_tool_results_redact_credential_values(tmp_path):
    root, host = make_host(tmp_path)
    secret_api_key = "sk-abcdefghijklmnopqrstuvwx"
    secret_bearer = "Bearer abcdefghijklmnopqrstuvwxyz012345"
    (root / "config.txt").write_text(
        f"api_key={secret_api_key}\nauthorization: {secret_bearer}\n",
        encoding="utf-8",
    )

    read = result(host.execute_tool_call(call(
        "workspace_read_file", {"path": "config.txt"}, "redact_read"
    )))
    searched = result(host.execute_tool_call(call(
        "workspace_search_text", {"query": "api_key", "directory": "."}, "redact_search"
    )))

    serialized = json.dumps({"read": read, "searched": searched})
    assert secret_api_key not in serialized
    assert secret_bearer not in serialized
    assert "[REDACTED]" in serialized


def test_fixed_command_is_terminated_when_cancelled(tmp_path, monkeypatch):
    import subprocess
    import threading

    root, host = make_host(tmp_path)
    started = threading.Event()
    cancelled = threading.Event()
    captured = {}

    class FakeProcess:
        pid = 54321
        returncode = -15

        def __init__(self, command, **kwargs):
            captured.update(kwargs)
            self.calls = 0
            self.terminated = False

        def communicate(self, timeout=None):
            self.calls += 1
            if self.calls == 1:
                started.set()
                assert cancelled.wait(2)
                raise subprocess.TimeoutExpired("fake-command", timeout)
            assert self.terminated
            return "partial stdout", "partial stderr"

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.terminated = True

    monkeypatch.setattr("cds_ai_ide_adapter.tool_host.shutil.which", lambda name: "/usr/bin/fake")
    def signal_fake_process(_pid, _signal):
        process = processes[0]
        process.terminated = True

    processes = []
    original_init = FakeProcess.__init__

    def tracked_init(self, command, **kwargs):
        original_init(self, command, **kwargs)
        processes.append(self)

    FakeProcess.__init__ = tracked_init
    monkeypatch.setattr("cds_ai_ide_adapter.tool_host.os.killpg", signal_fake_process)
    monkeypatch.setattr("cds_ai_ide_adapter.tool_host.subprocess.Popen", FakeProcess)

    def cancel_after_start():
        assert started.wait(2)
        cancelled.set()

    cancel_thread = threading.Thread(target=cancel_after_start)
    cancel_thread.start()
    output = host._run_fixed_command(
        ["fake", "build"], "workspace_dotnet_build", "Build",
        require_approval=False, cancellation_event=cancelled,
    )
    cancel_thread.join(timeout=2)

    assert output["status"] == "cancelled"
    assert output["stdout"] == "partial stdout"
    assert output["stderr"] == "partial stderr"
    assert captured["shell"] is False


def test_workspace_posix_write_rejects_symlink_destination_and_parent(tmp_path):
    import os

    if os.name == "nt" or os.open not in getattr(os, "supports_dir_fd", set()):
        pytest.skip("directory-descriptor operations are POSIX-specific")
    root, host = make_host(tmp_path, approval=lambda request: True)
    outside = tmp_path / "outside.txt"
    outside.write_text("protected", encoding="utf-8")
    (root / "linked.txt").symlink_to(outside)

    symlink_write = result(host.execute_tool_call(call(
        "workspace_write_file",
        {"path": "linked.txt", "content": "overwrite"},
        "write_symlink",
    )))
    assert symlink_write["status"] == "error"
    assert outside.read_text(encoding="utf-8") == "protected"

    outside_dir = tmp_path / "outside-dir"
    outside_dir.mkdir()
    (root / "linked-dir").symlink_to(outside_dir, target_is_directory=True)
    parent_write = result(host.execute_tool_call(call(
        "workspace_write_file",
        {"path": "linked-dir/new.txt", "content": "escape"},
        "write_parent_symlink",
    )))
    assert parent_write["status"] == "error"
    assert not (outside_dir / "new.txt").exists()


def test_workspace_posix_read_rejects_symlink_destination(tmp_path):
    import os

    if os.name == "nt" or os.open not in getattr(os, "supports_dir_fd", set()):
        pytest.skip("directory-descriptor operations are POSIX-specific")
    root, host = make_host(tmp_path)
    outside = tmp_path / "private.txt"
    outside.write_text("outside secret", encoding="utf-8")
    (root / "private-link.txt").symlink_to(outside)

    output = result(host.execute_tool_call(call(
        "workspace_read_file", {"path": "private-link.txt"}, "read_symlink"
    )))
    assert output["status"] == "error"

def test_required_command_sandbox_uses_bubblewrap_and_disables_network(tmp_path, monkeypatch):
    root, host = make_host(tmp_path)
    host.require_command_sandbox = True
    monkeypatch.setattr("cds_ai_ide_adapter.tool_host.shutil.which", lambda name: "/usr/bin/bwrap" if name == "bwrap" else "/usr/bin/dotnet")
    command = host._sandbox_command(["/usr/bin/dotnet", "build", "Demo.csproj"])
    assert command[:2] == ["/usr/bin/bwrap", "--die-with-parent"]
    assert "--unshare-all" in command
    assert "--tmpfs" in command
    assert "--bind" in command
    assert str(root) in command
    assert command[-5:] == ["--chdir", str(root), "--", "/usr/bin/dotnet", "build", "Demo.csproj"][-5:]


def test_required_command_sandbox_fails_closed_without_bubblewrap(tmp_path, monkeypatch):
    root, host = make_host(tmp_path)
    host.require_command_sandbox = True
    monkeypatch.setattr("cds_ai_ide_adapter.tool_host.shutil.which", lambda name: "/usr/bin/dotnet" if name == "dotnet" else None)
    output = host._run_fixed_command(["dotnet", "build"], "workspace_dotnet_build", "Build", require_approval=False)
    assert output["status"] == "unavailable"
    assert "sandbox is unavailable" in output["error"]