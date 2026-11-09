from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .config import Settings
from .database import Database
from .gateway import GatewayError, ModelGateway, OpenAICompatibleGateway
from .registry import RegistryError, Worker, WorkerRegistry
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
    content: str = Field(min_length=1, max_length=200_000)


class OpenAIChatRequest(BaseModel):
    model: str = "clicoding"
    messages: list[ChatMessage] = Field(min_length=1, max_length=500)
    conversation_id: str | None = None
    task_type: Literal["chat", "coding", "review", "vision"] = "chat"
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = Field(default=None, gt=0)
    user: str | None = None


def _chat_message(role: str, content: str) -> dict[str, str]:
    return {"role": role, "content": content}


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
    multi_model = orchestrator or (MultiModelOrchestrator(db, router) if config.enable_planning else None)
    conversation_locks: dict[str, asyncio.Lock] = {}

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
        return conversation

    async def run_turn(
        conversation_id: str,
        content: str,
        task_type: str,
        system_messages: list[dict[str, str]] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any], str]:
        if not db.conversation_exists(conversation_id):
            raise HTTPException(status_code=404, detail="Conversation not found")
        lock = conversation_locks.setdefault(conversation_id, asyncio.Lock())
        async with lock:
            user_event = db.append_event(
                conversation_id, role="user", content=content, producer="user"
            )
            history = list(system_messages or []) + [
                {"role": event["role"], "content": event["content"]}
                for event in db.list_events(conversation_id)
                if event["role"] in ("user", "assistant")
            ]
            try:
                worker = router.select(task_type)
            except NoEligibleWorker as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc

            selected_gateway = gateway or OpenAICompatibleGateway(
                base_url=worker.base_url,
                model=worker.model,
                api_key=worker.api_key,
                timeout_seconds=worker.timeout_seconds,
            )
            try:
                completion = await selected_gateway.complete(history)
            except GatewayError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc
            except Exception as exc:
                raise HTTPException(
                    status_code=502, detail="Unexpected upstream model failure"
                ) from exc

            assistant_event = db.append_event(
                conversation_id,
                role="assistant",
                content=completion.content,
                producer=worker.id,
            )
            return user_event, assistant_event, completion.content

    @app.post(
        "/api/v1/conversations/{conversation_id}/turns",
        response_model=TurnResponse,
    )
    async def create_turn(conversation_id: str, request: TurnRequest) -> TurnResponse:
        user_event, assistant_event, answer = await run_turn(
            conversation_id, request.content, request.task_type
        )
        return TurnResponse(
            conversation_id=conversation_id,
            user_event_id=user_event["id"],
            assistant_event_id=assistant_event["id"],
            assistant_content=answer,
            producer=assistant_event["producer"],
        )

    @app.post("/v1/chat/completions")
    async def openai_chat_completions(request: OpenAIChatRequest) -> dict[str, Any]:
        if request.model not in ("clicoding", "assistant", "auto"):
            raise HTTPException(status_code=404, detail="Unknown public model alias")
        if request.stream:
            raise HTTPException(
                status_code=400,
                detail="Streaming is not supported yet; set stream=false",
            )
        if not request.messages or request.messages[-1].role != "user":
            raise HTTPException(
                status_code=400,
                detail="The final message must be a user message",
            )

        last_user = request.messages[-1].content
        supplied_system = [
            _chat_message(message.role, message.content)
            for message in request.messages
            if message.role == "system"
        ]

        if request.conversation_id:
            conversation_id = request.conversation_id
            if not db.conversation_exists(conversation_id):
                raise HTTPException(status_code=404, detail="Conversation not found")
        else:
            created = db.create_conversation()
            conversation_id = created["id"]
            # On the first call, import the client-provided prior turns so a client
            # that sends full history can bootstrap continuity. The final user turn
            # is appended by run_turn below.
            for message in request.messages[:-1]:
                if message.role in ("user", "assistant"):
                    db.append_event(
                        conversation_id,
                        role=message.role,
                        content=message.content,
                        producer="client-import",
                    )

        user_event, assistant_event, answer = await run_turn(
            conversation_id, last_user, request.task_type, supplied_system
        )

        response_id = f"chatcmpl-{uuid.uuid4().hex}"
        return {
            "id": response_id,
            "object": "chat.completion",
            "created": int(time.time()),
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
            },
        }


    @app.post("/api/v1/conversations/{conversation_id}/orchestrated-turns", response_model=TurnResponse)
    async def create_orchestrated_turn(conversation_id: str, request: TurnRequest) -> TurnResponse:
        if multi_model is None:
            raise HTTPException(status_code=503, detail="Multi-model planning is disabled")
        if not db.conversation_exists(conversation_id):
            raise HTTPException(status_code=404, detail="Conversation not found")
        lock = conversation_locks.setdefault(conversation_id, asyncio.Lock())
        async with lock:
            user_event = db.append_event(conversation_id, role="user", content=request.content, producer="user")
            context = "\n\n".join(
                f"{event['role']}: {event['content']}"
                for event in db.list_events(conversation_id)
                if event["role"] in ("user", "assistant")
            )
            try:
                result = await multi_model.run(conversation_id, request.content, context)
            except NoEligibleWorker as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            except Exception as exc:
                raise HTTPException(status_code=502, detail="Multi-model orchestration failed") from exc
            assistant_event = db.append_event(conversation_id, role="assistant", content=result.answer, producer="orchestrator")
            return TurnResponse(
                conversation_id=conversation_id,
                user_event_id=user_event["id"],
                assistant_event_id=assistant_event["id"],
                assistant_content=result.answer,
                producer="orchestrator",
                execution_id=result.execution_id,
            )

    return app
