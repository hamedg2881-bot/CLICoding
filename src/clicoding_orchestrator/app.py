from __future__ import annotations

import asyncio
import hmac
import json
import logging
import time
import uuid
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from .config import Settings
from .database import Database
from .gateway import GatewayError, ModelGateway, OpenAICompatibleGateway
from .registry import RegistryError, Worker, WorkerRegistry, runtime_key
from .router import CapabilityRouter, NoEligibleWorker
from .orchestration import MultiModelOrchestrator


class TurnRequest(BaseModel):
    content: str = Field(min_length=1, max_length=200_000)
    # This is a task/capability hint, never a model or worker identifier.
    task_type: Literal["chat", "coding", "review", "vision"] = "chat"


class TurnResponse(BaseModel):
    conversation_id: str
    user_event_id: str
    assistant_event_id: str
    assistant_content: str
    producer: str
    execution_id: str | None = None


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str | list[dict[str, Any]]

    @field_validator("content")
    @classmethod
    def validate_content(cls, value):
        if isinstance(value, str):
            if not value.strip() or len(value) > 200_000:
                raise ValueError("text content must be non-empty and at most 200000 characters")
            return value
        if not isinstance(value, list) or not value or len(value) > 8:
            raise ValueError("multimodal content must contain between 1 and 8 parts")
        for part in value:
            if not isinstance(part, dict):
                raise ValueError("content parts must be objects")
            if part.get("type") == "text":
                text = part.get("text")
                if not isinstance(text, str) or len(text) > 200_000:
                    raise ValueError("text parts must contain at most 200000 characters")
            elif part.get("type") == "image_url":
                image = part.get("image_url")
                url = image.get("url") if isinstance(image, dict) else None
                if (
                    not isinstance(url, str)
                    or not url.startswith("data:image/")
                    or ";base64," not in url
                    or len(url) > 16_000_000
                ):
                    raise ValueError(
                        "image_url must be a base64 data URI no larger than 12 MB"
                    )
            else:
                raise ValueError("only text and image_url content parts are supported")
        return value


class SandboxApprovalCreate(BaseModel):
    language: Literal["python", "javascript", "typescript", "go", "rust", "java", "c", "cpp", "csharp", "php", "ruby"]
    source: str = Field(min_length=1, max_length=131072)


class SandboxApprovalDecision(BaseModel):
    decision: Literal["approve", "reject"]


class OpenAIChatRequest(BaseModel):
    model: str = "clicoding"
    messages: list[ChatMessage] = Field(min_length=1, max_length=500)
    conversation_id: str | None = None
    task_type: Literal["chat", "coding", "review", "vision"] = "chat"
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = Field(default=None, gt=0)
    user: str | None = None


logger = logging.getLogger(__name__)


_MULTIMODAL_STORAGE_PREFIX = "__CLICODING_MULTIMODAL_V1__"


def _chat_message(role: str, content: str | list[dict[str, Any]]) -> dict[str, object]:
    return {"role": role, "content": content}


def _store_content(content: str | list[dict[str, Any]]) -> str:
    if isinstance(content, str):
        return content
    return _MULTIMODAL_STORAGE_PREFIX + json.dumps(content, ensure_ascii=False)


def _restore_content(content: str) -> str | list[dict[str, Any]]:
    if not content.startswith(_MULTIMODAL_STORAGE_PREFIX):
        return content
    try:
        value = json.loads(content[len(_MULTIMODAL_STORAGE_PREFIX):])
    except json.JSONDecodeError:
        return content
    return value if isinstance(value, list) else content


def _content_text(content: str | list[dict[str, Any]]) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(
        str(part.get("text", ""))
        for part in content
        if part.get("type") == "text"
    )


def _has_images(content: str | list[dict[str, Any]]) -> bool:
    return isinstance(content, list) and any(
        part.get("type") == "image_url" for part in content
    )


