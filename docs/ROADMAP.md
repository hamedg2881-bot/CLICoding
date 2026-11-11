# Implementation Roadmap

This is the canonical ordered plan. Do not skip gates or add optional infrastructure before the previous vertical slice is proven.

## Phase 0 — Architecture and repository baseline
**Status:** In progress; initial documentation committed.

Deliverables:
- [x] Project charter and non-negotiable requirements.
- [x] Architecture baseline.
- [x] Ordered roadmap and workflow.
- [x] Decision log and status record.
- [ ] Confirm target server/workstations and deployment constraints.
- [ ] Define initial acceptance test project.

Exit gate: user confirms infrastructure constraints; docs accurately reflect the agreed environment.

## Phase 1 — Infrastructure and model capability inventory
Deliverables:
- Server CPU/RAM/GPU/storage/OS/network inventory.
- Workstation OS/IDE/.NET SDK inventory.
- Candidate model/runtime shortlist.
- Reproducible benchmark prompts for .NET repository understanding, multi-file edits, tool calling, vision, latency, and concurrent use.
- Capability/compatibility matrix with actual test evidence.

Exit gate: choose initial coding model, vision-capable model, runtime, and hardware allocation based on measured results.

## Phase 2 — LAN AI Gateway
Deliverables:
- Internal model runtime and gateway.
- Stable internal endpoint and model aliases.
- Authentication, request limits, timeouts, cancellation, streaming.
- Capability-aware routing.
- Queue/concurrency and health endpoints.
- Logs/metrics without recording secrets or unnecessary source content.

Exit gate: approved clients can call the gateway over LAN; tool calling and image input pass compatibility tests; service survives restart and load tests.

## Phase 3 — Agent and IDE integration
Deliverables:
- One proven agent client as initial reference integration.
- IDE-agnostic agent/tool contract.
- Discovery, context assembly, planning, approval, file read/search/edit, diff.
- Workspace scoping and tool allowlist.
- Offline installation and configuration instructions.

Exit gate: agent understands a sample .NET solution, proposes a file-level plan, applies an approved change, and presents a reviewable diff with internet blocked.

## Phase 4 — Engineering validation
Deliverables:
- Git status/diff/restore integration.
- .NET build/test/analyzer commands.
- Test result capture and feedback to agent.
- Safe retry/fix loop.
- Approval for destructive commands, package changes, and commit/push.

Exit gate: a deliberately failing sample change is diagnosed and corrected with actual successful build/test evidence; rollback is demonstrated.

## Phase 5 — Vision and screenshot-based UI development
Deliverables:
- Manual screenshot/image attachment.
- Verified image-content routing to Vision.
- Playwright install and local browser automation.
- Automated screenshot capture for configured viewports/states.
- Visual issue reporting tied to acceptance criteria.
- Iterative UI correction and before/after evidence.
- Region annotation/selection, if supported by selected client, as a follow-up increment.

Exit gate: agent receives a screenshot, identifies a seeded visual defect, changes the correct UI code, and produces a new screenshot showing the verified correction; all while internet is blocked.

## Phase 6 — Local project knowledge
Deliverables:
- Local source/documentation indexing.
- Embedding model and vector/search store if justified.
- Incremental index updates and deletion handling.
- Retrieval citations/file references and relevance evaluation.

Exit gate: retrieval improves task accuracy on a repeatable test set over plain repository search. Do not ship RAG if it does not demonstrate measurable value.

## Phase 7 — Multi-user and resource management
Deliverables:
- [x] Per-user authentication/ownership, environment and durable token lifecycle.
- [x] Per-principal API rate limits and administrator audit export.
- [x] Active-turn cancellation and bounded request admission.
- [ ] Fair queueing and cross-process worker concurrency coordination; conversation turn leases/cancellation are implemented, but worker-wide distributed semaphores remain process-local.
- [ ] Model lifecycle/sleep/wake policy with safe release.
- [ ] Load, isolation, and failure tests.
- [ ] Usage and operational dashboards.

