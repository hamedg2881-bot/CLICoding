## 2026-11-14 — First-party CDS_AI chat interface

- Added a first-party browser chat interface at `/`, packaged with the Python distribution and served by FastAPI.
- The UI uses the existing authenticated conversation and OpenAI-compatible streaming APIs; it does not connect to internal worker endpoints.
- Implemented RTL-first Persian layout, task-type selection, independent session-scoped chat list, server-backed history reload, direct token streaming, specialist progress rendering, and Stop through the existing conversation cancellation endpoint.
- The progress panel maps only safe lifecycle event types to user-facing status labels; it does not display internal worker IDs, task prompts, model reasoning or raw worker output.
- Added a route-level UI smoke test. Browser/Playwright acceptance, mobile/keyboard accessibility, cancellation race testing, and Open WebUI custom-progress rendering remain open.
- No DGX files, services, runtimes or containers were changed. No ZIP transfer or service restart occurred.

## 2026-11-11 — Offline backup and restore

- Added the packaged cds-ai-backup CLI and a source-tree wrapper. The utility uses SQLite's online backup API, includes only attachment files referenced by the database snapshot, verifies SQLite integrity and SHA-256/size metadata, and writes a versioned manifest.
- Restore verifies the backup before staging, refuses overwrite unless explicitly requested, retains pre-restore database/attachment paths, checks staged files and attempts rollback if installation fails.
- Added tests for backup/verify/restore, tamper detection, unreferenced payload rejection and overwrite protection.
- The utility does not encrypt backups, schedule jobs, include .env/secrets, or stop/restart services. Store backups encrypted at rest and stop CDS_AI before restore.
- Automatic attachment expiry, malware scanning and content decoding remain open.
- No DGX transfer or service/runtime restart has occurred.

## 2026-11-11 — API security operations hardening

- Added a durable SQLite token registry with cryptographically random user tokens, SHA-256 hashes at rest, one-time plaintext disclosure, administrator-only listing and revocation.
- Dynamic tokens activate authentication even when no environment-mapped user token exists; revoked tokens are rejected on the next request.
- Added per-principal fixed-window rate limiting using SQLite transactions with an immediate write lock, so concurrent application processes share the same admission counter.
- Added request correlation IDs and an administrator-only paginated audit endpoint. Audit records contain principal, route, method, status and timestamp only; request bodies, prompts, bearer tokens and credentials are not recorded.
- Added regression tests for token lifecycle, privilege separation, revocation, rate-limit isolation and audit redaction.
- Workstation tool results are recursively redacted for common credential-shaped values before they are returned to the model/client or persisted in the idempotency ledger.
- SQLite event sequence allocation now uses an immediate write transaction and is tested across independent Database instances under concurrent appends.
- Durable SQLite-backed cross-process turn leases, renewal, expiry recovery and persisted cancellation are implemented; independent-process and two-app cancellation tests pass CI. Keep production single-worker until live supervisor acceptance.
- Configuration: CLICODING_RATE_LIMIT_PER_WINDOW (default 120) and CLICODING_RATE_LIMIT_WINDOW_SECONDS (default 60).
- This is source-level security hardening; production key bootstrap, TLS/proxy configuration, audit retention and operational alerting remain deployment gates.
- No DGX transfer or service/runtime restart has occurred.

# Project Status

**Last updated:** 2026-11-14  
**Product / public assistant ID:** CDS_AI  
**Canonical source repository:** https://github.com/hamedg2881-bot/CLICoding  
**Current candidate:** `feature/cds-ai-release-integration`, stacked on PR #13  
**Overall state:** The integration candidate combines the conversation-first CDS_AI core, shared assistant policy, regenerate/edit lineage, cross-process turn leases/cancellation, safe versioned context compaction, specialist/direct streaming fixes, per-user isolation, workstation tools and conservative context budgeting. The last pre-UI GitHub CI run passed 167 tests; the first-party chat UI slice is awaiting its own CI verification. No transfer to DGX or service restart has been performed.

## Current implementation snapshot — 2026-11-14
- Product identity: `CDS_AI`; legacy CLICoding service/database/environment identifiers remain for compatibility.
- Conversation lifecycle: persistent history, exact retry idempotency, latest-turn regenerate and guarded edit/resubmit operations are present in PR #13; the integration candidate adds an authenticated active-turn cancellation endpoint.
- Context lifecycle: direct chat and tool-call turns use model-assisted compaction with versioned summaries. Original events remain intact; recent three user turns are retained; summaries are invalidated when rewritten history intersects their coverage. Specialist planning, each task worker and final synthesis now receive independently budgeted context summaries when needed; canonical history is not rewritten.
- Streaming: direct chat and tool-call deltas stream; specialist requests emit structured plan/task lifecycle progress during execution, followed by the buffered final answer. Progress is carried as `x_clicoding.progress` metadata in `event: progress` SSE frames; no internal reasoning or raw worker output is exposed.
- First-party UI: `/` serves a packaged RTL-aware CDS_AI chat interface with session-scoped independent conversations, history reload, task-type selection, live progress rendering, direct token display and Stop wired to the authenticated cancellation API. Browser/Playwright acceptance remains open; Open WebUI custom progress rendering is not yet verified.
- Workstation execution: approved, allow-listed reference tool host; no arbitrary shell tool.
- Worker example uses recorded ports 8003/8004/8005 and leaves all workers disabled. Served aliases and tool-call capability require live verification.
- Attachments: conversation-scoped PNG/JPEG/GIF/WebP upload, bounded size, MIME sniffing, SHA-256 integrity, private local storage, owner-scoped fetch/delete, and attachment-reference resolution into model vision payloads. Automatic expiry/retention and storage backup/restore policy remain open.
- Remaining source gates include packaged IDE UI, browser/screenshot visual validation, OS-level process sandboxing, audit retention/alerting and cross-platform acceptance. Live multi-process supervisor acceptance remains a deployment gate.
- No DGX host files, services, model runtimes or containers have been changed.

