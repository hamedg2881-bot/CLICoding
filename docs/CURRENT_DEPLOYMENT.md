# Current DGX Spark deployment snapshot

**Last host verification:** 2026-11-14  
**Source candidate:** `feature/cds-ai-release-integration`, release commit `f93b65a8e3ba83704b2fc99e7b21c10fd7eec291`  
**Host:** `spark-7ea3`, NVIDIA DGX Spark GB10  
**Evidence:** command output and API tests supplied during the CLICoding project session.

> This is a dated operational snapshot, not a live health check. The values below were verified from user-supplied host output/configuration on the date above. A newer read-only host inventory supersedes this document. Do not describe this snapshot as proof that a service or model is healthy right now.

## 1. Orchestrator service

| Item | Verified value |
|---|---|
| systemd unit | `cds-ai-orchestrator.service` |
| Service account | `cds-ai` |
| Bind address / port | `172.18.0.1:8090` |
| Working directory | `/opt/ai/clicoding/current/source/CDS_AI` |
| Current release path | `/opt/ai/clicoding/releases/f93b65a8e3ba83704b2fc99e7b21c10fd7eec291` |
| Python | 3.12.3, ARM64 / aarch64 |
| Worker registry | `/etc/cds-ai/workers.yaml` |
| Database | `/var/lib/cds-ai/clicoding.db` |
| Attachment storage | `/var/lib/cds-ai/attachments` |

The service health route is `GET /health`; the observed response was `{"status":"ok","component":"conversation-router"}`. The OpenAI-compatible API is exposed under `/v1`, with public assistant model ID `CDS_AI`. The API uses a configured bearer credential; credential values are intentionally excluded from this document.

Observed service environment:
- `CLICODING_ENABLE_PLANNING=false`
- `CLICODING_MAX_PARALLEL_TASKS=1`

These are deployment values, not source-code defaults. The source default for global parallelism is different; do not infer the live setting from the Python default.

## 2. Configured workers

All three registry entries were enabled in the observed host configuration. Each had priority 10 and `max_concurrency: 1`.

| Worker ID | Served model alias | Host-local API | Capabilities | Context | Max output | Timeout |
|---|---|---|---|---:|---:|---:|
| `fast-reasoning` | `gpt-oss-20b` | `http://127.0.0.1:8003/v1` | `chat`, `planning` | 8,192 | 2,048 | 180s |
| `coding-primary` | `qwen-coder` | `http://127.0.0.1:8004/v1` | `coding`, `review` | 8,192 | 2,048 | 240s |
| `multimodal-primary` | `qwen3-vl-2b` | `http://127.0.0.1:8005/v1` | `vision` only | 4,096 | 512 | 180s |

Important distinctions:
- `multimodal-primary` is the registry worker ID; its currently configured capability is **Vision only**, not general chat.
- The live coding alias is `qwen-coder`; do not substitute the target architecture's Qwen3-Coder-Next name without a new runtime verification.
- The live Vision alias is `qwen3-vl-2b`; this is an experimental small Vision worker, not the target Qwen3.6 general/multimodal model.
- No worker in this snapshot is configured with the `tool_calling` capability.

## 3. Client connection

Open WebUI was configured as follows:
- UI host port: `3000`
- Connection name: `CLICoding`
- OpenAI-compatible base URL: `http://172.18.0.1:8090/v1`
- Model shown to the client: `CDS_AI`

Open WebUI Function Calling / Native Tool Calling / Tools were disabled after the client reported `tools must contain between 1 and 32 function definitions`. This issue was not verified as fixed.

## 4. Streaming and response-quality observations

- A direct API streaming test returned HTTP 200 with `text/event-stream`; the first SSE event arrived at about 0.07s, but that event contained only the assistant role, not text.
- A separate direct request completed in about 0.72s; a separate streaming test took about 2.89s total. These were not the same workload as the long architecture prompt.
- The first non-empty content-delta latency has not yet been measured.
- The user observed approximately 20 seconds before the first visible text for a long architecture question in Open WebUI. The answer was generic and included unsupported infrastructure claims. This is a user-observed result, not a measured root cause.
- Do not equate time-to-first-SSE-event with time-to-first-visible-text. Do not attribute the delay to context compaction without measuring it.

## 5. Confirmed implementation and deployment checks

At the recorded release:
- The production Python environment was installed from the staged wheelhouse; package consistency and import checks passed.
- The full source test suite had previously passed: 296 tests.
- The Orchestrator API exposed `/v1/models` and `/v1/chat/completions`; the configured public model was `CDS_AI`.
- The three worker entries and their capability assignments were visible through the deployed registry.

These checks do not establish current health after 2026-11-14, sustained concurrent-load safety, or full product acceptance.

## 6. Open verification gates

- Re-run a read-only inventory of systemd, worker registry, API readiness and actual served model aliases before treating this snapshot as current.
- Measure time to first **non-empty content delta** for the same long prompt through direct API and Open WebUI.
- Record prompt/context token estimates, compaction events, selected worker, upstream request timing and output-token counts without logging secrets or full sensitive prompts.
- Evaluate Persian/English architecture answers against repository facts and reject unsupported infrastructure claims.
- Verify Open WebUI conversation continuity, cancellation, and the exact streaming behavior.
- Resolve and retest the Open WebUI tool-definition error before enabling client tool calling.
- Validate real Vision image quality and language adherence against the exact `qwen3-vl-2b` runtime.
- Verify TLS/LAN exposure, multi-user behavior and deployment rollback separately.

## 7. Target architecture versus current deployment

The target topology in [THREE_MODEL_TOPOLOGY.md](THREE_MODEL_TOPOLOGY.md) remains a design target: GPT-OSS-20B, Qwen3-Coder-Next and Qwen3.6-35B-A3B, with all three intended to remain resident concurrently. The live worker configuration recorded above is different: GPT-OSS-20B, `qwen-coder`, and the Vision-only `qwen3-vl-2b`; global parallelism is configured to 1. Do not present the target topology as the currently deployed model set.
