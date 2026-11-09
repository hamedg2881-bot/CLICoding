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
# Configure CLICODING_WORKERS_FILE or the legacy single-upstream variables. Set CLICODING_ENABLE_PLANNING=true to enable the opt-in multi-model orchestration route.
uvicorn clicoding_orchestrator.app:create_app --factory --app-dir src --host 127.0.0.1 --port 8090
```

The example command binds to loopback. The optional `CLICODING_API_KEY` setting enables Bearer authentication on every endpoint except `/health`. Before LAN exposure, configure a long random key and internal TLS. This is shared-key authentication only; user/tenant isolation, quotas, and request-rate limits are not implemented.

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

With planning disabled, deterministic routing accepts a task hint of `chat`, `coding`, `review`, or `vision` and selects the enabled worker with the required capability and lowest numeric priority (worker ID breaks ties). With planning enabled, the Planner generates a validated task graph and the Orchestrator dispatches each task to a worker that supports all requested capabilities.

## OpenAI-compatible client API

The public client surface exposes one alias only:

```http
GET /v1/models
POST /v1/chat/completions
```

The only public model ID is `clicoding`. Internal worker IDs and upstream model aliases
are not returned by `/v1/models`. The chat endpoint currently supports non-streaming,
text-only requests with `system`, `user`, and `assistant` messages.

Example request:

```json
{
  "model": "clicoding",
  "messages": [{"role": "user", "content": "Hello"}],
  "stream": false
}
```

The response includes the standard chat-completion fields plus a CLICoding extension:

```json
{
  "model": "clicoding",
  "conversation_id": "server-generated-id",
  "choices": [{"message": {"role": "assistant", "content": "Hello"}}]
}
```

For reliable canonical history, the client adapter must store the returned
`conversation_id` with its chat and send it back on every subsequent request. If no
conversation ID is supplied, the API creates a new canonical conversation and imports
the supplied prior text turns on that first request. A generic OpenAI client that discards
unknown response fields will not automatically preserve this mapping; client integration
must be explicitly tested before connecting Open WebUI.

The API currently rejects `stream=true`, unknown public model aliases, and non-text
message content. It does not yet support tools/function calls, audio, image payloads,
regenerate/edit semantics, or idempotency keys.

## Internal conversation API

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

## Opt-in multi-model orchestration\n\nSet `CLICODING_ENABLE_PLANNING=true` to enable planner-backed orchestration on the standard `/v1/chat/completions` endpoint and the internal conversation-turn API. It uses a worker declaring the `planning` capability (or falls back to a `chat` worker), validates the generated task graph, persists the execution and tasks before dispatch, and routes each task to a worker that supports all of its declared capabilities. Multiple successful task results are synthesized by a `chat` worker.\n\n```http\nPOST /api/v1/conversations/{conversation_id}/orchestrated-turns\nContent-Type: application/json\n\n{"content":"Review the code and inspect this design"}\n```\n\nInspect durable execution state with:\n\n```http\nGET /api/v1/conversations/{conversation_id}/executions/{execution_id}\n```\n\nWhen enabled, `/v1/chat/completions` uses the same planner-backed orchestration path; when disabled, it retains deterministic single-worker routing. The OpenAI-compatible response still exposes only the `clicoding` assistant identity and includes the canonical `conversation_id`; `x_clicoding.execution_id` identifies the internal execution. This opt-in implementation is a development slice, not yet safe for LAN exposure.\n\n## Current limitations

- The OpenAI-compatible text endpoint is implemented, but Open WebUI integration is not yet validated. Its adapter must persist and resend the returned `conversation_id`; do not assume generic clients preserve custom response fields.
- Task type is currently a deterministic API hint. Automatic semantic classification,
  request decomposition, dependency graphs, parallel execution, and synthesis are not
  implemented yet.
- Worker `max_concurrency` limits are enforced with process-local semaphores across concurrent requests. They are not distributed across multiple service processes.
- Non-streaming text chat only; image/audio payload transport is not implemented. Tool/function calling is also not implemented.
- One process is expected. Per-conversation in-process locks do not coordinate multiple
  service processes.
- Bearer authentication is optional and must be configured with `CLICODING_API_KEY` before LAN exposure. It is one shared key, not user/tenant isolation. TLS, quotas, and request-rate limits are not implemented.
- Failed upstream requests leave the user event recorded. Idempotency keys and safe client
  retry semantics are still required; regenerate/edit/resend behavior is not yet defined.
- No tool broker, approval workflow, distributed queue, or resource-aware runtime scheduler.

## Tests

```bash
pip install -e '.[test]'
pytest
```

Tests cover persistence, worker capability selection, disabled-worker exclusion, and
conversation continuity across a chat-to-coding worker handoff.