## Confirmed requirements
- Internet is unavailable during development; internal LAN is available.
- IDE choice is open.
- Central LAN model serving with workstation-side agent/tools.
- Full software workflow: discovery, planning, approval, implementation, build/test, review, delivery.
- Manual screenshot attachment and automated Playwright screenshot capture.
- Vision model must receive actual image content.
- UI correction is iterative and validated by new screenshots.
- Local/internal dependencies only during runtime.
- GitHub repository is the canonical record; no secrets in Git.

## Infrastructure decision and evidence status
- **User-confirmed target AI server:** NVIDIA DGX Spark.
- Confirmed DGX Spark platform: GB10, approximately 121 GiB usable unified memory, NVIDIA driver 580.178.04, CUDA 13.0.
- A live three-runtime experiment is now recorded in `docs/DGX_LIVE_VALIDATION_2026-11-10.md`, including the verified model endpoints, runtime versions, conservative context caps, and latest memory snapshot.
- Host OS/kernel, CPU details, dedicated/shared allocation policy, LAN/DNS/TLS configuration, and sustained peak-memory behavior remain to be verified.
- Repository documentation updates do not modify the DGX host. The currently running model experiments were separately user-authorized; no model files, containers, or images were deleted.

## Phase 0 checklist
- [x] README project charter.
- [x] Architecture baseline.
- [x] Ordered roadmap and phase exit gates.
- [x] End-to-end workflow.
- [x] Architecture decision log.
- [x] DGX Spark selected as target AI server.
- [ ] Verify DGX Spark hardware/OS/runtime and project allocation.
- [ ] Confirm workstation OS mix.
- [ ] Confirm expected number of concurrent developers.
- [ ] Select first sample .NET/web project for acceptance testing.

## Phase 1 — next inventory actions
Collect a read-only infrastructure inventory before selecting runtime/model:
1. Server CPU, RAM, GPU/accelerator, unified memory, storage, OS, driver/runtime.
2. Whether server is dedicated or shared with other workloads.
3. Workstation OS and available local resources.
4. Approximate concurrent developer count.
5. Existing internal Git, DNS, TLS/CA, NuGet feed, and artifact storage.
6. Candidate models already available locally and their exact paths/quantization/context.
7. Desired response latency and context size.

Then run controlled evaluations for repository understanding, C# edits, tool calling, image input, latency, and concurrent requests. No model/runtime is selected as final until these tests are recorded.