Exit gate: target number of simultaneous users passes defined latency/error/resource thresholds without interrupting active inference.

## Phase 8 — Offline packaging and production acceptance
Deliverables:
- [x] Verified offline SQLite/attachment backup and restore CLI with manifest hashes and retained pre-restore rollback paths.
- [ ] Version-pinned installers, IDE extensions, SDKs, browser binaries, runtimes, model weights, NuGet packages, docs.
- [ ] Internal artifact/package mirrors.
- [ ] Firewall/egress policy and internal TLS.
- [ ] Upgrade/rollback and clean offline installation guide/operator runbook.
- [ ] End-to-end acceptance report with network evidence.

Exit gate: a clean workstation can be provisioned from internal artifacts and complete the full workflow with public internet blocked.

## Change control
A phase may be marked complete only when its exit gate has reproducible evidence recorded in docs/STATUS.md or a linked test report. Planned is not implemented; implemented is not verified.

## Phase 2A — Conversation and orchestration core (required before multi-model workflows)

**Status:** Source implementation is substantially complete for the conversation-first baseline: durable history, direct-chat streaming, explicit specialist routing, validated plans/tasks, tool-call transport, cancellation, context budgeting/compaction, image references, ownership and retry idempotency. PR #14 currently passes 164 CI tests. Live DGX compatibility, specialist progress, worker-wide distributed concurrency and production acceptance remain open.

Deliverables:
- [x] Initial conversation/event persistence contract with stable conversation identity independent of model (single-process SQLite slice).
- [x] In-memory task and execution-plan contracts with dependency/cycle validation.
- [x] Persistent task and execution-plan storage in SQLite.
- [x] Structured LLM Planner component with local schema, graph, and capability allow-list validation.
- [x] Context assembly, per-worker admission and multimodal attachment references for direct chat/tool turns; specialist-specific context acceptance remains open.
- [x] Configuration-backed capability-based worker selection (deterministic task-type hint).
- [x] Dependency-wave executor with bounded parallelism, dependency-output handoff, and failure blocking.
- [x] Durable sequential/parallel task execution service and opt-in conversation API route.
- [x] Integrate opt-in orchestration into the standard OpenAI chat route; Open WebUI mapping and safety/production validation remain pending.
- [x] Non-streaming transparent worker handoff with shared conversation history.
- [x] Upstream token/tool-call SSE for direct chat; specialist final-answer SSE is buffered and live progress remains open.
- [x] Active-turn cancellation, retry idempotency for user/tool continuations, and workstation approval gates.
- [ ] Bounded automatic specialist retries and end-to-end partial-failure recovery.
- [ ] Contract/acceptance tests from [ORCHESTRATOR_SPEC.md](ORCHESTRATOR_SPEC.md).
- [x] OpenAI-compatible streaming chat/tool-call endpoint with one public CDS_AI alias and canonical conversation IDs.

Exit gate: pass the single-model continuity, model handoff, Vision-to-Coder handoff, sequential and parallel workflow, partial failure, cancellation, context integrity, and resource safety tests.

Phase 2 gateway work exposes the transport primitives required by this core; LiteLLM or any other gateway does not own conversation or task state. The source includes a YAML-backed worker registry, deterministic capability routing, same-conversation history, direct upstream SSE, validated execution plans, a structured Planner, SQLite-persisted execution/task state, bounded dependency-wave execution, OpenAI tool-call round-trip, per-worker context budgeting/compaction, image attachment references, retry idempotency, and cancellation. Ordinary chat bypasses Planner. Specialist work uses explicit orchestration and currently returns a buffered final answer. Worker semaphores remain process-local; conversation turn leases/cancellation, SQLite event sequence allocation and rate limits are serialized across connections. Live Open WebUI/model compatibility, worker-wide distributed concurrency, resource-aware model scheduling and operational acceptance remain open.

