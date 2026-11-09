# CLICoding Orchestrator — first vertical slice

This service proves one critical property before multi-model scheduling is added:
**conversation identity and history belong to CLICoding, not to a model runtime.**

## Stack

- Python 3.11+
- FastAPI / Uvicorn
- SQLite for durable conversation events
- httpx for an OpenAI-compatible upstream chat-completions API

The gateway is a replaceable transport adapter. It does not own conversation state.
No LangGraph or LiteLLM dependency is required for this slice.

## Run locally

From the repository root:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
cp .env.example .env
# Edit CLICODING_UPSTREAM_BASE_URL and CLICODING_UPSTREAM_MODEL for your local endpoint.
uvicorn clicoding_orchestrator.app:app --app-dir src --host 127.0.0.1 --port 8090
```

The service binds to loopback by default. Do not expose it to the LAN before authentication,
TLS, tenant isolation, and request limits are implemented.

## API

Create a conversation:

```http
POST /api/v1/conversations
```

Append a user turn and receive the assistant response:

```http
POST /api/v1/conversations/{conversation_id}/turns
Content-Type: application/json

{"content":"Review the authentication flow"}
```

Retrieve the canonical event history:

```http
GET /api/v1/conversations/{conversation_id}
```

Health:

```http
GET /health
```

Every user and assistant event is persisted with an ordered sequence number and producer.
On every turn, the service reconstructs the conversation from SQLite and sends the history
to the configured upstream model. The upstream model can be changed without changing the
conversation ID or stored history.

## Current limitations (intentional)

- Single upstream model configured at process start; dynamic worker selection is the next slice.
- Non-streaming text chat only; streaming and multimodal attachment transport are later slices.
- One process is expected. Per-conversation in-process locks serialize turns; multi-process
  distributed locking is not implemented yet.
- No authentication, user/tenant isolation, quotas, or LAN exposure.
- A failed upstream request leaves the user event recorded; the caller should inspect history
  before manually retrying. Idempotency keys are planned before client retry integration.
- No task planner, dependency graph, tool broker, or parallel task scheduler yet.

## Tests

```bash
pip install -e '.[test]'
pytest
```

The first acceptance test verifies that a second worker invocation receives the previous
user and assistant events, and that producer/model changes do not create a new conversation.
