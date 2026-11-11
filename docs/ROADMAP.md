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
- Per-user authentication/authorization and quotas.
- Fair queueing, concurrency limits, cancellation.
- Model lifecycle/sleep/wake policy with safe release.
- Load, isolation, and failure tests.
- Usage and operational dashboards.

Exit gate: target number of simultaneous users passes defined latency/error/resource thresholds without interrupting active inference.

## Phase 8 — Offline packaging and production acceptance
Deliverables:
- Version-pinned installers, IDE extensions, SDKs, browser binaries, runtimes, model weights, NuGet packages, docs.
- Internal artifact/package mirrors.
- Firewall/egress policy and internal TLS.
- Restore/backup and upgrade/rollback procedures.
- Offline installation guide and operator runbook.
- End-to-end acceptance report with network evidence.

Exit gate: a clean workstation can be provisioned from internal artifacts and complete the full workflow with public internet blocked.

## Change control
A phase may be marked complete only when its exit gate has reproducible evidence recorded in docs/STATUS.md or a linked test report. Planned is not implemented; implemented is not verified.

## Phase 2A — Conversation and orchestration core (required before multi-model workflows)

**Status:** Conversation persistence, deterministic capability-based handoff, validated execution plans, durable task state, bounded dependency-wave execution, and opt-in planner-backed orchestration are implemented in source. When `CLICODING_ENABLE_PLANNING=true`, the standard non-streaming OpenAI-compatible chat route now invokes the Orchestrator while preserving one public assistant alias and canonical conversation history. Latest final-review CI passed (37 tests); Open WebUI mapping and production hardening remain open.

Deliverables:
- [x] Initial conversation/event persistence contract with stable conversation identity independent of model (single-process SQLite slice).
- [x] In-memory task and execution-plan contracts with dependency/cycle validation.
- [x] Persistent task and execution-plan storage in SQLite.
- [x] Structured LLM Planner component with local schema, graph, and capability allow-list validation.
- [ ] Context assembly and multimodal attachment handoff.
- [x] Configuration-backed capability-based worker selection (deterministic task-type hint).
- [x] Dependency-wave executor with bounded parallelism, dependency-output handoff, and failure blocking.
- [x] Durable sequential/parallel task execution service and opt-in conversation API route.
- [x] Integrate opt-in orchestration into the standard OpenAI chat route; Open WebUI mapping and safety/production validation remain pending.
- [x] Non-streaming transparent worker handoff with shared conversation history.
- [ ] Unified response streaming.
- [ ] Bounded retry, partial failure recovery, cancellation, and approval gates.
- [ ] Contract/acceptance tests from [ORCHESTRATOR_SPEC.md](ORCHESTRATOR_SPEC.md).
- [x] OpenAI-compatible non-streaming text endpoint with one public model alias and an explicit conversation ID extension.

Exit gate: pass the single-model continuity, model handoff, Vision-to-Coder handoff, sequential and parallel workflow, partial failure, cancellation, context integrity, and resource safety tests.

Phase 2 gateway work must expose transport primitives required by this core, but LiteLLM or any other gateway must not become the owner of conversation or task state. The first implementation should be a thin vertical slice, not a large framework adoption. The current code includes a YAML-backed worker registry, deterministic capability routing, same-conversation history, a non-streaming OpenAI-compatible text API, validated execution plans, a structured LLM Planner, SQLite-persisted execution/task state, and a multi-model orchestration service with bounded dependency-wave execution. The service is available through both the standard OpenAI chat endpoint and the internal conversation route when planning is enabled; otherwise the standard endpoint retains deterministic single-worker routing. Worker concurrency limits are enforced per served runtime within one process. Open WebUI conversation mapping, streaming, multimodal image transport, cancellation/retry, cross-process locking, distributed admission, and resource-aware scheduling remain unimplemented. Open WebUI's ability to preserve the returned conversation ID remains unverified.

## Phase 2B — Conversation-first core and shared IDE agent contract (priority)

**Status:** Approved; implementation started on branch `architecture/conversation-first-core`.

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

## Phase 2C — Modern assistant interaction quality (priority, 2026-11-11)

The detailed product and release contract is in docs/CHAT_EXPERIENCE_SPEC.md. This is a first-class architecture requirement, not UI polish.

- Completed in this branch: true upstream SSE delta streaming for direct chat; persist assistant turn only after the full stream succeeds; cancel upstream iteration when the client disconnects; regression coverage.
- Next: tool-call round-trip and workstation tool broker contract; context-window budgeting/compaction; cancellation and retry semantics; multimodal attachment references; reference IDE adapter.
- Specialist execution continues to use explicit orchestration and currently streams only its completed final answer. Do not claim token-level specialist progress until implemented.

## Phase 2D — Secure workstation tool protocol

The tool-call and IDE-side execution boundary is specified in docs/IDE_TOOL_PROTOCOL.md.

- The IDE adapter owns workspace, file, process, build/test, browser, and screenshot operations on the developer workstation.
- The DGX remains an inference/orchestration host and must not mount developer workspaces.
- Next implementation: OpenAI tool-call message round-trip, typed tool definitions/results, and a reference local tool host with allow-list and approval gates.
- No arbitrary shell tool is exposed; tools are introduced one at a time with schemas, policy, audit, idempotency, and tests.


## CDS_AI source completion and release gates — 2026-11-11

The user-facing assistant identity is **CDS_AI**. The consolidated source candidate is tracked in PR #12. Earlier PRs #6–#11 are historical implementation slices and must not be treated as independent release artifacts.

### Implemented in the current source candidate
- [x] Conversation-first routing: ordinary chat bypasses Planner.
- [x] Direct chat upstream SSE and disconnect cleanup.
- [x] OpenAI function tool-call request/response round-trip with schema validation.
- [x] Incremental tool-call SSE deltas and exact pending-call correlation.
- [x] Workstation reference tool host with workspace scoping, approval, idempotency and fixed commands.
- [x] Conservative context admission and output reservation; overflow is explicit.
- [x] Per-user bearer-token ownership isolation and optional trusted-header mode.
- [x] Retry idempotency for interrupted user turns and exact duplicate tool results.
- [x] Product/API identity `CDS_AI`.
- [x] Specialist final-answer SSE framing regression coverage.

### Still required before the product can be called complete
1. Conversation lifecycle: user-visible stop/cancel, retry, regenerate, edit/resubmit, turn lineage and concurrency semantics.
2. Context lifecycle: model-specific tokenizers, automatic safe compaction, summary lineage, context inspection and recovery.
3. Attachments: typed attachment IDs, bounded storage/retention, MIME sniffing, decoding, access control and image lifecycle.
4. IDE integration: packaged reference editor integration, approval UI, diff review/apply, local build/test evidence and a second adapter compatibility check.
5. Engineering loop: browser/Playwright execution, screenshot capture, vision-based visual validation, bounded repair and rollback.
6. Security/operations: rate limits, token rotation/revocation, audit export, process sandboxing, result redaction, multi-process conversation locking and load tests.
7. Live runtime: verify exact model aliases, tool-call parser behavior, vision behavior, context limits, timeout/cancellation and resource headroom on DGX.
8. Offline release: pinned dependency artifacts, clean workstation install, public-internet-blocked end-to-end test, backup/restore and rollback.
9. Client acceptance: Open WebUI plus at least one packaged IDE client, multi-user isolation and long-running conversation acceptance.

Use `docs/RELEASE_GATES.md` as the single release checklist. A source test passing does not imply a live model capability or deployment gate has passed.
