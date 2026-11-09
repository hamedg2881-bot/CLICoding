from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .config import Settings
from .database import Database
from .gateway import GatewayError, ModelGateway, OpenAICompatibleGateway
from .registry import RegistryError, Worker, WorkerRegistry
from .router import CapabilityRouter, NoEligibleWorker


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


def create_app(
    settings: Settings | None = None,
    gateway: ModelGateway | None = None,
    database: Database | None = None,
    registry: WorkerRegistry | None = None,
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
    conversation_locks: dict[str, asyncio.Lock] = {}

    app = FastAPI(
        title="CLICoding Orchestrator",
        version="0.2.0",
        description=(
            "Persistent conversations with configuration-backed, capability-based "
            "worker selection."
        ),
    )
    app.state.database = db
    app.state.registry = worker_registry
    app.state.router = router

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "component": "conversation-router"}

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

    @app.post(
        "/api/v1/conversations/{conversation_id}/turns",
        response_model=TurnResponse,
    )
    async def create_turn(conversation_id: str, request: TurnRequest) -> TurnResponse:
        if not db.conversation_exists(conversation_id):
            raise HTTPException(status_code=404, detail="Conversation not found")

        lock = conversation_locks.setdefault(conversation_id, asyncio.Lock())
        async with lock:
            user_event = db.append_event(
                conversation_id, role="user", content=request.content, producer="user"
            )
            history = [
                {"role": event["role"], "content": event["content"]}
                for event in db.list_events(conversation_id)
                if event["role"] in ("user", "assistant")
            ]

            try:
                worker = router.select(request.task_type)
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
            return TurnResponse(
                conversation_id=conversation_id,
                user_event_id=user_event["id"],
                assistant_event_id=assistant_event["id"],
                assistant_content=completion.content,
                producer=worker.id,
            )

    return app
