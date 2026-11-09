# Project Status

**Last updated:** 2026-11-09  
**Canonical repository:** https://github.com/hamedg2881-bot/CLICoding  
**Current phase:** Phase 2A — conversation and multi-model orchestration core  
**Overall state:** Conversation persistence, capability routing, structured planning, durable multi-model execution, optional Bearer authentication, and the non-streaming OpenAI-compatible API are implemented. GitHub Actions verified the final-reviewed source revision with 37 passing tests; Open WebUI integration and DGX deployment validation remain open. Open WebUI integration and DGX deployment validation remain open.

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
- Previously observed environment details associated with the user's DGX Spark: GB10 platform, approximately 128 GB unified memory, NVIDIA driver 580.178.04, CUDA 13.0. These are historical context, not yet re-verified for this project's Phase 1 baseline.
- Host allocation (dedicated/shared), current OS/kernel, CPU, memory availability, storage, active GPU workloads, runtime versions, model inventory, and LAN configuration remain unverified.
- No installation, configuration change, model download, service restart, or other server mutation has been performed for CLICoding.

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