## Implementation log
### 2026-11-09 — Project kickoff
- Inspected repository metadata; repository was empty.
- Confirmed main as default branch and write permission.
- Established project charter, architecture, roadmap, workflow, and decisions.
- Created [Issue #1](https://github.com/hamedg2881-bot/CLICoding/issues/1) for Phase 1 inventory and model capability validation.
- No infrastructure or source-code changes were made.

### 2026-11-09 — Target AI server selected
- User confirmed DGX Spark as the target AI server for CLICoding.
- Historical DGX Spark details are recorded as leads only; fresh read-only verification is still required.
- No server-side changes were made.


### 2026-11-09 — Concurrent model residency direction
- User clarified that the desired architecture keeps required models resident simultaneously; frequent model sleep/load switching is not the target design.
- User prefers MoE models and does not want Qwen3.8-27B Dense as a preferred option because of its expected low decode speed.
- Preliminary pair to validate: Qwen3-Coder-Next 80B-A3B NVFP4 (coding/tool use) + Qwen3.6-35B-A3B NVFP4 (vision/general).
- Nemotron 3.5 Lightning 30B-A3B NVFP4 remains a text-only agentic challenger, not a Vision replacement.
- Published starting points: Coder-Next GB10 recipe reports ~43.22 GiB resident weights and uses an 8 GiB KV budget in its single-user full-context recipe; Qwen3.6 official vLLM recipe lists 21 GB minimum VRAM for NVFP4 on GB10; Nemotron 3.5 Lightning requires roughly 30 GB before KV headroom.
- These figures are not additive guarantees or proof of concurrent fit. Runtime allocations, KV cache, CUDA graphs, activations, system services, and current workloads must be measured.
- No model was downloaded, deleted, restarted, or reconfigured.
- Next action: collect a fresh read-only memory/process/container snapshot and exact local model directory sizes before any deployment experiment.


### 2026-11-09 — DGX Spark live read-only snapshot
User supplied a fresh command output from host `spark-7ea3`:
- RAM: 121 GiB total, 106 GiB used, 12 GiB free, 15 GiB available.
- Swap: 16 GiB configured, 2.3 GiB used.
- GB10 driver: 580.178.04; CUDA 13.0; GPU utilization 0% at capture.
- `nvidia-smi` reports `VLLM::EngineCore` PID 993248 using 97,383 MiB unified/GPU memory.
- Active containers: `qwen38-flash` (eugr/spark-vllm-b12x:nightly-20261015), `open-webui` v0.13.0 healthy, and `open-terminal`.
- Docker cgroup stats show qwen38-flash 4.945 GiB, Open WebUI 649 MiB, Open Terminal 25.64 MiB; Docker memory stats do not represent the GB10 allocation reported by nvidia-smi.
- Local model directory sizes: qwen3-vl-8b-vllm 17G; Qwen3.8-27B-NVFP4 21G; Qwen3.6-VL-35B-A3B-NVFP4-DGX-Spark-VisionSafe 25G; Qwen3-Coder-Next-NVFP4-GB10 43G; Qwen3.8-Flash-Next-FP4-Local 96G.
- Current free/available memory is not sufficient to load another large model alongside the running Flash runtime. No model was stopped, downloaded, deleted, or reconfigured.
- Initial disk-size sum for the proposed Coder-Next + Qwen3.6 pair is about 68G, but this is not a measured concurrent runtime footprint. Next step is read-only inspection of the running container's launch args, model paths, and each candidate's config before any controlled runtime experiment.

## 2026-11-09 — Conversation Router and Orchestrator contract accepted
- User confirmed that complex requests may require multiple models and that seamless model changes and model-to-model integration are among the highest priorities.
- Accepted a central, model-independent Task Orchestrator. It owns plans, task decomposition, dependencies, sequential/parallel/hybrid execution, worker dispatch, validation, retries, cancellation, approval gates, and final synthesis.
- Accepted a canonical Conversation Manager: stable conversation identity and shared event history independent of model; task-specific context assembly preserves relevant turns, summaries, attachments, tool evidence, constraints, and dependency outputs.
- Accepted capability- and resource-aware routing. The system must not use multiple models unnecessarily or exceed measured resource budgets; queue instead of interrupting active inference.
- Accepted a unified user experience: one assistant identity, one conversation, and one coherent response stream. Model handoffs are transparent by default.
- LiteLLM remains a candidate gateway only; it is not the orchestrator or conversation-memory owner. LangGraph is not mandatory.
- Added [docs/ORCHESTRATOR_SPEC.md](ORCHESTRATOR_SPEC.md) as the first implementation contract, including logical data contracts, execution semantics, safety, resource policy, implementation slices, and ten acceptance tests.
- Current work is architecture/specification and repository documentation only. No host services, model runtimes, containers, model files, or user workspaces were changed. No software has been installed and no server service was restarted.
- Next step: choose the smallest persistence/gateway/orchestration stack against the contract, then implement and test the first single-model continuity slice before enabling multi-model execution.

### 2026-11-09 — Single client endpoint rule
- Accepted that Open WebUI or another reference client must connect only to the CLICoding Orchestrator endpoint and see one assistant alias. Individual model runtimes remain internal workers; LiteLLM, if adopted, is downstream of the Orchestrator.
- Rationale: avoid model-specific sessions and ensure the Orchestrator—not the UI model picker—owns routing and multi-model workflows.

### 2026-11-09 — First Orchestrator source slice committed
- Added a Python 3.11+ FastAPI service under `src/clicoding_orchestrator`.
- Added SQLite persistence for conversations and ordered user/assistant/tool events, with producer metadata.
- Added a replaceable OpenAI-compatible upstream adapter using httpx; the gateway transports requests but does not own conversation state.
- Added API routes to create a conversation, append a turn, retrieve its full history, and check health.
- Added a test proving that a later model invocation receives the earlier user and assistant messages while retaining the same conversation ID; the fake worker changes its producer ID between calls.
- Added local run instructions, environment example, and package metadata.
- Three first-slice tests are committed: conversation continuity across worker/producer changes, unknown conversation handling, and persistence after reopening the SQLite database. An isolated local verification harness ran the equivalent test scenarios successfully (3 passed). The environment could not clone GitHub to execute the committed checkout directly, so rerun `pytest` from a repository checkout before declaring the commit-level test run complete.
- Known limits: one configured upstream model per process, non-streaming text-only API, single-process turn locks, no idempotency keys, no authentication/tenant isolation, no planner/task graph, and no tool broker.
- No DGX Spark host, model runtime, container, service, or workstation workspace was changed. No package was installed on the user's machines.
- Next: rerun the committed tests from a repository checkout; then add a server-side worker registry/selector and prove real upstream model handoff without changing conversation identity.


### 2026-11-09 — Capability-based worker registry and handoff
- Added a YAML-backed worker registry with internal worker ID, OpenAI-compatible endpoint, served model alias, capabilities, enabled state, priority, timeout, and declared concurrency limit.
- Added environment-variable lookup for worker API keys; credentials are not stored in the example YAML and are omitted from registry diagnostics.
- Added deterministic routing for task hints `chat`, `coding`, `review`, and `vision`. The router only selects enabled workers that declare the requested capability; lower numeric priority wins and worker ID breaks ties.
- Connected routing to the persistent conversation turn API. The same canonical history is passed to the selected worker after a handoff, while the persisted producer identifies the internal worker.
- Added `GET /api/v1/workers` for operator diagnostics and `CLICODING_WORKERS_FILE` configuration. Legacy single-upstream mode remains available when no YAML file is configured.
- Added tests for capability/priority selection, disabled-worker exclusion, YAML validation, environment-backed credentials, and chat-to-coding handoff continuity. These tests are committed but have not yet been executed from a direct repository checkout in this environment.
- Important limitations: task type is currently a deterministic request hint, not semantic intent detection; `max_concurrency` is metadata only and not yet enforced; vision capability selection exists but actual image payload transport does not; streaming, multi-process locking, idempotency, planner/task graph, parallel execution, tool broker, authentication, and resource-aware scheduling remain future work.
- No DGX Spark configuration, model, container, runtime, service, or workstation was changed. No deployment or package installation was performed.


### 2026-11-09 — OpenAI-compatible client API
- Added `GET /v1/models` with exactly one public model alias, `clicoding`; internal worker IDs and upstream model aliases are not exposed as client model choices.
- Added non-streaming, text-only `POST /v1/chat/completions` using the standard chat-completion response shape plus a `conversation_id` extension.
- Added explicit continuation: clients send the returned `conversation_id` on subsequent requests; the Orchestrator then loads its canonical event history and routes to the selected worker.
- When no conversation ID is supplied, a new canonical conversation is created and the request's prior text turns are imported as bootstrap history. System messages are applied to the worker request but are not persisted as conversation events.
- Added tests for the stable public model list, initial history import, system-message forwarding, explicit continuation, and rejection of streaming/unknown model aliases.
- Updated runbook with the API contract and a warning: a generic OpenAI client that discards the custom `conversation_id` response field will not automatically maintain canonical conversation mapping. Open WebUI integration is not yet verified.
- Current limitations remain: no streaming, image/audio payloads, tool/function calling, idempotency, safe regenerate/edit semantics, authentication, multi-process locking, or semantic task planner. The task type remains an explicit internal API hint.
- The CI workflow has been added, but no successful GitHub Actions run has yet been observed. Tests are committed, not verified by execution in this session.
- No DGX Spark host, running service, model, container, workstation, or local installation was changed.


### 2026-11-09 — Execution plan contract and dependency-wave executor
- Added Pydantic contracts for plans and tasks, with strict field validation, unique task IDs, known dependency checks, duplicate/self-dependency rejection, and cycle detection.
- Added topological dependency waves: independent tasks appear in the same wave; dependants appear only in later waves.
- Added a bounded asynchronous executor. It runs tasks in a wave concurrently up to a configured limit, passes successful dependency outputs to downstream tasks, retains successful sibling results, and marks dependants blocked after a failed dependency.
- Added tests for graph ordering, parallel overlap, dependency-output handoff, unknown dependencies, cycles, duplicate IDs, partial failure, and invalid concurrency.
- This is an execution-engine foundation only: plans are not yet generated by an LLM, persisted in SQLite, exposed through API routes, or connected to the chat endpoint. No model dispatch adapter is wired to this executor yet.
- Test source has been committed but has not been executed from a direct repository checkout in this session. Do not mark this slice verified until CI or a checkout test run passes.
- No DGX Spark host, model, runtime, container, service, or workstation was changed.


### 2026-11-09 — Structured LLM Planner
- Added a model-agnostic planner adapter that asks an injected OpenAI-compatible planning gateway for a JSON execution plan matching the local Pydantic schema.
- Planner instructions request the smallest useful plan, distinguish independent tasks from dependent tasks, and constrain task capabilities to an explicit allow-list derived by the caller.
- Planner output is treated as untrusted: JSON is parsed locally, schema and dependency graph are validated, and any unregistered capability is rejected before execution.
- Added tests for valid/fenced JSON, context forwarding, invalid JSON, empty requests, and capability allow-list enforcement.
- The planner is a callable component only. It is not yet selected through the worker registry, invoked from `/v1/chat/completions`, or persisted as a plan. No automatic multi-model execution is enabled by this commit.
- GitHub Actions is running tests for the new commits; final CI result for the latest branch head is pending.
- No DGX Spark host, model, runtime, container, service, or workstation was changed.


### 2026-11-09 — Durable execution and multi-model orchestration service
- Added SQLite tables for executions and task lifecycle records, including the validated plan JSON, task capability requirements, dependency IDs, assigned worker, status, output, and error.
- Added database operations to create an execution atomically with its tasks, update task lifecycle, finish an execution, and retrieve a complete execution after database reopen.
- Added capability selection requiring a worker to support every capability declared by a task; added planning-worker selection that prefers an explicit planning capability and falls back to chat.
- Added `MultiModelOrchestrator`: it selects a planning worker, obtains and validates a plan, persists it before dispatch, routes tasks to capability-compatible workers, executes dependency waves with bounded concurrency, records task outcomes, and synthesizes multiple successful outputs through a chat worker.
- Added an opt-in `POST /api/v1/conversations/{conversation_id}/orchestrated-turns` route and `CLICODING_ENABLE_PLANNING` setting. The existing OpenAI `/v1/chat/completions` path remains on deterministic single-worker routing until the next integration slice; this is deliberate so existing clients are not silently moved to a higher-cost planner path.
- Added tests for persistence across DB reopen and an end-to-end fake-gateway orchestration across planner, coding, vision, and synthesis workers.
- CI runs for the latest commits are pending. Do not treat these newly added tests as passed until the latest main-branch Actions run reports success.
- Current limits: no Open WebUI conversation-ID mapping verification, no streaming, no multimodal image transport in the OpenAI endpoint, no task cancellation/retry, no cross-process scheduling locks, and no resource-aware model admission. The orchestrated route is opt-in and still requires deployment configuration plus API security before LAN exposure.
- No DGX Spark host, model, runtime, container, service, or workstation was changed.


### 2026-11-09 — Standard OpenAI endpoint orchestration integration
- Updated the shared turn pipeline so enabling `CLICODING_ENABLE_PLANNING=true` routes both the internal conversation-turn API and the standard non-streaming `POST /v1/chat/completions` endpoint through the multi-model Orchestrator.
- The public model alias remains `clicoding`; internal worker IDs remain hidden from the client. The response retains the canonical `conversation_id` and adds `x_clicoding.execution_id` plus an `orchestrated` indicator.
- Existing canonical conversation events are supplied as prior context on subsequent turns; client-supplied system messages are passed to the planner context. The standard route remains deterministic single-worker when planning is disabled.
- Added an API regression test covering two OpenAI-compatible turns with the same conversation ID, one public model identity, persisted orchestrator-produced events, and prior-turn context continuity.
- Added a safe final-synthesis fallback: if the chat synthesis worker fails after task execution, completed worker outputs are returned rather than converting completed work into an HTTP failure.
- Latest CI for the API integration and synthesis fallback is running; do not mark it verified until the current main-branch run completes successfully.
- Remaining important gaps: Open WebUI conversation-ID adapter validation, streaming, multimodal image payload transport, idempotency/retry semantics, auth/tenant isolation, cross-process locking, cancellation, and resource-aware scheduling.
- No DGX Spark host, model, runtime, container, service, or workstation was changed.


### 2026-11-09 — CI verification for standard endpoint orchestration
- GitHub Actions run [42600738892](https://github.com/hamedg2881-bot/CLICoding/actions/runs/42600738892) completed the test suite successfully for code commit `6950900e5e67e60f604656b44e2415b6d24f6d0a`.
- Result: **28 passed**, one upstream Starlette/httpx deprecation warning; no test failures.
- This verifies the OpenAI-compatible endpoint orchestration integration, stable conversation continuity test, persisted execution tests, and fallback when a synthesis worker is not available.
- Documentation-only commits may follow this tested code revision; no runtime deployment has been performed.


### 2026-11-09 — Final source review and pre-deployment hardening
- Reviewed all current Python modules, project configuration, example worker registry, tests, and deployment-facing documentation.
- Fixed SQLite `:memory:` behavior: repository operations now share a kept-alive in-memory database instead of opening unrelated empty databases per session.
- Enforced each enabled worker's configured `max_concurrency` across concurrent requests within one process, both for deterministic chat routing and planner-backed execution. These limits are process-local and are not a distributed scheduler.
- Hardened worker registry validation: worker IDs/capability names, HTTP(S) endpoint URLs, enabled flags, concurrency, priority, timeout, and configured secret environment variables are validated; a declared but missing API-key environment variable now fails closed.
- Added optional inbound Bearer authentication using `CLICODING_API_KEY`; all API and diagnostic routes are protected when configured, while `/health` remains available for health checks. This is one shared key only, not user/tenant isolation. Internal TLS remains required for LAN exposure.
- Unified the legacy `orchestrated-turns` endpoint with the canonical turn pipeline to avoid duplicated conversation handling and inconsistent context assembly.
- Added regression tests for in-memory SQLite persistence, shared worker concurrency, invalid worker endpoints/limits, missing configured secrets, and API authentication.
- Updated README, environment example, and Orchestrator runbook to remove stale claims about planner integration, concurrency, and authentication.
- Latest CI for these final-review changes is pending. DGX deployment is not yet approved: Open WebUI conversation-ID preservation, image transport, streaming, multi-process safety, TLS, and current DGX resource/runtime inventory remain unverified.
- No DGX Spark host, model, runtime, container, service, or workstation was changed.


### 2026-11-09 — Final-review CI verification
- GitHub Actions run [42601893118](https://github.com/hamedg2881-bot/CLICoding/actions/runs/42601893118) completed successfully for code/test commit `cbe59f3a499a2107f151753e644c1d82e8587054`.
- Result: **37 passed**, one Starlette/httpx deprecation warning; no test failures.
- Coverage includes conversation persistence, in-memory SQLite behavior, worker routing and registry validation, planner/schema validation, dependency-wave execution, durable execution state, OpenAI API continuity, bearer authentication, and process-local worker concurrency enforcement including logical aliases of the same runtime.
- The tested source is suitable for controlled DGX staging, but not yet production-ready for multi-user LAN use. Open WebUI mapping, streaming, image transport, tenant isolation, TLS termination, and live DGX compatibility checks remain deployment gates.
- No DGX Spark host, model, runtime, container, service, or workstation was changed.


### 2026-11-09 — Authentication and runtime-alias concurrency recheck
- Final code revision `6974a962b399bafc02b1b572c874a320f93dcde7` passed GitHub Actions run [42602083537](https://github.com/hamedg2881-bot/CLICoding/actions/runs/42602083537).
- Result remains **37 passed**, one Starlette/httpx deprecation warning.
- The final recheck includes case-insensitive Bearer scheme handling and verifies the API authentication test after the change. The latest subsequent commits only update staging documentation.


### 2026-11-09 — Three-model replacement topology recorded and tested
- User reaffirmed that Qwen3.8 Flash is not the target architecture; it is to be replaced by three specialist runtimes after successful validation.
- Confirmed model roles:
  - **Coder:** Qwen3-Coder-Next-NVFP4-GB10; local directory `/opt/ai/models/Qwen3-Coder-Next-NVFP4-GB10`.
  - **Vision:** Qwen3.6-VL-35B-A3B-NVFP4-DGX-Spark-VisionSafe; local directory `/opt/ai/models/Qwen3.6-VL-35B-A3B-NVFP4-DGX-Spark-VisionSafe`.
  - **Small/fast:** exact model remains unselected; earlier discussion only established a 4B–8B target class. Do not infer a model identity or endpoint.
- The small/fast runtime is configured to provide both `planning` and `chat` capabilities, allowing it to propose structured plans and synthesize final results. The Orchestrator remains responsible for validating plans, dependency execution, capability dispatch, conversation state, and final response handling.
- Updated `config/workers.example.yaml` to a three-runtime topology, disabled by default, with placeholder served aliases/endpoints. The file does not start, stop, wake, sleep, download, or install models.
- Added `docs/THREE_MODEL_TOPOLOGY.md` with request flow, resource/lifecycle policy, exact known model paths, and explicit gates before Flash retirement.
- Added an orchestration test proving the small/fast worker handles both planning and synthesis while Coder and Vision independently execute specialist tasks.
- GitHub Actions run [42603776820](https://github.com/hamedg2881-bot/CLICoding/actions/runs/42603776820) passed: **38 passed**, one Starlette/httpx deprecation warning.
- Three related commits are on `main`; latest reviewed documentation commit is `f3e5f699853492be92108f0de706781d733b12e8`.
- Remaining blockers: choose and benchmark the small/fast model; verify actual served aliases and endpoints; implement and test image payload transport to Vision; verify Open WebUI conversation mapping and streaming; measure safe concurrent memory use on DGX.
- No DGX model, container, runtime, service, or host configuration was changed. Flash remains untouched pending replacement acceptance and explicit approval.


### 2026-11-09 — Three-model MoE selection and concurrent residency confirmed
- User selected the three-model target set for CLICoding:
  - **Fast reasoning/planning:** OpenAI GPT-OSS-20B MXFP4 (MoE; 20.9B total / 3.6B active); text-only.
  - **Coding:** Qwen3-Coder-Next NVFP4 (MoE; 80B total / 3B active).
  - **Multimodal general + vision:** Qwen3.6-35B-A3B NVFP4 (MoE; 35B total / 3B active).
- The 35B Qwen3.6 model is assigned both general chat and vision; it is not a vision-only worker.
- User clarified that all three runtimes must remain loaded simultaneously. Runtime switching/unloading is not part of normal request handling. Start with one active request per runtime and allow up to three independent tasks in parallel.
- The example registry now declares the three selected worker identities and capability sets. Example ports/served aliases remain placeholders pending read-only discovery; entries remain disabled by default and do not start/load models.
- No DGX host, model, container, runtime, service, or configuration has been changed in this implementation step.
- Next implementation tasks: multimodal image transport to Qwen3.6, configurable global parallelism of 3, worker readiness checks, then controlled simultaneous-residency validation on DGX. Flash remains untouched until acceptance and explicit retirement approval.


### 2026-11-09 — Three-runtime orchestration and multimodal transport implementation
- Added configurable global parallelism, defaulting to 3 through `CLICODING_MAX_PARALLEL_TASKS`; the executor now allows three independent tasks to overlap. Per-runtime worker semaphores still enforce each runtime's own `max_concurrency` (default 1).
- Updated the standard OpenAI-compatible chat request to accept text blocks and base64 image data URIs (maximum 12 MB per encoded image field; remote URLs and other modalities are rejected). Multimodal user content is stored in the canonical SQLite conversation event and restored when building worker history.
- When an image is present, the planner is instructed to create a vision task. The Orchestrator validates that requirement and forwards the actual image content only to workers declaring the `vision` capability; Coder receives textual context/findings, not the raw image payload.
- Added tests for three-way concurrency, three selected worker-role routing, API image validation/persistence, and image forwarding to the multimodal worker without leaking image data to Coder.
- GitHub Actions run [42606589994](https://github.com/hamedg2881-bot/CLICoding/actions/runs/42606589994) passed on commit `446473111e7273527cde3c6d49e1351caf459f2f`: **44 passed**, one Starlette/httpx deprecation warning.
- These tests use fake worker gateways. Live Qwen3.6 image inference, GPT-OSS/Coder endpoint compatibility, Open WebUI conversation-ID behavior, streaming, and simultaneous resident memory use on DGX remain unverified.
- No DGX host, model, container, runtime, service, or configuration was changed.


### 2026-11-09 — DGX runtime compatibility research and preflight preparation
- Added `docs/DGX_RUNTIME_PROFILES.md` with model-specific GB10 benchmark references, isolated-runtime strategy, initial context/KV budgets, and acceptance sequence.
- Current public evidence supports the selected roles: GPT-OSS-20B has a reported 96.2 tok/s result on GB10 with vLLM 0.31.0; Qwen3.6-35B-A3B NVFP4 has a reported 102.3 tok/s with MTP on vLLM 0.31.0; Qwen3-Coder-Next NVFP4 has reported 60.8 tok/s baseline and 72 tok/s with an external EAGLE3 draft on a vLLM 0.24.0 recipe. These are separate single-model tests, not a simultaneous three-runtime benchmark.
- Runtime versions differ: Qwen3.6 NVFP4 GB10 recipe requires vLLM 0.28.0+; GPT-OSS 20B has a GB10 vLLM 0.31.0 benchmark; the cited Coder-Next NVFP4 recipe is tuned for vLLM 0.24.0/0.25.1 and reports a regression for one checkpoint on 0.26.0. Use isolated containers and verify the actual image tags/checkpoint formats before selecting exact launch images.
- Added `scripts/dgx_spark_model_preflight.sh`, a read-only inventory of model directories/config metadata, memory, NVIDIA status, Docker images/containers, listening ports and existing OpenAI model endpoints.
- Preliminary budget arithmetic: approximately 78 GiB of model weights plus 14 GiB initial KV caps leaves roughly 29 GiB of the 121 GiB usable memory for runtime workspaces and OS. This is only an estimate; it does not prove all three processes fit.
- Next gate: user runs the read-only preflight and shares its output. Then prepare exact per-model Compose/runtime settings using verified local model paths and image versions. No host mutation has occurred.


### 2026-11-09 — Multimodal conversation continuity completed
- Conversation retrieval now restores persisted multimodal content blocks to the OpenAI-style array form instead of exposing the internal SQLite storage encoding.
- Follow-up turns without a new image reuse the most recent user image attachment for Vision routing, so requests such as “explain the highlighted part” retain the image context across turns.
- The actual image payload is included only in the multimodal worker request; the Coder worker receives text/context rather than raw image bytes.
- GitHub Actions run [42607106297](https://github.com/hamedg2881-bot/CLICoding/actions/runs/42607106297) passed on commit `52dc2ef68fa9413601f01c109de8f702aa4f4a80`: **44 passed**, one existing Starlette/httpx deprecation warning.
- Latest workflow also validates the read-only DGX preflight shell script with `bash -n` and parses the example worker YAML.


### 2026-11-10 — DGX three-runtime live smoke-test update
- Recorded the live DGX state in [docs/DGX_LIVE_VALIDATION_2026-11-10.md](DGX_LIVE_VALIDATION_2026-11-10.md).
- Confirmed live host endpoints: GPT-OSS-20B at port 8003 (vLLM 0.28.0, context 8K), Qwen3-Coder-Next at port 8004 (vLLM 0.24.0, context 8K), and Qwen3-VL-2B at port 8005 (vLLM 0.28.0, context 4K).
- GPT-OSS and Coder passed independent real text requests and a concurrent request test. Qwen3-VL-2B loaded successfully and passed a text-only API smoke test.
- Qwen3-VL-2B is an evaluation candidate only; it does not replace the selected Qwen3.6-35B-A3B target model without a separate explicit selection decision.
- At the latest recorded snapshot, all three runtimes were resident; available RAM was about 17 GiB and swap use about 4.5 GiB. This is not yet a sustained-load safety guarantee.
- No real Vision image inference has been run yet. Persian/English OCR, screenshot/UI interpretation, coordinate grounding, and error analysis remain the immediate validation gate.
- Current context caps are deliberately conservative: GPT-OSS 8K, Coder 8K, Vision 4K. Do not increase them until KV-cache and concurrent-load measurements support it.
- No model files, containers, or images were deleted; Flash assets/runtime remain outside this experiment and untouched.

## 2026-11-11 — Conversation-first redesign started

- User-approved direction: one shared Conversation Engine for Open WebUI and IDE clients; ordinary chat goes directly to a chat-capable model; Planner is reserved for specialist/multi-step work; tool execution remains host-mediated, allow-listed, workspace-scoped, and approval-gated.
- Architecture decision recorded as D-015; architecture and roadmap updated in PR #6.
- PR #6 first vertical slice: ordinary text chat bypasses Planner even when planning is enabled; explicit specialist task hints and image turns retain the existing orchestration path; the explicit orchestrated-turn endpoint can force orchestration.
- Regression tests added for direct-chat bypass and explicit specialist dispatch. CI is pending at the time of this status entry.
- PR #5 (bounded Planner output repair) passed CI but is a defensive reliability improvement only; it is not the architecture fix and has not been merged.
- No changes deployed to DGX. ZIP transfer and service restart remain gated on CI, source review, host-side validation, and explicit user approval.
- Required next work: typed tool-call contracts; safe host-side tool execution and approvals; shared IDE adapter contract; workstation-side workspace/file/diff/build/test/screenshot loop; natural-language specialist routing; true incremental streaming and cancellation.

## 2026-11-11 — Modern chat interaction slice

- Extended the OpenAI-compatible model gateway with upstream SSE delta streaming.
- Direct chat now forwards actual incremental text deltas to clients; it does not wait for the complete model answer before sending the first content chunk.
- Conversation user event is persisted before generation; assistant event is persisted only after a complete successful stream. Interrupted streams are not recorded as completed assistant messages.
- Client cancellation propagates through async stream iteration so the upstream HTTP stream is closed and the worker semaphore is released.
- Added regression coverage for incremental delta chunks and final conversation persistence.
- CI run 43012306383 passed: 73 tests passed, 1 warning. Earlier implementation runs 43012195870 and 43012270120 also passed.
- Added docs/CHAT_EXPERIENCE_SPEC.md as the product acceptance contract for modern chat quality and IDE parity.
- Limitations remain explicit: specialist orchestration streams only its final answer; tool-call round-trip, context budgeting/compaction, client-visible cancellation controls, richer attachment storage, and reference IDE adapter are not yet implemented.
- No DGX transfer or service restart has occurred.

## 2026-11-11 — Gateway streaming tests and IDE tool boundary

- Added gateway-level tests for upstream OpenAI SSE delta parsing and rejection of streams that end without [DONE].
- Latest code CI run 43012613955 passed: 75 tests passed, 1 warning.
- Added docs/IDE_TOOL_PROTOCOL.md specifying the workstation-side tool host, typed call/result contract, workspace scope, idempotency, approval, cancellation, and evidence requirements.
- The tool-call round-trip and workstation agent are design targets only at this point; no execution API has been enabled.

## 2026-11-11 — CDS_AI identity and conversation-first foundation

- User confirmed the public assistant name is **CDS_AI**. The canonical GitHub repository remains `hamedg2881-bot/CLICoding`; product identity and source-repository identity are intentionally separate.
- The OpenAI-compatible API now advertises `CDS_AI` as its public model ID and returns that identity in completion responses. Legacy request aliases `clicoding`, `assistant`, and `auto` remain accepted for migration compatibility. Existing environment-variable names, database paths, and service identifiers are intentionally unchanged.
- Added `docs/PRODUCT_IDENTITY.md` and updated README/package/API metadata.
- Integrated the bounded planner-output repair from the separate planner-recovery PR into the conversation-first branch. Invalid JSON/schema output gets one repair attempt; persistent invalid output still fails closed.
- Direct ordinary chat remains Planner-independent and uses true upstream SSE delta streaming. Specialist streaming remains buffered until the task graph completes; it is not token streaming.
- GitHub Actions run **43013958446** on head `80a3366890e68688b81edc2d4cd40f02c1922370`: **76 passed, 1 warning**.
- No source files have been transferred to DGX Spark; no host services, model runtimes, or worker configurations have been changed.
- Remaining release blockers include structured tool-call round-tripping and a workstation tool host, per-user authorization/ownership enforcement, context budgeting and compaction, explicit cancellation/retry/regeneration, attachment lifecycle hardening, reference IDE adapter, and Open WebUI/IDE end-to-end acceptance.

## 2026-11-11 — Workstation adapter and context admission control

- Added the Python reference package `cds_ai_ide_adapter`: workspace-bound file listing/read/search, approved atomic file writes with SHA-256 optimistic concurrency, fixed Git status/diff, and approved fixed .NET build/test commands.
- Added a local SQLite idempotency ledger. Successful tool results are cached; a reused call ID with different arguments is rejected; interrupted side effects are not automatically re-executed.
- Added `CDSAIClient` to run the non-streaming tool loop on the workstation. DGX remains an inference/orchestration endpoint and receives no workspace mount or shell authority.
- Workstation tests cover traversal, symlink escape, secret-file blocking, approval denial, write idempotency, command argument arrays, restricted environment, and an end-to-end tool round-trip through the API.
- Added per-worker `context_window_tokens` and `max_output_tokens` limits. Context admission uses a conservative UTF-8 estimate and fixed image reservation; over-budget requests are rejected with HTTP 413 rather than silently truncated. Direct streaming is preflighted before SSE and rechecked under the conversation lock.
- Supported OpenAI generation options (temperature, max_tokens, top_p, presence/frequency penalties, bounded stop sequences) are forwarded to direct chat and tool-call workers; output limits are enforced per worker. Specialist user-supplied generation options are explicitly rejected for now rather than silently ignored.
- Automatic history summarization/compaction is not implemented. Token estimation is not tokenizer-specific and must be replaced/refined per actual runtime before larger context windows are enabled.
- Latest CI evidence for the workstation adapter before context-budgeting work: run 43015872942, **98 passed, 1 warning**. Context-budgeting branch is awaiting its own CI run.
- No transfer to DGX Spark or changes to any DGX runtime/service have been made.


## 2026-11-11 — Incremental tool-call SSE

- Added structured upstream SSE parsing for partial OpenAI function-call deltas.
- The OpenAI-compatible API now forwards `delta.tool_calls`, reassembles and validates the completed function call, and persists it only after a successful stream.
- Streamed tool-result continuation is supported; tool messages remain correlated to the exact pending call.
- Direct streaming still propagates client cancellation and does not persist a partial assistant event.
- Parallel tool calls remain disabled; one outstanding tool call per assistant turn is enforced.
- The Python workstation reference client currently uses buffered HTTP tool rounds; Open WebUI-compatible API clients can use streamed tool-call deltas.
- Live DGX runtime compatibility remains unverified and no production worker has been marked `tool_calling`.

## 2026-11-11 — Identity and conversation ownership

- Added `owner_id` to conversations with a backward-compatible migration; pre-existing records remain in the `shared` namespace.
- Added per-user bearer token mapping through `CLICODING_USER_API_KEYS` (JSON object of stable user IDs to unique 32+ character tokens); reserved principal `admin` is administrative.
- Added optional Open WebUI trusted-user-header mode through `CLICODING_TRUST_USER_HEADER=true`. This is explicitly a trusted-network mode, not cryptographic identity; it requires a private proxy that strips client-supplied identity headers.
- Conversation creation/read/turns, OpenAI-compatible continuation, execution reads, and sandbox approval/result routes now enforce owner scope. Worker diagnostics are admin-only.
- No automatic reassignment of legacy shared conversations; administrator migration is required before multi-user rollout.
- Remaining identity/security gates: verify Open WebUI identity headers in a real multi-user deployment, token rotation/revocation, rate limits, and audit export.


## 2026-11-11 — Retry and regeneration semantics

- Exact retries of the last unfinished user/tool input reuse the existing event; duplicate user/tool entries are not added after failed requests or stream cancellation.
- Added guarded tail-event deletion and `X-CLICoding-Operation: regenerate` for the latest completed assistant turn only.
- Regeneration verifies the exact preceding input and deletes only the current tail assistant event; earlier conversation history is retained.
- Buffered requests return HTTP 409 for invalid regeneration; streaming requests emit an SSE error.
- Editing an earlier turn and truncating subsequent history is not implemented; it requires a target event ID and explicit confirmation.
- Packaged Open WebUI/IDE controls for the regenerate header remain an integration task.


## 2026-11-11 — Edit and resubmit

- Added `X-CLICoding-Operation: edit` with an explicit `X-CLICoding-Event-ID` target.
- Only user events can be edited. The new request context contains all events before the target plus the replacement message.
- Target and later events are deleted transactionally only after a successful model response/tool-call stream; failed/cancelled edits preserve the original history.
- Works for buffered specialist turns and direct streamed chat/tool turns.
- Packaged UI controls for selecting the target event and confirming the history rewrite remain integration work.

## Consolidated validation

- Consolidated Draft PR: #13 — `Complete CDS_AI conversation, tools, identity, and IDE foundation`.
- CI run `43020629874` on head `3bfbaab4a30653ffa980bfbee9884c6a345581f2`: **128 passed, 1 warning**.
- Earlier incremental PRs #5–#12 are closed as superseded by PR #13; no changes were merged into `main`.
- All work remains on GitHub only. No source ZIP has been transferred to DGX Spark, and no DGX service/runtime/model has been changed or restarted.


## 2026-11-11 — Shared CDS_AI policy

- Added a single internal system policy applied to direct chat, streaming, specialist orchestration context, and the reference conversation endpoints.
- Establishes the CDS_AI identity, same-language responses, Persian RTL preservation, uncertainty/action honesty, tool-output untrusted-data handling, and approved-tool-only behavior.
- Client-provided system messages are retained but the CDS_AI policy is appended after them so they cannot override the core safety/reliability rules.