def _openwebui_conversation_id(request: Request) -> str | None:
    """Derive a stable, per-user conversation ID from Open WebUI custom headers."""
    chat_id = request.headers.get("x-clicoding-chat-id", "").strip()
    user_id = request.headers.get("x-clicoding-user-id", "").strip()
    if not chat_id and not user_id:
        return None
    if not chat_id or not user_id:
        raise HTTPException(
            status_code=400,
            detail="Both X-CLICoding-Chat-ID and X-CLICoding-User-ID are required",
        )
    if len(chat_id) > 256 or len(user_id) > 256:
        raise HTTPException(status_code=400, detail="Open WebUI session identifiers are too long")
    stable_key = f"clicoding:open-webui:{user_id}:{chat_id}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, stable_key))


def create_app(
    settings: Settings | None = None,
    gateway: ModelGateway | None = None,
    database: Database | None = None,
    registry: WorkerRegistry | None = None,
    orchestrator: MultiModelOrchestrator | None = None,
) -> FastAPI:
    config = settings or Settings.from_env()
    db = database or Database(config.database_path)

    if registry is not None:
        worker_registry = registry
    elif config.workers_file:
        try:
            worker_registry = WorkerRegistry.from_yaml(config.workers_file)
        except RegistryError as exc:
            raise RuntimeError(f"Invalid CLICoding worker registry: {exc}") from exc
    else:
        # Backward-compatible single-worker mode for the first slice.
        worker_registry = WorkerRegistry(
            [
                Worker(
                    id="default",
                    base_url=config.upstream_base_url,
                    model=config.upstream_model,
                    capabilities=frozenset({"chat"}),
                    enabled=True,
                    priority=100,
                    api_key=config.upstream_api_key,
                    timeout_seconds=config.request_timeout_seconds,
                )
            ]
        )

    router = CapabilityRouter(worker_registry)
    multi_model = orchestrator or (MultiModelOrchestrator(db, router, max_parallel_tasks=config.max_parallel_tasks) if config.enable_planning else None)
    conversation_locks: dict[str, asyncio.Lock] = {}
    runtime_limits: dict[tuple[str, str], int] = {}
    for worker in worker_registry.enabled_workers():
        key = runtime_key(worker)
        runtime_limits[key] = min(
            runtime_limits.get(key, worker.max_concurrency),
            worker.max_concurrency,
        )
    worker_semaphores = {
        key: asyncio.Semaphore(limit) for key, limit in runtime_limits.items()
    }

    app = FastAPI(
        title="CLICoding Orchestrator",
        version="0.3.0",
        description=(
            "OpenAI-compatible single-assistant API with persistent conversations "
            "and capability-based internal worker selection."
        ),
    )
    app.state.database = db
    app.state.registry = worker_registry
    app.state.router = router
    app.state.orchestrator = multi_model

    @app.middleware("http")
    async def optional_bearer_auth(request: Request, call_next):
        # Health remains available to local service monitors. When configured, the
        # same shared secret protects every API and diagnostic endpoint.
        if config.api_key and request.url.path != "/health":
            supplied = request.headers.get("authorization", "")
            scheme, separator, token = supplied.partition(" ")
            if (
                not separator
                or scheme.lower() != "bearer"
                or not hmac.compare_digest(token, config.api_key)
            ):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid or missing bearer token"},
                    headers={"WWW-Authenticate": "Bearer"},
                )
        return await call_next(request)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "component": "conversation-router"}

    @app.get("/v1/models")
    async def list_public_models() -> dict[str, object]:
        # Never expose internal worker IDs or upstream model aliases to clients.
        return {
            "object": "list",
            "data": [
                {
                    "id": "clicoding",
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "clicoding",
                }
            ],
        }

    @app.get("/api/v1/workers")
    async def list_workers() -> dict[str, list[dict[str, object]]]:
        # Operator diagnostics only; this endpoint does not expose credentials.
        return {"workers": worker_registry.describe()}

    @app.post("/api/v1/conversations", status_code=201)
    async def create_conversation() -> dict[str, str]:
        return db.create_conversation()

    @app.get("/api/v1/conversations/{conversation_id}")
    async def get_conversation(conversation_id: str) -> dict[str, Any]:
        conversation = db.get_conversation(conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        for event in conversation["events"]:
            event["content"] = _restore_content(event["content"])
        return conversation

    async def run_turn(
        conversation_id: str,
        content: str | list[dict[str, Any]],
        task_type: str,
        system_messages: list[dict[str, str]] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any], str, str | None]:
        if not db.conversation_exists(conversation_id):
            raise HTTPException(status_code=404, detail="Conversation not found")
        lock = conversation_locks.setdefault(conversation_id, asyncio.Lock())
        async with lock:
            user_event = db.append_event(
                conversation_id, role="user", content=_store_content(content), producer="user"
            )
            events = [
                {"role": event["role"], "content": _restore_content(event["content"])}
                for event in db.list_events(conversation_id)
                if event["role"] in ("user", "assistant")
            ]
            history = list(system_messages or []) + events
            recent_image_parts: list[dict[str, Any]] = []
            for event in reversed(events):
                if event["role"] == "user" and _has_images(event["content"]):
                    recent_image_parts = [
                        part for part in event["content"]
                        if part.get("type") == "image_url"
                    ]
                    break
            vision_content: str | list[dict[str, Any]] = content
            if not _has_images(content) and recent_image_parts:
                current_text = _content_text(content)
                vision_content = (
                    ([{"type": "text", "text": current_text}] if current_text else [])
                    + recent_image_parts
                )
            execution_id: str | None = None

            if multi_model is not None:
                # The Orchestrator owns planning and internal worker handoffs.
                # The public API continues to expose one assistant identity.
                context_messages = list(system_messages or []) + events[:-1]
                context = "\n\n".join(
                    f"{message['role']}: {_content_text(message['content'])}"
                    for message in context_messages
                )
                try:
                    result = await multi_model.run(
                        conversation_id,
                        _content_text(content) or (
                            "Analyze the attached image(s)." if _has_images(content) else ""
                        ),
                        context,
                        user_content=vision_content,
                    )
                except NoEligibleWorker as exc:
                    raise HTTPException(status_code=503, detail=str(exc)) from exc
                except Exception as exc:
                    # Keep the public response generic, but retain the server-side
                    # traceback so upstream/planner failures can be diagnosed.
                    # Do not log request content, prompts, or credentials.
                    logger.exception(
                        "Multi-model orchestration failed (conversation_id=%s)",
                        conversation_id,
                    )
                    raise HTTPException(
                        status_code=502, detail="Multi-model orchestration failed"
                    ) from exc
                answer = result.answer
                producer = "orchestrator"
                execution_id = result.execution_id
            else:
                try:
                    worker = router.select("vision" if _has_images(vision_content) else task_type)
                except NoEligibleWorker as exc:
                    raise HTTPException(status_code=503, detail=str(exc)) from exc

                selected_gateway = gateway or OpenAICompatibleGateway(
                    base_url=worker.base_url,
                    model=worker.model,
                    api_key=worker.api_key,
                    timeout_seconds=worker.timeout_seconds,
                )
                try:
                    async with worker_semaphores[runtime_key(worker)]:
                        completion = await selected_gateway.complete(history)
                except GatewayError as exc:
                    raise HTTPException(status_code=502, detail=str(exc)) from exc
                except Exception as exc:
                    raise HTTPException(
                        status_code=502, detail="Unexpected upstream model failure"
                    ) from exc
                answer = completion.content
                producer = worker.id

            assistant_event = db.append_event(
                conversation_id,
                role="assistant",
                content=answer,
                producer=producer,
            )
            return user_event, assistant_event, answer, execution_id

    @app.post(
        "/api/v1/conversations/{conversation_id}/turns",
        response_model=TurnResponse,
    )
    async def create_turn(conversation_id: str, request: TurnRequest) -> TurnResponse:
        user_event, assistant_event, answer, execution_id = await run_turn(
            conversation_id, request.content, request.task_type
        )
        return TurnResponse(
            conversation_id=conversation_id,
            user_event_id=user_event["id"],
            assistant_event_id=assistant_event["id"],
            assistant_content=answer,
            producer=assistant_event["producer"],
            execution_id=execution_id,
        )

    @app.post("/v1/chat/completions")
    async def openai_chat_completions(
        http_request: Request, chat_request: OpenAIChatRequest
    ) -> Any:
        if chat_request.model not in ("clicoding", "assistant", "auto"):
            raise HTTPException(status_code=404, detail="Unknown public model alias")
        if not chat_request.messages or chat_request.messages[-1].role != "user":
            raise HTTPException(
                status_code=400,
                detail="The final message must be a user message",
            )

        last_user = chat_request.messages[-1].content
        supplied_system = [
            _chat_message(message.role, message.content)
            for message in chat_request.messages
            if message.role == "system"
        ]

        # Open WebUI drops arbitrary response fields such as conversation_id.
        # Its configured custom headers preserve chat/user identity on every turn.
        external_conversation_id = _openwebui_conversation_id(http_request)
        if chat_request.conversation_id:
            conversation_id = chat_request.conversation_id
            if not db.conversation_exists(conversation_id):
                raise HTTPException(status_code=404, detail="Conversation not found")
        elif external_conversation_id:
            db.get_or_create_conversation(external_conversation_id)
            conversation_id = external_conversation_id
        else:
            created = db.create_conversation()
            conversation_id = created["id"]
            # Generic OpenAI clients may bootstrap a new conversation with full history.
            for message in chat_request.messages[:-1]:
                if message.role in ("user", "assistant"):
                    db.append_event(
                        conversation_id,
                        role=message.role,
                        content=_store_content(message.content),
                        producer="client-import",
                    )

        user_event, assistant_event, answer, execution_id = await run_turn(
            conversation_id, last_user, chat_request.task_type, supplied_system
        )

        response_id = f"chatcmpl-{uuid.uuid4().hex}"
        created_at = int(time.time())
        if chat_request.stream:
            # Buffered SSE compatibility: orchestration completes before the final
            # answer is emitted. This is protocol streaming, not token-by-token output.
            async def event_stream():
                chunks = [
                    {
                        "id": response_id,
                        "object": "chat.completion.chunk",
                        "created": created_at,
                        "model": "clicoding",
                        "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
                    },
                    {
                        "id": response_id,
                        "object": "chat.completion.chunk",
                        "created": created_at,
                        "model": "clicoding",
                        "choices": [{"index": 0, "delta": {"content": answer}, "finish_reason": None}],
                    },
                    {
                        "id": response_id,
                        "object": "chat.completion.chunk",
                        "created": created_at,
                        "model": "clicoding",
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    },
                ]
                for chunk in chunks:
                    yield "data: " + json.dumps(chunk, ensure_ascii=False, separators=(",", ":")) + "\n\n"
                yield "data: [DONE]\n\n"

            return StreamingResponse(
                event_stream(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        return {
            "id": response_id,
            "object": "chat.completion",
            "created": created_at,
            "model": "clicoding",
            "conversation_id": conversation_id,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": answer},
                    "finish_reason": "stop",
                }
            ],
            "x_clicoding": {
                "user_event_id": user_event["id"],
                "assistant_event_id": assistant_event["id"],
                "producer": assistant_event["producer"],
                "system_messages_applied": len(supplied_system),
                "execution_id": execution_id,
                "orchestrated": multi_model is not None,
            },
        }

    @app.post(
        "/api/v1/conversations/{conversation_id}/orchestrated-turns",
        response_model=TurnResponse,
    )
    async def create_orchestrated_turn(
        conversation_id: str, request: TurnRequest
    ) -> TurnResponse:
        if multi_model is None:
            raise HTTPException(
                status_code=503, detail="Multi-model planning is disabled"
            )
        user_event, assistant_event, answer, execution_id = await run_turn(
            conversation_id, request.content, request.task_type
        )
        return TurnResponse(
            conversation_id=conversation_id,
            user_event_id=user_event["id"],
            assistant_event_id=assistant_event["id"],
            assistant_content=answer,
            producer=assistant_event["producer"],
            execution_id=execution_id,
        )

    @app.get("/api/v1/conversations/{conversation_id}/executions/{execution_id}")
    async def get_execution(conversation_id: str, execution_id: str) -> dict[str, Any]:
        execution = db.get_execution(execution_id)
        if execution is None or execution["conversation_id"] != conversation_id:
            raise HTTPException(status_code=404, detail="Execution not found")
        return execution

    @app.post(
        "/api/v1/executions/{execution_id}/tasks/{task_id}/sandbox-approvals",
        status_code=201,
    )
    async def create_sandbox_approval(
        execution_id: str, task_id: str, request: SandboxApprovalCreate
    ) -> dict[str, Any]:
        try:
            return db.create_sandbox_approval(
                execution_id, task_id, request.language, request.source
            )
        except ValueError as exc:
            message = str(exc)
            status = 404 if "does not exist" in message else 409
            raise HTTPException(status_code=status, detail=message) from exc

    @app.get(
        "/api/v1/executions/{execution_id}/tasks/{task_id}/sandbox-approvals"
    )
    async def list_sandbox_approvals(
        execution_id: str, task_id: str
    ) -> dict[str, Any]:
        execution = db.get_execution(execution_id)
        if execution is None:
            raise HTTPException(status_code=404, detail="Execution not found")
        if not any(task["task_id"] == task_id for task in execution["tasks"]):
            raise HTTPException(status_code=404, detail="Execution task not found")
        return {"approvals": db.list_sandbox_approvals(execution_id, task_id)}

    @app.get("/api/v1/sandbox-approvals/{approval_id}")
    async def get_sandbox_approval(approval_id: str) -> dict[str, Any]:
        approval = db.get_sandbox_approval(approval_id)
        if approval is None:
            raise HTTPException(status_code=404, detail="Sandbox approval not found")
        return approval

    @app.post("/api/v1/sandbox-approvals/{approval_id}/execute")
    async def execute_sandbox_approval(approval_id: str) -> dict[str, Any]:
        approval = db.get_sandbox_approval(approval_id)
        if approval is None:
            raise HTTPException(status_code=404, detail="Sandbox approval not found")
        if approval["status"] != "approved":
            raise HTTPException(
                status_code=409,
                detail=f"Sandbox approval is not approved (status={approval['status']})",
            )
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(
                    "/run/clicoding-sandbox/broker.sock", limit=600 * 1024
                ),
                timeout=3,
            )
            writer.write(
                (json.dumps({"approval_id": approval_id}) + "\n").encode("utf-8")
            )
            await writer.drain()
            raw = await asyncio.wait_for(reader.readline(), timeout=90)
            writer.close()
            await writer.wait_closed()
            if not raw or len(raw) > 512 * 1024:
                raise RuntimeError("broker returned an empty or oversized response")
            result = json.loads(raw.decode("utf-8"))
            if not isinstance(result, dict):
                raise RuntimeError("broker returned an invalid response")
            if result.get("status") == "broker_error":
                raise RuntimeError(result.get("error", "broker execution failed"))
            if result.get("status") == "rejected":
                raise HTTPException(status_code=409, detail=result.get("error", "Execution rejected"))
            return result
        except (OSError, asyncio.TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
            raise HTTPException(
                status_code=503, detail=f"Sandbox broker unavailable or failed: {exc}"
            ) from exc

    @app.get("/api/v1/sandbox-approvals/{approval_id}/result")
    async def get_sandbox_execution_result(approval_id: str) -> dict[str, Any]:
        approval = db.get_sandbox_approval(approval_id)
        if approval is None:
            raise HTTPException(status_code=404, detail="Sandbox approval not found")
        result = db.get_sandbox_execution_result(approval_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Sandbox execution result not found")
        return result

    @app.post("/api/v1/sandbox-approvals/{approval_id}/decision")
    async def decide_sandbox_approval(
        approval_id: str, request: SandboxApprovalDecision
    ) -> dict[str, Any]:
        try:
            approval = db.decide_sandbox_approval(approval_id, request.decision)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if approval is None:
            raise HTTPException(status_code=404, detail="Sandbox approval not found")
        return approval

    return app
