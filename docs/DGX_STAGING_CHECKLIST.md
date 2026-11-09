# DGX Spark — controlled staging checklist

**Status:** Staging instructions only. This document does not authorize installation, service changes, model changes, or runtime restarts.

## 1. Source revision

Use the canonical repository and the reviewed source revision recorded in `docs/STATUS.md`. Do not transfer a working tree with uncommitted changes.

On a machine with the repository checkout:

```bash
git fetch origin main
git checkout main
git pull --ff-only origin main
git status --short
git rev-parse HEAD
git bundle create CLICoding-main.bundle main
sha256sum CLICoding-main.bundle > CLICoding-main.bundle.sha256
```

Copy both `CLICoding-main.bundle` and `CLICoding-main.bundle.sha256` to DGX through the approved LAN transfer path. On DGX, verify the checksum before importing:

```bash
sha256sum -c CLICoding-main.bundle.sha256
git clone CLICoding-main.bundle CLICoding
cd CLICoding
git checkout main
git rev-parse HEAD
```

If using a direct Git fetch from DGX is permitted, verify the exact commit SHA against the reviewed SHA in the status record instead.

## 2. Read-only DGX preflight

Before creating a virtual environment or changing any service, record:

```bash
uname -a
uname -m
python3 --version
python3 -m pip --version
free -h
df -h
nvidia-smi
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
```

Confirm ARM64 architecture, Python version, disk location for the SQLite database, available host memory, and the actual OpenAI-compatible worker endpoints. Do not infer worker ports or model aliases from old configuration.

## 3. Python dependencies and offline installation

- The package requires Python 3.11 or newer.
- DGX Spark is ARM64; stage wheels for the exact DGX Python minor version and architecture. Do not copy an x86_64 virtual environment or x86_64 wheels.
- Resolve and stage all package/build dependencies from the approved internal package mirror or a compatible ARM64 staging host before offline installation.
- Keep the wheelhouse and virtual environment outside the Git working tree.
- Install with pip in offline mode only after the wheelhouse is verified. Do not allow pip to fall back to public package indexes.
- Run `pytest` on DGX before enabling the service.

## 4. Initial safe runtime configuration

- Start with `CLICODING_ENABLE_PLANNING=false`.
- Use an absolute persistent `CLICODING_DATABASE_PATH`.
- Copy `config/workers.example.yaml` to a private file outside Git and replace every example endpoint/model alias with a verified live endpoint.
- Keep worker concurrency conservative (start at 1); the configured limit is process-local.
- Supply worker credentials through environment variables referenced by `api_key_env`.
- Generate a random inbound secret of at least 32 ASCII characters and set `CLICODING_API_KEY` before any network exposure.
- Keep Uvicorn bound to `127.0.0.1` for the first smoke test. `/health` is public; all other API/diagnostic endpoints require the Bearer key when configured.
- Use one Uvicorn process. Multiple processes do not share conversation locks or worker concurrency semaphores.
- Do not connect Open WebUI yet. Streaming is not implemented and preservation of the returned `conversation_id` has not been verified.

## 5. Smoke-test order

1. Confirm `GET /health`.
2. Confirm `GET /v1/models` requires the configured Bearer key and returns only the public `clicoding` alias.
3. Verify each configured worker endpoint independently with a harmless chat-completions request.
4. Run a single-worker text request with planning disabled.
5. Verify the returned `conversation_id` can be reused and that canonical history persists after process restart.
6. Enable planning only after planning, coding, vision (if configured), and synthesis capabilities have each been validated against their real served model.
7. Test a small two-worker plan and inspect the persisted execution/tasks.
8. Record actual latency, memory use, errors, and worker concurrency before increasing limits.

## 6. Explicit deployment blockers

The current slice is not a production multi-user service. Before LAN/Open WebUI rollout, complete and validate:

- Open WebUI conversation-ID mapping and retry/regenerate behavior.
- Streaming support or a proven client configuration that uses non-streaming requests.
- Actual image payload transport to a vision worker.
- Tenant/user isolation; the current Bearer key is shared by all authorized clients.
- Internal TLS, rate limits, request quotas, cancellation, and safe idempotency.
- Cross-process coordination or a strict single-process service design.
- Live ARM64/DGX dependency and model-endpoint compatibility tests.

Do not mount developer workspaces into the model server. Keep build, test, browser, screenshot, and Git tools on the developer workstation.
