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

from .tool_protocol import (
    ToolProtocolError, pending_tool_call, validate_tool_calls,
    validate_tool_definitions, validate_tool_result,
)

from .config import Settings
from .context import ContextBudgetExceeded, ensure_context_budget
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
    role: Literal["system", "user", "assistant", "tool"]
    content: str | list[dict[str, Any]] | None
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    name: str | None = None

    @field_validator("content")
    @classmethod
    def validate_content(cls, value):
        if value is None:
            return None
        if isinstance(value, str):
            if len(value) > 200_000:
                raise ValueError("text content must be at most 200000 characters")
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
    model: str = "CDS_AI"
    messages: list[ChatMessage] = Field(min_length=1, max_length=500)
    conversation_id: str | None = None
    task_type: Literal["chat", "coding", "review", "vision"] = "chat"
    stream: bool = False
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, gt=0, le=131072)
    top_p: float | None = Field(default=None, gt=0, le=1)
    presence_penalty: float | None = Field(default=None, ge=-2, le=2)
    frequency_penalty: float | None = Field(default=None, ge=-2, le=2)
    stop: str | list[str] | None = None
    user: str | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None

    @field_validator("stop")
    @classmethod
    def validate_stop(cls, value):
        if value is None:
            return None
        values = [value] if isinstance(value, str) else value
        if (
            not isinstance(values, list)
            or not 1 <= len(values) <= 4
            or any(not isinstance(item, str) or not item or len(item) > 256 for item in values)
        ):
            raise ValueError("stop must be a string or 1-4 non-empty strings of at most 256 characters")
        return value


logger = logging.getLogger(__name__)


_MULTIMODAL_STORAGE_PREFIX = "__CLICODING_MULTIMODAL_V1__"


def _chat_message(message: ChatMessage) -> dict[str, Any]:
    result: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_call_id is not None:
        result["tool_call_id"] = message.tool_call_id
    if message.tool_calls is not None:
        result["tool_calls"] = message.tool_calls
    if message.name is not None:
        result["name"] = message.name
    return result


def _store_content(content: str | list[dict[str, Any]] | None) -> str:
    if content is None:
        return ""
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


