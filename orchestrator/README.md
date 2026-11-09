# CLICoding Orchestrator — conversation and worker-routing slice

This service owns the canonical conversation history and selects internal workers by
capability. The client talks to one stable Orchestrator endpoint; worker IDs are not
client-selectable model names.

## Stack

- Python 3.11+
- FastAPI / Uvicorn
- SQLite for durable conversation events
- httpx for OpenAI-compatible chat-completions
- YAML worker configuration

No LangGraph or LiteLLM dependency is required for this slice.

## Run locally

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
cp .env.example .env
# Configure CLICODING_WORKERS_FILE or the legacy single-upstream variables.
uvicorn clicoding_orchestrator.app:create_app --factory --app-dir src --host 127.0.0.1 --port 8090
```

The service binds to loopback by default. Do not expose it to the LAN before authentication,
TLS, tenant isolation, quotas, and request limits are implemented.

## Worker configuration

Copy `config/workers.example.yaml` to a private local `workers.yaml`, then set
`CLICODING_WORKERS_FILE=/absolute/path/to/workers.yaml` in the service environment.
Replace endpoint and model aliases with values verified in your environment. Workers are
disabled in the example until explicitly enabled by the operator.

Each worker declares an internal ID, OpenAI-compatible base URL, served model alias,
capabilities, enabled state, priority, timeout, and maximum concurrency. The registry
loader only reads configuration; it does not discover, download, install, load, wake,
sleep, or otherwise mutate runtimes. Credentials should come from environment-backed
secret injection rather than being committed in YAML.

The first deterministic routing contract accepts a task hint of `chat`, `coding`,
`review`, or `vision`. It selects the enabled worker with the required capability and
lowest numeric priority (worker ID breaks ties). The hint is a task category, not a model
name. A semantic planner that infers and decomposes complex requests is a later slice.

## API

Create a conversation:

```http
POST /api/v1/conversations
```

Append a turn:

```http
POST /api/v1/conversations/{conversation_id}/turns
Content-Type: application/json

{"content":"Review this code","task_type":"review"}
```

Omit `task_type` for ordinary chat. The same canonical conversation history is assembled
for every selected worker, so a coding worker can continue after a chat worker without a
new conversation.

Retrieve canonical history:

```http
GET /api/v1/conversations/{conversation_id}
```

List worker configuration for operator diagnostics (never returns API keys):

```http
GET /api/v1/workers
```

Health:

```http
GET /health
```

## Current limitations

- The current REST API is an internal development interface, not yet the OpenAI-compatible `/v1/chat/completions` endpoint required by Open WebUI. Do not connect Open WebUI to this slice yet.
- Task type is currently a deterministic API hint. Automatic semantic classification,
  request decomposition, dependency graphs, parallel execution, and synthesis are not
  implemented yet.
- The YAML `max_concurrency` field is registered but not yet enforced by a global scheduler.
- Non-streaming text chat only; image/audio payload transport is not implemented.
- One process is expected. Per-conversation in-process locks do not coordinate multiple
  service processes.
- No authentication, user/tenant isolation, quotas, or LAN exposure.
- Failed upstream requests leave the user event recorded. Idempotency keys and safe client
  retry semantics are still required.
- No tool broker, approval workflow, distributed queue, or resource-aware runtime scheduler.

## Tests

```bash
pip install -e '.[test]'
pytest
```

Tests cover persistence, worker capability selection, disabled-worker exclusion, and
conversation continuity across a chat-to-coding worker handoff.
