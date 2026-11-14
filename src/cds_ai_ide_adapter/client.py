from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from clicoding_orchestrator.context import redact_sensitive_data
from .tool_host import ToolApproval, WorkspaceToolHost


class CDSAIClientError(RuntimeError):
    pass


@dataclass(frozen=True)
class CDSAIChatResult:
    conversation_id: str
    message: dict[str, Any]
    tool_rounds: int
    response: dict[str, Any]


class CDSAIClient:
    """Minimal async OpenAI-compatible client with a workstation-local tool loop."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        timeout_seconds: float = 180.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("base_url must be an HTTP(S) URL")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        normalized = base_url.rstrip("/")
        self.url = (
            normalized if normalized.endswith("/chat/completions")
            else f"{normalized}/chat/completions"
        )
        self.api_root = normalized
        if self.api_root.endswith("/chat/completions"):
            self.api_root = self.api_root[: -len("/chat/completions")]
        if self.api_root.endswith("/v1"):
            self.api_root = self.api_root[:-3]
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    def create_sandbox_executor(
        self, approval: Callable[[ToolApproval], bool] | None
    ) -> "CDSAISandboxExecutor":
        """Create a workstation callback that uses the existing Orchestrator Docker broker."""
        return CDSAISandboxExecutor(
            self.api_root,
            api_key=self.api_key,
            approval=approval,
            timeout_seconds=max(self.timeout_seconds, 700.0),
        )

    async def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        tool_host: WorkspaceToolHost,
        *,
        model: str = "CDS_AI",
        conversation_id: str | None = None,
        task_type: str = "chat",
        tool_choice: str | dict[str, Any] = "auto",
        max_tool_rounds: int = 8,
    ) -> CDSAIChatResult:
        if not messages or messages[-1].get("role") != "user":
            raise ValueError("messages must end with a user message")
        if max_tool_rounds < 0 or max_tool_rounds > 32:
            raise ValueError("max_tool_rounds must be between 0 and 32")
        system_messages = [message for message in messages if message.get("role") == "system"]
        next_messages = messages
        tool_rounds = 0
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            transport=self.transport,
        ) as client:
            while True:
                payload: dict[str, Any] = {
                    "model": model,
                    "messages": next_messages,
                    "task_type": task_type,
                    "stream": False,
                    "tools": tool_host.definitions,
                    "tool_choice": tool_choice,
                }
                if conversation_id is not None:
                    payload["conversation_id"] = conversation_id
                try:
                    response = await client.post(self.url, json=payload, headers=headers)
                except httpx.HTTPError as exc:
                    raise CDSAIClientError("CDS_AI request transport failed") from exc
                if response.is_error:
                    try:
                        error_body = response.json()
                        detail = error_body.get("detail", "request rejected")
                    except (ValueError, AttributeError):
                        detail = "request rejected"
                    raise CDSAIClientError(
                        f"CDS_AI returned HTTP {response.status_code}: {str(detail)[:1000]}"
                    )
                try:
                    body = response.json()
                    choice = body["choices"][0]
                    message = choice["message"]
                except (ValueError, KeyError, IndexError, TypeError) as exc:
                    raise CDSAIClientError("CDS_AI returned an invalid chat-completion response") from exc

                conversation_id = body.get("conversation_id") or conversation_id
                if not isinstance(conversation_id, str) or not conversation_id:
                    raise CDSAIClientError("CDS_AI response did not include a conversation_id")

                tool_calls = message.get("tool_calls") or []
                if not tool_calls:
                    return CDSAIChatResult(
                        conversation_id=conversation_id,
                        message=message,
                        tool_rounds=tool_rounds,
                        response=body,
                    )
                if len(tool_calls) != 1:
                    raise CDSAIClientError("CDS_AI returned more than one tool call in a turn")
                if tool_rounds >= max_tool_rounds:
                    raise CDSAIClientError("maximum workstation tool-call rounds exceeded")

                tool_rounds += 1
                cancellation_event = threading.Event()
                tool_task = asyncio.create_task(asyncio.to_thread(
                    tool_host.execute_tool_call,
                    tool_calls[0],
                    conversation_id,
                    cancellation_event,
                ))
                try:
                    tool_message = await asyncio.shield(tool_task)
                except asyncio.CancelledError:
                    # A worker thread cannot be force-cancelled. Signal it so
                    # pending file writes stop at their next safe commit point.
                    cancellation_event.set()
                    raise
                next_messages = [*system_messages, tool_message]


class CDSAISandboxExecutor:
    """Submit immutable workspace snapshots to the existing approved Docker sandbox."""

    def __init__(
        self,
        api_root: str,
        api_key: str | None,
        approval: Callable[[ToolApproval], bool] | None,
        timeout_seconds: float = 700.0,
        transport: httpx.BaseTransport | None = None,
    ):
        if not api_root.startswith(("http://", "https://")):
            raise ValueError("api_root must be an HTTP(S) URL")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.api_root = api_root.rstrip("/")
        self.api_key = api_key
        self.approval = approval
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    def __call__(
        self,
        workspace_id: str,
        action: str,
        project_path: str,
        files: list[dict[str, str]],
        approval_callback: Callable[[ToolApproval], bool] | None,
        cancellation_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        if action not in {"build", "test"}:
            raise ValueError("unsupported sandbox action")
        if cancellation_event is not None and cancellation_event.is_set():
            return {"status": "cancelled"}
        callback = self.approval or approval_callback
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        timeout = httpx.Timeout(
            self.timeout_seconds, connect=5.0, write=30.0, pool=5.0
        )
        base = f"{self.api_root}/api/v1/workspace-sandbox-approvals"
        payload = {
            "workspace_id": workspace_id,
            "action": action,
            "project_path": project_path,
            "files": files,
        }
        try:
            with httpx.Client(
                timeout=timeout, transport=self.transport
            ) as client:
                created = client.post(base, json=payload, headers=headers)
                if created.is_error:
                    raise CDSAIClientError(
                        f"workspace approval request failed with HTTP {created.status_code}"
                    )
                approval_record = created.json()
                approval_id = approval_record.get("id")
                digest = approval_record.get("snapshot_sha256")
                if not isinstance(approval_id, str) or not isinstance(digest, str):
                    raise CDSAIClientError("Orchestrator returned an invalid workspace approval")
                manifest = [
                    {
                        "path": item.get("path"),
                        "sha256": item.get("sha256"),
                        "bytes": item.get("bytes"),
                    }
                    for item in approval_record.get("files", [])
                ]
                preview_budget = 32_000
                previews = []
                for item in files:
                    if preview_budget <= 0:
                        break
                    redacted = redact_sensitive_data(item["content"])
                    preview = redacted[: min(2048, preview_budget)]
                    preview_budget -= len(preview)
                    previews.append({
                        "path": item["path"],
                        "content_preview": preview,
                        "truncated": len(redacted) > len(preview),
                    })
                request = ToolApproval(
                    tool_name=f"workspace_dotnet_{action}",
                    workspace_id=workspace_id,
                    summary=f"{action.title()} {project_path} in the isolated Docker sandbox",
                    details={
                        "action": action,
                        "project_path": project_path,
                        "snapshot_sha256": digest,
                        "file_count": len(manifest),
                        "total_bytes": approval_record.get("total_bytes"),
                        "file_manifest": manifest,
                        "file_previews": previews,
                        "source_review_required": any(item["truncated"] for item in previews)
                        or len(previews) < len(files),
                        "execution_boundary": "existing_root_owned_Docker_sandbox",
                        "network": "disabled",
                        "workspace_mount": "read-only",
                        "nuget_feed": "operator-managed local feed, read-only",
                        "host_project_execution": False,
                    },
                )
                if cancellation_event is not None and cancellation_event.is_set():
                    decision = "reject"
                elif callback is None or not callback(request):
                    decision = "reject"
                elif cancellation_event is not None and cancellation_event.is_set():
                    decision = "reject"
                else:
                    decision = "approve"
                decided = client.post(
                    f"{base}/{approval_id}/decision",
                    json={"decision": decision},
                    headers=headers,
                )
                if decided.is_error:
                    raise CDSAIClientError(
                        f"workspace approval decision failed with HTTP {decided.status_code}"
                    )
                if decision != "approve":
                    return {
                        "status": "cancelled" if cancellation_event and cancellation_event.is_set()
                        else "approval_denied",
                        "approval_id": approval_id,
                        "snapshot_sha256": digest,
                    }
                if cancellation_event is not None and cancellation_event.is_set():
                    cancelled = client.post(
                        f"{base}/{approval_id}/cancel", headers=headers
                    )
                    if cancelled.is_error:
                        raise CDSAIClientError("workspace approval cancellation failed")
                    return {
                        "status": "cancelled",
                        "approval_id": approval_id,
                        "snapshot_sha256": digest,
                    }

                monitor_stop = threading.Event()
                cancellation_result: dict[str, Any] = {}

                def monitor_cancellation() -> None:
                    assert cancellation_event is not None
                    while not monitor_stop.wait(0.1):
                        if cancellation_event.is_set():
                            try:
                                with httpx.Client(timeout=httpx.Timeout(20.0, connect=3.0)) as cancel_client:
                                    response = cancel_client.post(
                                        f"{base}/{approval_id}/cancel", headers=headers
                                    )
                                    if response.is_error:
                                        cancellation_result["error"] = (
                                            f"cancel endpoint returned HTTP {response.status_code}"
                                        )
                                    else:
                                        cancellation_result.update(response.json())
                            except (httpx.HTTPError, ValueError) as exc:
                                cancellation_result["error"] = type(exc).__name__
                            return

                monitor = None
                if cancellation_event is not None:
                    monitor = threading.Thread(
                        target=monitor_cancellation,
                        name=f"cds-ai-sandbox-cancel-{approval_id[:8]}",
                        daemon=True,
                    )
                    monitor.start()
                try:
                    executed = client.post(
                        f"{base}/{approval_id}/execute",
                        headers=headers,
                    )
                finally:
                    monitor_stop.set()
                    if monitor is not None:
                        monitor.join(timeout=2)
                if executed.is_error:
                    raise CDSAIClientError(
                        f"workspace sandbox execution failed with HTTP {executed.status_code}"
                    )
                result = executed.json()
                if not isinstance(result, dict):
                    raise CDSAIClientError("sandbox broker returned an invalid result")
                result.setdefault("approval_id", approval_id)
                result.setdefault("snapshot_sha256", digest)
                if cancellation_event is not None and cancellation_event.is_set():
                    if cancellation_result.get("status") in {"cancel_requested", "cancel_pending"} or cancellation_result.get("error"):
                        if result.get("status") != "cancelled":
                            result["status"] = "cancel_pending"
                    if cancellation_result:
                        result["cancellation"] = cancellation_result
                return result
        except httpx.HTTPError as exc:
            raise CDSAIClientError("workspace sandbox request transport failed") from exc