def _append_input_event(
    database: Database,
    conversation_id: str,
    role: str,
    content: str | list[dict[str, Any]] | None,
    producer: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Reuse an identical unfinished input event after a failed or cancelled request."""
    stored_content = _store_content(content)
    events = database.list_events(conversation_id)
    if events:
        last = events[-1]
        if (
            last["role"] == role
            and last["content"] == stored_content
            and last.get("metadata") == metadata
        ):
            return last
    return database.append_event(
        conversation_id,
        role=role,
        content=stored_content,
        producer=producer,
        metadata=metadata,
    )


def _content_text(content: str | list[dict[str, Any]] | None) -> str:
    if content is None:
        return ""
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
        title="CDS_AI Assistant",
        version="0.4.0",
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
        # Health remains available to local service monitors. User tokens provide
        # isolated conversation namespaces; a global key is an admin/service key.
        request.state.principal_id = "anonymous"
        request.state.is_admin = False
        if request.url.path != "/health" and (config.api_key or config.user_api_keys):
            supplied = request.headers.get("authorization", "")
            scheme, separator, token = supplied.partition(" ")
            if not separator or scheme.lower() != "bearer" or not token:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid or missing bearer token"},
                    headers={"WWW-Authenticate": "Bearer"},
                )

            matched_user = next(
                (
                    user_id for user_id, user_token in config.user_api_keys.items()
                    if hmac.compare_digest(token, user_token)
                ),
                None,
            )
            if matched_user is not None:
                request.state.principal_id = matched_user
            elif config.api_key and hmac.compare_digest(token, config.api_key):
                if config.trust_user_header:
                    # Open WebUI can supply its authenticated user ID through a
                    # server-side header template. Trust it only behind a private,
                    # trusted proxy that strips client-supplied identity headers.
                    header_user = request.headers.get("x-clicoding-user-id", "").strip()
                    if header_user:
                        if len(header_user) > 128 or any(ch.isspace() for ch in header_user):
                            return JSONResponse(
                                status_code=400,
                                content={"detail": "Invalid trusted user identity header"},
                            )
                        request.state.principal_id = header_user
                    else:
                        request.state.principal_id = "shared"
                elif config.user_api_keys:
                    request.state.principal_id = "admin"
                    request.state.is_admin = True
                else:
                    request.state.principal_id = "shared"
                    request.state.is_admin = True
            else:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid or missing bearer token"},
                    headers={"WWW-Authenticate": "Bearer"},
                )
        return await call_next(request)

    def _owner_scope(request: Request) -> str | None:
        return None if request.state.is_admin else request.state.principal_id

    def _owns_conversation(request: Request, conversation_id: str) -> bool:
        return db.conversation_exists(
            conversation_id,
            owner_id=_owner_scope(request),
        )

    def _owns_execution(request: Request, execution_id: str) -> dict[str, Any] | None:
        execution = db.get_execution(execution_id)
        if execution is None or not _owns_conversation(
            request, execution["conversation_id"]
        ):
            return None
        return execution

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
                    "id": "CDS_AI",
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "CDS_AI",
                }
            ],
        }

    @app.get("/api/v1/workers")
    async def list_workers(http_request: Request) -> dict[str, list[dict[str, object]]]:
        # Runtime topology is operator-only, even though credentials are omitted.
        if not http_request.state.is_admin:
            raise HTTPException(status_code=403, detail="Administrator access required")
        return {"workers": worker_registry.describe()}

    @app.post("/api/v1/conversations", status_code=201)
    async def create_conversation(http_request: Request) -> dict[str, str]:
        return db.create_conversation(http_request.state.principal_id)

    @app.get("/api/v1/conversations/{conversation_id}")
    async def get_conversation(
        conversation_id: str, http_request: Request
    ) -> dict[str, Any]:
        conversation = db.get_conversation(
            conversation_id, owner_id=_owner_scope(http_request)
        )
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
        force_orchestration: bool = False,
        input_role: str = "user",
        input_metadata: dict[str, Any] | None = None,
        tool_definitions: dict[str, dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        generation_options: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any], str, str | None]:
        if not db.conversation_exists(conversation_id):
            raise HTTPException(status_code=404, detail="Conversation not found")
        lock = conversation_locks.setdefault(conversation_id, asyncio.Lock())
        async with lock:
            input_event = _append_input_event(
                db,
                conversation_id,
                input_role,
                content,
                "user" if input_role == "user" else "tool-client",
                input_metadata,
            )
            events = []
            for event in db.list_events(conversation_id):
                if event["role"] not in ("user", "assistant", "tool"):
                    continue
                item = {"role": event["role"], "content": _restore_content(event["content"])}
                if event.get("metadata"):
                    item.update(event["metadata"])
                    if event["role"] == "assistant" and event["metadata"].get("tool_calls"):
                        item["content"] = None
                events.append(item)
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
            returned_tool_calls: list[dict[str, Any]] = []

            if tool_definitions is None and multi_model is not None and (
                force_orchestration or task_type != "chat" or _has_images(vision_content)
            ):
                # Specialist work is orchestrated; ordinary chat stays on the
                # direct conversation path and never depends on Planner output.
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
                except ContextBudgetExceeded as exc:
                    raise HTTPException(status_code=413, detail=str(exc)) from exc
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
                    required_capabilities = (
                        {"tool_calling"}
                        if tool_definitions is not None
                        else ({"vision"} if _has_images(vision_content) else {task_type})
                    )
                    if tool_definitions is not None:
                        if task_type != "chat":
                            required_capabilities.add(task_type)
                        if _has_images(vision_content):
                            required_capabilities.add("vision")
                    worker = router.select_capabilities(required_capabilities)
                except NoEligibleWorker as exc:
                    raise HTTPException(status_code=503, detail=str(exc)) from exc

                selected_gateway = gateway or OpenAICompatibleGateway(
                    base_url=worker.base_url,
                    model=worker.model,
                    api_key=worker.api_key,
                    timeout_seconds=worker.timeout_seconds,
                )
                try:
                    if generation_options and "max_tokens" in generation_options:
                        if generation_options["max_tokens"] > worker.max_output_tokens:
                            raise HTTPException(
                                status_code=400,
                                detail="max_tokens exceeds the selected worker output limit",
                            )
                    reserve_output = (
                        generation_options.get("max_tokens", worker.max_output_tokens)
                        if generation_options else worker.max_output_tokens
                    )
                    try:
                        ensure_context_budget(
                            history,
                            context_window_tokens=worker.context_window_tokens,
                            reserved_output_tokens=reserve_output,
                        )
                    except ContextBudgetExceeded as exc:
                        raise HTTPException(status_code=413, detail=str(exc)) from exc

                    async with worker_semaphores[runtime_key(worker)]:
                        kwargs: dict[str, Any] = {}
                        if tool_definitions is not None:
                            kwargs["tools"] = list(tool_definitions.values())
                            if tool_choice is not None:
                                kwargs["tool_choice"] = tool_choice
                        effective_options = dict(generation_options or {})
                        if isinstance(selected_gateway, OpenAICompatibleGateway):
                            effective_options.setdefault("max_tokens", worker.max_output_tokens)
                        if effective_options:
                            kwargs["options"] = effective_options
                        completion = await selected_gateway.complete(history, **kwargs)
                except HTTPException:
                    raise
                except GatewayError as exc:
                    raise HTTPException(status_code=502, detail=str(exc)) from exc
                except Exception as exc:
                    raise HTTPException(
                        status_code=502, detail="Unexpected upstream model failure"
                    ) from exc
                answer = completion.content
                producer = worker.id
                try:
                    returned_tool_calls = validate_tool_calls(
                        completion.tool_calls, tool_definitions or {}
                    )
                except ToolProtocolError as exc:
                    logger.warning("Rejected invalid model tool call: %s", exc)
                    raise HTTPException(
                        status_code=502, detail="Upstream returned an invalid tool call"
                    ) from exc
                required_tool_name = (
                    tool_choice.get("function", {}).get("name")
                    if isinstance(tool_choice, dict) else None
                )
                if (tool_choice == "required" or required_tool_name) and not returned_tool_calls:
                    raise HTTPException(
                        status_code=502, detail="Upstream did not honor required tool_choice"
                    )
                if (
                    required_tool_name and returned_tool_calls
                    and returned_tool_calls[0]["function"]["name"] != required_tool_name
                ):
                    raise HTTPException(
                        status_code=502, detail="Upstream called a different function than requested"
                    )
                if tool_choice == "none" and returned_tool_calls:
                    raise HTTPException(
                        status_code=502, detail="Upstream returned a tool call when tool_choice was none"
                    )

            assistant_metadata = (
                {"tool_calls": returned_tool_calls, "finish_reason": "tool_calls"}
                if returned_tool_calls else None
            )
            assistant_event = db.append_event(
                conversation_id, role="assistant", content=answer,
                producer=producer, metadata=assistant_metadata,
            )
            return input_event, assistant_event, answer, execution_id

    async def stream_direct_turn(
        conversation_id: str,
        content: str | list[dict[str, Any]],
        system_messages: list[dict[str, str]],
        worker: Worker,
        selected_gateway: ModelGateway,
        response_id: str,
        created_at: int,
        generation_options: dict[str, Any] | None = None,
        tool_definitions: dict[str, dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        input_role: str = "user",
        input_metadata: dict[str, Any] | None = None,
    ):
        """Stream direct chat and validated function-tool deltas; persist only on success."""
        lock = conversation_locks.setdefault(conversation_id, asyncio.Lock())
        async with lock:
            events = []
            for event in db.list_events(conversation_id):
                if event["role"] not in ("user", "assistant", "tool"):
                    continue
                item = {"role": event["role"], "content": _restore_content(event["content"])}
                if event.get("metadata"):
                    item.update(event["metadata"])
                    if event["role"] == "assistant" and event["metadata"].get("tool_calls"):
                        item["content"] = None
                events.append(item)
            input_message = {"role": input_role, "content": content}
            if input_metadata:
                input_message.update(input_metadata)
            history = list(system_messages) + events + [input_message]
            try:
                ensure_context_budget(
                    history,
                    context_window_tokens=worker.context_window_tokens,
                    reserved_output_tokens=(
                        generation_options.get("max_tokens", worker.max_output_tokens)
                        if generation_options else worker.max_output_tokens
                    ),
                )
            except ContextBudgetExceeded as exc:
                yield "data: " + json.dumps({
                    "error": {"message": str(exc), "type": "context_length_exceeded"}
                }, ensure_ascii=False) + "\n\n"
                yield "data: [DONE]\n\n"
                return
            _append_input_event(
                db,
                conversation_id,
                input_role,
                content,
                "user" if input_role == "user" else "tool-client",
                input_metadata,
            )
            role_chunk = {
                "id": response_id,
                "object": "chat.completion.chunk",
                "created": created_at,
                "model": "CDS_AI",
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
            }
            yield "data: " + json.dumps(role_chunk, ensure_ascii=False, separators=(",", ":")) + "\n\n"
            answer_parts: list[str] = []
            tool_calls_by_index: dict[int, dict[str, Any]] = {}
            finish_reason = "stop"
            try:
                stream_options = dict(generation_options or {})
                if isinstance(selected_gateway, OpenAICompatibleGateway):
                    stream_options.setdefault("max_tokens", worker.max_output_tokens)
                async with worker_semaphores[runtime_key(worker)]:
                    if tool_definitions is not None:
                        structured_stream = getattr(selected_gateway, "stream_events", None)
                        if callable(structured_stream):
                            stream = structured_stream(
                                history,
                                tools=list(tool_definitions.values()),
                                tool_choice=tool_choice,
                                options=stream_options or None,
                            )
                            async for event in stream:
                                piece = event.get("content")
                                if isinstance(piece, str) and piece:
                                    answer_parts.append(piece)
                                    chunk = {
                                        "id": response_id,
                                        "object": "chat.completion.chunk",
                                        "created": created_at,
                                        "model": "CDS_AI",
                                        "choices": [{
                                            "index": 0,
                                            "delta": {"content": piece},
                                            "finish_reason": None,
                                        }],
                                    }
                                    yield "data: " + json.dumps(chunk, ensure_ascii=False, separators=(",", ":")) + "\n\n"
                                deltas = event.get("tool_calls")
                                if deltas:
                                    if not isinstance(deltas, list):
                                        raise GatewayError("Upstream returned invalid tool-call deltas")
                                    for delta in deltas:
                                        if not isinstance(delta, dict):
                                            raise GatewayError("Upstream returned an invalid tool-call delta")
                                        index = delta.get("index", 0)
                                        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
                                            raise GatewayError("Upstream returned an invalid tool-call index")
                                        current = tool_calls_by_index.setdefault(
                                            index,
                                            {
                                                "id": "",
                                                "type": "function",
                                                "function": {"name": "", "arguments": ""},
                                            },
                                        )
                                        if len(tool_calls_by_index) > 1:
                                            raise GatewayError("Only one tool call is supported per assistant turn")
                                        if delta.get("id"):
                                            current["id"] = delta["id"]
                                        if delta.get("type"):
                                            current["type"] = delta["type"]
                                        function_delta = delta.get("function") or {}
                                        if not isinstance(function_delta, dict):
                                            raise GatewayError("Upstream returned an invalid function delta")
                                        if function_delta.get("name"):
                                            if not isinstance(function_delta["name"], str):
                                                raise GatewayError("Upstream returned an invalid function name delta")
                                            current["function"]["name"] += function_delta["name"]
                                        if function_delta.get("arguments"):
                                            if not isinstance(function_delta["arguments"], str):
                                                raise GatewayError("Upstream returned invalid function arguments")
                                            current["function"]["arguments"] += function_delta["arguments"]
                                    chunk = {
                                        "id": response_id,
                                        "object": "chat.completion.chunk",
                                        "created": created_at,
                                        "model": "CDS_AI",
                                        "choices": [{
                                            "index": 0,
                                            "delta": {"tool_calls": deltas},
                                            "finish_reason": None,
                                        }],
                                    }
                                    yield "data: " + json.dumps(chunk, ensure_ascii=False, separators=(",", ":")) + "\n\n"
                                if event.get("finish_reason") is not None:
                                    finish_reason = event["finish_reason"]
                        else:
                            kwargs: dict[str, Any] = {
                                "tools": list(tool_definitions.values()),
                            }
                            if tool_choice is not None:
                                kwargs["tool_choice"] = tool_choice
                            if stream_options:
                                kwargs["options"] = stream_options
                            completion = await selected_gateway.complete(history, **kwargs)
                            if completion.content:
                                answer_parts.append(completion.content)
                                delta = {"content": completion.content}
                                chunk = {
                                    "id": response_id,
                                    "object": "chat.completion.chunk",
                                    "created": created_at,
                                    "model": "CDS_AI",
                                    "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
                                }
                                yield "data: " + json.dumps(chunk, ensure_ascii=False, separators=(",", ":")) + "\n\n"
                            validated_calls = validate_tool_calls(
                                completion.tool_calls, tool_definitions
                            )
                            if validated_calls:
                                tool_calls_by_index = {0: validated_calls[0]}
                                chunk = {
                                    "id": response_id,
                                    "object": "chat.completion.chunk",
                                    "created": created_at,
                                    "model": "CDS_AI",
                                    "choices": [{
                                        "index": 0,
                                        "delta": {"tool_calls": [{
                                            "index": 0,
                                            **validated_calls[0],
                                        }]},
                                        "finish_reason": None,
                                    }],
                                }
                                yield "data: " + json.dumps(chunk, ensure_ascii=False, separators=(",", ":")) + "\n\n"
                            finish_reason = completion.finish_reason
                    else:
                        stream_method = getattr(selected_gateway, "stream", None)
                        if callable(stream_method):
                            stream = (
                                stream_method(history, options=stream_options)
                                if stream_options else stream_method(history)
                            )
                            async for piece in stream:
                                if not isinstance(piece, str):
                                    raise GatewayError("Upstream stream yielded a non-text delta")
                                if not piece:
                                    continue
                                answer_parts.append(piece)
                                chunk = {
                                    "id": response_id,
                                    "object": "chat.completion.chunk",
                                    "created": created_at,
                                    "model": "CDS_AI",
                                    "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}],
                                }
                                yield "data: " + json.dumps(chunk, ensure_ascii=False, separators=(",", ":")) + "\n\n"
                        else:
                            completion = (
                                await selected_gateway.complete(history, options=stream_options)
                                if stream_options else await selected_gateway.complete(history)
                            )
                            if completion.content:
                                answer_parts.append(completion.content)
                                chunk = {
                                    "id": response_id,
                                    "object": "chat.completion.chunk",
                                    "created": created_at,
                                    "model": "CDS_AI",
                                    "choices": [{"index": 0, "delta": {"content": completion.content}, "finish_reason": None}],
                                }
                                yield "data: " + json.dumps(chunk, ensure_ascii=False, separators=(",", ":")) + "\n\n"

                validated_tool_calls: list[dict[str, Any]] = []
                if tool_calls_by_index:
                    ordered = [
                        tool_calls_by_index[index] for index in sorted(tool_calls_by_index)
                    ]
                    validated_tool_calls = validate_tool_calls(ordered, tool_definitions or {})
                required_tool_name = (
                    tool_choice.get("function", {}).get("name")
                    if isinstance(tool_choice, dict) else None
                )
                if (tool_choice == "required" or required_tool_name) and not validated_tool_calls:
                    raise GatewayError("Upstream did not honor required tool_choice")
                if (
                    required_tool_name and validated_tool_calls
                    and validated_tool_calls[0]["function"]["name"] != required_tool_name
                ):
                    raise GatewayError("Upstream called a different function than requested")
                if tool_choice == "none" and validated_tool_calls:
                    raise GatewayError("Upstream returned a tool call when tool_choice was none")

                assistant_metadata = (
                    {"tool_calls": validated_tool_calls, "finish_reason": "tool_calls"}
                    if validated_tool_calls else None
                )
                db.append_event(
                    conversation_id,
                    role="assistant",
                    content="".join(answer_parts),
                    producer=worker.id,
                    metadata=assistant_metadata,
                )
                final_chunk = {
                    "id": response_id,
                    "object": "chat.completion.chunk",
                    "created": created_at,
                    "model": "CDS_AI",
                    "choices": [{
                        "index": 0,
                        "delta": {},
                        "finish_reason": "tool_calls" if validated_tool_calls else finish_reason,
                    }],
                }
                yield "data: " + json.dumps(final_chunk, ensure_ascii=False, separators=(",", ":")) + "\n\n"
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Direct chat streaming failed (conversation_id=%s)", conversation_id
                )
                yield "data: " + json.dumps({
                    "error": {
                        "message": "The assistant stream was interrupted. Please retry.",
                        "type": "server_error",
                    }
                }, ensure_ascii=False) + "\n\n"
            yield "data: [DONE]\n\n"

    @app.post(
        "/api/v1/conversations/{conversation_id}/turns",
        response_model=TurnResponse,
    )
    async def create_turn(
        conversation_id: str, turn_request: TurnRequest, http_request: Request
    ) -> TurnResponse:
        if not _owns_conversation(http_request, conversation_id):
            raise HTTPException(status_code=404, detail="Conversation not found")
        user_event, assistant_event, answer, execution_id = await run_turn(
            conversation_id, turn_request.content, turn_request.task_type
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
        if chat_request.model not in ("CDS_AI", "cds_ai", "clicoding", "assistant", "auto"):
            raise HTTPException(status_code=404, detail="Unknown public model alias")
        if not chat_request.messages or chat_request.messages[-1].role not in ("user", "tool"):
            raise HTTPException(status_code=400, detail="The final message must be a user or tool message")
        last_message = chat_request.messages[-1]
        if last_message.role == "user" and (
            not isinstance(last_message.content, (str, list))
            or (isinstance(last_message.content, str) and not last_message.content.strip())
        ):
            raise HTTPException(status_code=400, detail="User message content is required")
        if last_message.role == "tool" and (
            not isinstance(last_message.content, str) or not last_message.content
        ):
            raise HTTPException(status_code=400, detail="Tool result content is required")
        if last_message.role == "tool" and chat_request.tools is None:
            raise HTTPException(status_code=400, detail="Tool definitions must accompany tool results")
        last_user = last_message.content
        supplied_system = [
            _chat_message(message) for message in chat_request.messages
            if message.role == "system"
        ]
        try:
            tool_definitions = validate_tool_definitions(chat_request.tools)
            if chat_request.tool_choice is not None:
                if chat_request.tools is None:
                    raise ToolProtocolError("tool_choice requires tools")
                if isinstance(chat_request.tool_choice, str):
                    if chat_request.tool_choice not in {"auto", "none", "required"}:
                        raise ToolProtocolError("tool_choice must be auto, none, required, or a function selector")
                elif isinstance(chat_request.tool_choice, dict):
                    selector = chat_request.tool_choice.get("function", {})
                    name = selector.get("name") if isinstance(selector, dict) else None
                    if chat_request.tool_choice.get("type") != "function" or name not in tool_definitions:
                        raise ToolProtocolError("tool_choice references an undeclared function")
                else:
                    raise ToolProtocolError("tool_choice has an invalid format")
        except ToolProtocolError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        # Open WebUI drops arbitrary response fields such as conversation_id.
        # Its configured custom headers preserve chat/user identity on every turn.
        external_conversation_id = _openwebui_conversation_id(http_request)
        owner_id = http_request.state.principal_id
        if chat_request.conversation_id:
            conversation_id = chat_request.conversation_id
            if not _owns_conversation(http_request, conversation_id):
                raise HTTPException(status_code=404, detail="Conversation not found")
        elif external_conversation_id:
            try:
                if http_request.state.is_admin and db.get_conversation(external_conversation_id):
                    pass
                else:
                    db.get_or_create_conversation(external_conversation_id, owner_id=owner_id)
            except PermissionError as exc:
                raise HTTPException(status_code=404, detail="Conversation not found") from exc
            conversation_id = external_conversation_id
        else:
            created = db.create_conversation(owner_id)
            conversation_id = created["id"]
            # Generic OpenAI clients may bootstrap a new conversation with full history.
            for message in chat_request.messages[:-1]:
                if message.role in ("user", "assistant", "tool"):
                    metadata = {}
                    if message.tool_calls is not None:
                        metadata["tool_calls"] = message.tool_calls
                    if message.tool_call_id is not None:
                        metadata["tool_call_id"] = message.tool_call_id
                    if message.name is not None:
                        metadata["name"] = message.name
                    db.append_event(
                        conversation_id, role=message.role,
                        content=_store_content(message.content), producer="client-import",
                        metadata=metadata or None,
                    )

        existing_events = db.list_events(conversation_id)
        if last_message.role == "tool":
            try:
                validate_tool_result(existing_events, last_message.tool_call_id, last_message.name)
            except ToolProtocolError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        else:
            try:
                if pending_tool_call(existing_events) is not None:
                    raise ToolProtocolError("the pending tool call must be answered before a new user turn")
            except (ToolProtocolError, KeyError, TypeError) as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

        response_id = f"chatcmpl-{uuid.uuid4().hex}"
        created_at = int(time.time())
        is_specialist = not tool_definitions and (
            chat_request.task_type != "chat" or _has_images(last_user)
        )
        generation_options = {
            key: value for key, value in {
                "temperature": chat_request.temperature,
                "max_tokens": chat_request.max_tokens,
                "top_p": chat_request.top_p,
                "presence_penalty": chat_request.presence_penalty,
                "frequency_penalty": chat_request.frequency_penalty,
                "stop": chat_request.stop,
            }.items() if value is not None
        }
        if is_specialist and not tool_definitions and generation_options:
            raise HTTPException(
                status_code=400,
                detail="generation options are currently supported on direct chat and tool-call turns",
            )

        stream_worker = None
        if not (is_specialist and not tool_definitions):
            required = (
                {"tool_calling"} if tool_definitions
                else ({"vision"} if _has_images(last_user) else {chat_request.task_type})
            )
            if tool_definitions:
                if chat_request.task_type != "chat":
                    required.add(chat_request.task_type)
                if _has_images(last_user):
                    required.add("vision")
            try:
                stream_worker = router.select_capabilities(required)
            except NoEligibleWorker as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            if (
                chat_request.max_tokens is not None
                and chat_request.max_tokens > stream_worker.max_output_tokens
            ):
                raise HTTPException(
                    status_code=400,
                    detail="max_tokens exceeds the selected worker output limit",
                )

        if chat_request.stream and not is_specialist:
            assert stream_worker is not None
            stream_events = []
            for event in existing_events:
                if event["role"] not in ("user", "assistant", "tool"):
                    continue
                item = {"role": event["role"], "content": _restore_content(event["content"])}
                if event.get("metadata"):
                    item.update(event["metadata"])
                    if event["role"] == "assistant" and event["metadata"].get("tool_calls"):
                        item["content"] = None
                stream_events.append(item)
            try:
                stream_input = {"role": last_message.role, "content": last_user}
                if last_message.role == "tool":
                    stream_input["tool_call_id"] = last_message.tool_call_id
                    if last_message.name:
                        stream_input["name"] = last_message.name
                ensure_context_budget(
                    supplied_system + stream_events + [stream_input],
                    context_window_tokens=stream_worker.context_window_tokens,
                    reserved_output_tokens=(
                        chat_request.max_tokens or stream_worker.max_output_tokens
                    ),
                )
            except ContextBudgetExceeded as exc:
                raise HTTPException(status_code=413, detail=str(exc)) from exc
            stream_gateway = gateway or OpenAICompatibleGateway(
                base_url=stream_worker.base_url,
                model=stream_worker.model,
                api_key=stream_worker.api_key,
                timeout_seconds=stream_worker.timeout_seconds,
            )
            return StreamingResponse(
                stream_direct_turn(
                    conversation_id, last_user, supplied_system, stream_worker,
                    stream_gateway, response_id, created_at, generation_options,
                    tool_definitions or None, chat_request.tool_choice,
                    last_message.role,
                    (
                        {
                            "tool_call_id": last_message.tool_call_id,
                            **({"name": last_message.name} if last_message.name else {}),
                        }
                        if last_message.role == "tool" else None
                    ),
                ),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                    "Connection": "keep-alive",
                },
            )

        input_metadata = None
        if last_message.role == "tool":
            input_metadata = {
                "tool_call_id": last_message.tool_call_id,
                **({"name": last_message.name} if last_message.name else {}),
            }
        input_event, assistant_event, answer, execution_id = await run_turn(
            conversation_id, last_user or "", chat_request.task_type, supplied_system,
            force_orchestration=is_specialist and not tool_definitions,
            input_role=last_message.role, input_metadata=input_metadata,
            tool_definitions=tool_definitions or None, tool_choice=chat_request.tool_choice,
            generation_options=generation_options,
        )

        if chat_request.stream:
            # Specialist execution emits a final-answer SSE stream after the task
            # graph completes. Direct chat uses true upstream token streaming.
            async def event_stream():
                chunks = [
                    {
                        "id": response_id,
                        "object": "chat.completion.chunk",
                        "created": created_at,
                        "model": "CDS_AI",
                        "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
                    },
                    {
                        "id": response_id,
                        "object": "chat.completion.chunk",
                        "created": created_at,
                        "model": "CDS_AI",
                        "choices": [{"index": 0, "delta": {"content": answer}, "finish_reason": None}],
                    },
                    {
                        "id": response_id,
                        "object": "chat.completion.chunk",
                        "created": created_at,
                        "model": "CDS_AI",
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    },
                ]
                for chunk in chunks:
                    yield "data: " + json.dumps(chunk, ensure_ascii=False, separators=(",", ":")) + "\\n\\n"
                yield "data: [DONE]\\n\\n"

            return StreamingResponse(
                event_stream(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        return {
            "id": response_id,
            "object": "chat.completion",
            "created": created_at,
            "model": "CDS_AI",
            "conversation_id": conversation_id,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None if assistant_event.get("metadata") else answer,
                        **(
                            {"tool_calls": assistant_event["metadata"]["tool_calls"]}
                            if assistant_event.get("metadata") else {}
                        ),
                    },
                    "finish_reason": "tool_calls" if assistant_event.get("metadata") else "stop",
                }
            ],
            "x_clicoding": {
                "user_event_id": input_event["id"],
                "assistant_event_id": assistant_event["id"],
                "producer": assistant_event["producer"],
                "system_messages_applied": len(supplied_system),
                "execution_id": execution_id,
                "orchestrated": execution_id is not None,
            },
        }

    @app.post(
        "/api/v1/conversations/{conversation_id}/orchestrated-turns",
        response_model=TurnResponse,
    )
    async def create_orchestrated_turn(
        conversation_id: str, turn_request: TurnRequest, http_request: Request
    ) -> TurnResponse:
        if not _owns_conversation(http_request, conversation_id):
            raise HTTPException(status_code=404, detail="Conversation not found")
        if multi_model is None:
            raise HTTPException(
                status_code=503, detail="Multi-model planning is disabled"
            )
        user_event, assistant_event, answer, execution_id = await run_turn(
            conversation_id, turn_request.content, turn_request.task_type,
            force_orchestration=True,
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
    async def get_execution(
        conversation_id: str, execution_id: str, http_request: Request
    ) -> dict[str, Any]:
        if not _owns_conversation(http_request, conversation_id):
            raise HTTPException(status_code=404, detail="Execution not found")
        execution = db.get_execution(execution_id)
        if execution is None or execution["conversation_id"] != conversation_id:
            raise HTTPException(status_code=404, detail="Execution not found")
        return execution

    @app.post(
        "/api/v1/executions/{execution_id}/tasks/{task_id}/sandbox-approvals",
        status_code=201,
    )
    async def create_sandbox_approval(
        execution_id: str, task_id: str, request: SandboxApprovalCreate,
        http_request: Request,
    ) -> dict[str, Any]:
        if _owns_execution(http_request, execution_id) is None:
            raise HTTPException(status_code=404, detail="Execution not found")
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
        execution_id: str, task_id: str, http_request: Request
    ) -> dict[str, Any]:
        execution = _owns_execution(http_request, execution_id)
        if execution is None:
            raise HTTPException(status_code=404, detail="Execution not found")
        if not any(task["task_id"] == task_id for task in execution["tasks"]):
            raise HTTPException(status_code=404, detail="Execution task not found")
        return {"approvals": db.list_sandbox_approvals(execution_id, task_id)}

    @app.get("/api/v1/sandbox-approvals/{approval_id}")
    async def get_sandbox_approval(
        approval_id: str, http_request: Request
    ) -> dict[str, Any]:
        approval = db.get_sandbox_approval(approval_id)
        if approval is None or _owns_execution(http_request, approval["execution_id"]) is None:
            raise HTTPException(status_code=404, detail="Sandbox approval not found")
        return approval

    @app.post("/api/v1/sandbox-approvals/{approval_id}/execute")
    async def execute_sandbox_approval(
        approval_id: str, http_request: Request
    ) -> dict[str, Any]:
        approval = db.get_sandbox_approval(approval_id)
        if approval is None or _owns_execution(http_request, approval["execution_id"]) is None:
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
    async def get_sandbox_execution_result(
        approval_id: str, http_request: Request
    ) -> dict[str, Any]:
        approval = db.get_sandbox_approval(approval_id)
        if approval is None or _owns_execution(http_request, approval["execution_id"]) is None:
            raise HTTPException(status_code=404, detail="Sandbox approval not found")
        result = db.get_sandbox_execution_result(approval_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Sandbox execution result not found")
        return result

    @app.post("/api/v1/sandbox-approvals/{approval_id}/decision")
    async def decide_sandbox_approval(
        approval_id: str, request: SandboxApprovalDecision, http_request: Request
    ) -> dict[str, Any]:
        current = db.get_sandbox_approval(approval_id)
        if current is None or _owns_execution(http_request, current["execution_id"]) is None:
            raise HTTPException(status_code=404, detail="Sandbox approval not found")
        try:
            approval = db.decide_sandbox_approval(approval_id, request.decision)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if approval is None:
            raise HTTPException(status_code=404, detail="Sandbox approval not found")
        return approval

    return app
