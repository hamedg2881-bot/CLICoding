# CDS_AI Orchestrator

This service is the single public conversation gateway for **CDS_AI**. Open WebUI and workstation/IDE clients connect to the Orchestrator; model workers remain private behind it.

## Current architecture

- **Public identity:** `CDS_AI`. Internal worker IDs and served model aliases are never exposed through `/v1/models`.
- **Conversation-first routing:** ordinary chat goes directly to a chat-capable worker and does not invoke Planner. Explicit coding/review/vision or forced multi-step work uses the specialist orchestrator.
- **Streaming:** direct chat and OpenAI function-tool deltas stream from upstream SSE. Specialist execution emits structured plan/task lifecycle progress as `event: progress` SSE metadata (`x_clicoding.progress`) and then returns a buffered final answer; it does not stream specialist tokens.
- **History:** SQLite-backed conversations, tool-call metadata, summaries, retry idempotency, latest-turn regenerate, guarded edit/resubmit and active-turn cancellation.
- **Context:** per-worker admission/output limits and versioned model-assisted compaction. Token estimation is conservative and not tokenizer-specific; live model limits must be verified before increasing context sizes.
- **Tools:** typed OpenAI tool-call round-trip. The workstation-side Python reference adapter owns workspace tools, approval and execution. The DGX Orchestrator does not mount developer workspaces and exposes no arbitrary shell tool.
- **Images:** conversation-scoped PNG/JPEG/GIF/WebP attachments with bounded upload, signature checks, SHA-256 integrity and owner-scoped retrieval/deletion.
- **Security:** per-user bearer identities, durable token issue/list/revoke, per-principal SQLite rate limits, request IDs and administrator-only audit export. Trusted identity headers are safe only behind a private proxy that strips/reinjects them.
- **Storage:** SQLite event sequence allocation, durable cross-process conversation-turn leases, lease renewal/expiry recovery and persisted cancellation are implemented. Keep one Uvicorn worker until live supervisor/multi-process acceptance is completed.

## Install and run locally

Python 3.11+ is required.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
cp .env.example .env
# Edit .env and configure a strong CLICODING_API_KEY before any LAN exposure.
uvicorn clicoding_orchestrator.app:create_app --factory --app-dir src --host 127.0.0.1 --port 8090 --workers 1
```

For a LAN deployment, bind only to the required private interface, use internal TLS or a trusted reverse proxy, and configure a strong bootstrap admin credential. Never expose model-worker ports directly.

## Authentication and operations

- `CLICODING_API_KEY`: bootstrap shared/admin bearer key. Keep it in the service secret environment, not in Git.
- `CLICODING_USER_API_KEYS`: optional JSON object mapping stable user IDs to unique bearer tokens. The reserved user ID `admin` is administrative.
- `CLICODING_TRUST_USER_HEADER=true`: optional Open WebUI identity-header mode. Enable only if a private trusted proxy prevents users from supplying or changing `X-CLICoding-User-ID` themselves. Otherwise issue per-user tokens.
- `CLICODING_RATE_LIMIT_PER_WINDOW` and `CLICODING_RATE_LIMIT_WINDOW_SECONDS`: fixed-window per-principal request limit; defaults are 120 requests per 60 seconds.
- `CLICODING_ATTACHMENT_STORAGE_DIR` and `CLICODING_MAX_ATTACHMENT_BYTES`: private image storage and upload bound.
- `/health` is public for local monitoring. All other endpoints require authentication once a bootstrap or issued token exists. Authentication remains enabled after issued tokens are revoked.
- Administrator token endpoints: `POST/GET /api/v1/admin/tokens`, `DELETE /api/v1/admin/tokens/{token_id}`. The plaintext token is returned only once; only its SHA-256 hash is stored.
- Administrator audit export: `GET /api/v1/admin/audit?limit=100`. It records method, route, principal, status, request ID and timestamp, not request bodies, prompts or bearer tokens.

## Public OpenAI-compatible API

- `GET /v1/models` — returns only the public model `CDS_AI`.
- `POST /v1/chat/completions` — chat, multimodal image references, direct streaming and validated tool-call messages.
- `X-CLICoding-Chat-ID` and `X-CLICoding-User-ID` — optional Open WebUI custom headers for stable per-user chat mapping when trusted-header mode is correctly secured.
- The returned `conversation_id` should be persisted and sent on subsequent requests by generic OpenAI clients.

Example:

```json
{
  "model": "CDS_AI",
  "messages": [{"role": "user", "content": "Hello"}],
  "stream": true
}
```

Do not configure Open WebUI to connect directly to a worker. It must connect to the Orchestrator's `/v1` endpoint and expose only `CDS_AI`.

## Conversation and attachment API

- `POST /api/v1/conversations`
- `GET /api/v1/conversations/{conversation_id}`
- `POST /api/v1/conversations/{conversation_id}/cancel`
- `POST /api/v1/conversations/{conversation_id}/attachments`
- `GET /api/v1/conversations/{conversation_id}/attachments/{attachment_id}`
- `DELETE /api/v1/conversations/{conversation_id}/attachments/{attachment_id}`

All conversation and attachment operations enforce the authenticated owner scope. Legacy conversations remain in the `shared` namespace until explicitly migrated.

## Workstation and IDE tools

Install the project on the developer workstation and use `cds_ai_ide_adapter.CDSAIClient` with a `WorkspaceToolHost` bound to one explicitly selected workspace. File writes and .NET build/test require approval; Git status/diff are fixed read-only commands. No general-purpose shell tool is exposed. Tool results are redacted for common credential-shaped values, but this is defense in depth and not a guarantee that every secret format is detected.

The adapter is currently a Python reference library, not a packaged VS Code, Visual Studio, Rider or JetBrains extension. Its .NET build/test tools still execute on the workstation host after approval and are not OS-sandboxed. The existing privileged Docker runner isolates approved single-file snippets only; multi-file workspace snapshot build/test integration must extend that same broker/runner before release. Browser automation, Playwright screenshots, visual correction and cross-platform process confinement remain release gates.

## Backup and restore

Install the package to obtain the `cds-ai-backup` command. See [docs/BACKUP_RESTORE.md](../docs/BACKUP_RESTORE.md) for verified SQLite/attachment backup, integrity checking, restore and rollback procedures. Stop CDS_AI before restore. Backups contain sensitive conversation data and must be encrypted at rest by the operator. The proposed systemd unit and controlled offline install/upgrade/rollback procedure are documented in [deploy/README-orchestrator-service.md](../deploy/README-orchestrator-service.md).

## Validation and release status

Run tests with:

```bash
pip install -e '.[test]'
pytest
```

GitHub Actions validates source-level behavior only. Live DGX model aliases, tool-call parser compatibility, vision quality, context limits, latency, concurrency and unified-memory headroom must be tested on the actual DGX runtimes before deployment. The integration candidate is source-only; do not transfer or restart services until all source gates are reviewed and the user explicitly approves deployment.
