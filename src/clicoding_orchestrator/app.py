from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .config import Settings
from .database import Database
from .gateway import GatewayError, ModelGateway, OpenAICompatibleGateway


class TurnRequest(BaseModel):
    content: str = Field(min_length=1, max_length=200_000)


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
) -> FastAPI:
    config = settings or Settings.from_env()
    db = database or Database(config.database_path)
    model_gateway = gateway or OpenAICompatibleGateway(
        base_url=config.upstream_base_url,
        model=config.upstream_model,
        api_key=config.upstream_api_key,
        timeout_seconds=config.request_timeout_seconds,
    )
    conversation_locks: dict[str, asyncio.Lock] = {}

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield

    app = FastAPI(
        title="CLICoding Orchestrator",
        version="0.1.0",
        description=(
            "First vertical slice: persistent, model-independent conversations "
            "with an OpenAI-compatible upstream model."
        ),
        lifespan=lifespan,
    )
    app.state.database = db
    app.state.gateway = model_gateway

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "component": "conversation-core"}

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

        # Serialize turns within this process so two messages cannot interleave
        # their history while the first model request is still running.
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
                completion = await model_gateway.complete(history)
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
                producer=completion.model,
            )
            return TurnResponse(
                conversation_id=conversation_id,
                user_event_id=user_event["id"],
                assistant_event_id=assistant_event["id"],
                assistant_content=completion.content,
                producer=completion.model,
            )

    return app


