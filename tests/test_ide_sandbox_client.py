import json

import httpx

from cds_ai_ide_adapter.client import CDSAISandboxExecutor


def test_remote_sandbox_executor_requires_approval_and_executes_exact_snapshot():
    requests = []
    approval_requests = []
    approval_id = "d0a0f0d0-1111-4222-8333-444455556666"
    digest = "a" * 64

    def handler(request):
        requests.append((request.method, request.url.path))
        if request.url.path == "/api/v1/workspace-sandbox-approvals":
            payload = json.loads(request.content)
            assert payload["workspace_id"] == "demo-workspace"
            assert payload["action"] == "build"
            return httpx.Response(201, json={
                "id": approval_id,
                "status": "pending",
                "snapshot_sha256": digest,
                "total_bytes": 12,
                "files": [
                    {"path": "Demo.csproj", "sha256": "b" * 64, "bytes": 12}
                ],
            })
        if request.url.path.endswith("/decision"):
            assert json.loads(request.content) == {"decision": "approve"}
            return httpx.Response(200, json={"status": "approved"})
        if request.url.path.endswith("/execute"):
            return httpx.Response(200, json={
                "status": "succeeded",
                "exit_code": 0,
                "snapshot_sha256": digest,
            })
        return httpx.Response(404)

    executor = CDSAISandboxExecutor(
        "http://orchestrator.local",
        api_key="test-api-key",
        approval=lambda request: approval_requests.append(request) or True,
        transport=httpx.MockTransport(handler),
    )
    result = executor(
        "demo-workspace",
        "build",
        "Demo.csproj",
        [{"path": "Demo.csproj", "content": "<Project />"}],
        None,
    )

    assert result["status"] == "succeeded"
    assert result["approval_id"] == approval_id
    assert result["snapshot_sha256"] == digest
    assert requests == [
        ("POST", "/api/v1/workspace-sandbox-approvals"),
        ("POST", f"/api/v1/workspace-sandbox-approvals/{approval_id}/decision"),
        ("POST", f"/api/v1/workspace-sandbox-approvals/{approval_id}/execute"),
    ]
    assert approval_requests[0].details["execution_boundary"] == "existing_root_owned_Docker_sandbox"
    assert approval_requests[0].details["host_project_execution"] is False
    assert approval_requests[0].details["snapshot_sha256"] == digest


def test_remote_sandbox_executor_denial_never_calls_execute():
    paths = []
    approval_id = "d0a0f0d0-1111-4222-8333-444455556667"

    def handler(request):
        paths.append(request.url.path)
        if request.url.path == "/api/v1/workspace-sandbox-approvals":
            return httpx.Response(201, json={
                "id": approval_id,
                "status": "pending",
                "snapshot_sha256": "c" * 64,
                "total_bytes": 1,
                "files": [],
            })
        if request.url.path.endswith("/decision"):
            assert json.loads(request.content) == {"decision": "reject"}
            return httpx.Response(200, json={"status": "rejected"})
        return httpx.Response(500)

    executor = CDSAISandboxExecutor(
        "http://orchestrator.local",
        api_key=None,
        approval=lambda request: False,
        transport=httpx.MockTransport(handler),
    )
    result = executor(
        "demo-workspace",
        "test",
        "Demo.csproj",
        [{"path": "Demo.csproj", "content": "<Project />"}],
        None,
    )

    assert result["status"] == "approval_denied"
    assert not any(path.endswith("/execute") for path in paths)


def test_remote_sandbox_executor_rejects_approval_if_stop_arrives_during_dialog():
    import threading

    event = threading.Event()
    paths = []
    approval_id = "d0a0f0d0-1111-4222-8333-444455556668"

    def handler(request):
        paths.append(request.url.path)
        if request.url.path == "/api/v1/workspace-sandbox-approvals":
            return httpx.Response(201, json={
                "id": approval_id,
                "status": "pending",
                "snapshot_sha256": "d" * 64,
                "total_bytes": 1,
                "files": [],
            })
        if request.url.path.endswith("/decision"):
            assert json.loads(request.content) == {"decision": "reject"}
            return httpx.Response(200, json={"status": "rejected"})
        return httpx.Response(500)

    def approve_then_stop(_request):
        event.set()
        return True

    executor = CDSAISandboxExecutor(
        "http://orchestrator.local",
        api_key=None,
        approval=approve_then_stop,
        transport=httpx.MockTransport(handler),
    )
    result = executor(
        "demo-workspace",
        "build",
        "Demo.csproj",
        [{"path": "Demo.csproj", "content": "<Project />"}],
        None,
        event,
    )

    assert result["status"] == "cancelled"
    assert not any(path.endswith("/execute") for path in paths)