## Phase 2B — Conversation-first core and shared IDE agent contract (priority)

**Status:** Implemented in source on the CDS_AI integration branch. The Python workstation adapter and typed tool protocol exist; packaged IDE UI, OS sandboxing and live workstation acceptance remain open.

### Architecture target
- One model-independent Conversation Engine is the entry point for Open WebUI and IDE clients.
- Ordinary chat bypasses Planner and responds directly through a chat-capable model.
- Specialist routing invokes coding/review/vision workers; Planner is reserved for work that genuinely needs decomposition.
- Tool calling uses typed, validated, allow-listed contracts. The orchestrator executes tools under policy, workspace scope, audit, and approval rules.
- The same assistant identity, conversation history, and context contract are shared by chat and IDE adapters.
- IDE-side workspace, file, terminal, build/test, browser, and screenshot tools execute on the developer workstation; model inference remains on DGX over LAN.

### Ordered implementation slices
1. **Direct-chat baseline:** route ordinary text chat directly to the chat worker; prove that Planner is not called; retain persisted conversation continuity.
2. **Explicit specialist dispatch:** route declared coding/review/vision tasks to existing specialist orchestration; preserve image handoff and durable task records.
3. **Tool-call contract:** define typed tool requests/results, allow-list, validation, error semantics, idempotency, and approval policy before enabling execution.
4. **IDE adapter:** implement one reference IDE client against the same stable API; pass workspace context and tool results without exposing worker endpoints.
5. **Engineering loop:** approved file edits, diffs, build/test evidence, bounded repair, rollback, and screenshot-based visual validation.
6. **Portability and hardening:** validate a second editor, streaming, cancellation, concurrency, failure recovery, and offline installation.

### Acceptance gates
- A greeting and general question succeed with Planner unavailable and produce no execution-plan row.
- Multi-turn context survives direct chat and model handoff.
- Explicit coding/review and image requests reach the correct specialist path.
- Malformed planner output is rejected/recovered without a generic unhandled 502 or execution of partial data.
- No file/shell/build/test operation executes without the appropriate workstation tool policy and approval.
- An IDE can understand a sample repository, propose a scoped plan, request approval, apply a diff, run build/tests locally, and report real exit-code evidence.
- All CI tests pass before ZIP transfer; DGX deployment and Orchestrator restart require separate validation and user approval.

The existing Phase 3 workstation-side IDE requirement remains mandatory; this phase makes the shared conversation/tool contract a prerequisite rather than deferring it as a disconnected add-on.

## Phase 2C — Modern assistant interaction quality (2026-11-11)

The product and release contract is in docs/CHAT_EXPERIENCE_SPEC.md.

- Implemented: conversation-first direct chat, true upstream SSE deltas, client-disconnect cleanup, tool-call delta transport, cancellation, regenerate/edit-resubmit, context budgeting/compaction, attachment references, per-user ownership, token lifecycle, per-principal rate limiting and audit export.
- Specialist orchestration remains a buffered final-answer stream; truthful live progress events remain open.
- Live model/runtime tool-call and vision compatibility must be proven on DGX before enabling those capabilities in production.

## Phase 2D — Secure workstation tool protocol

The tool-call and IDE-side execution boundary is specified in docs/IDE_TOOL_PROTOCOL.md.

- Implemented: typed tool definitions/results, schema validation, pending-call correlation, workstation Python reference host/client, workspace scope, approval, idempotency, fixed Git/.NET commands, credential-pattern redaction and verified backup/restore tooling.
- The DGX remains an inference/orchestration host and must not mount developer workspaces.
- No arbitrary shell tool is exposed. The model requests tools; the workstation host applies its own allow-list and approval policy before local execution.
- Open: packaged editor UI, platform-specific path/TOCTOU protections, OS-level process sandboxing, browser/screenshot automation, cross-platform acceptance and live model compatibility.
