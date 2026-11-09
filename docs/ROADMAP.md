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

**Status:** First single-model persistence slice implemented in source; automated tests are written but not yet executed. Multi-model orchestration remains unimplemented.

Deliverables:
- [x] Initial conversation/event persistence contract with stable conversation identity independent of model (single-process SQLite slice).
- [ ] Persistent task and execution-plan contracts.
- [ ] Context assembly and multimodal attachment handoff.
- [ ] Capability-based worker selection.
- [ ] Sequential, parallel, and hybrid task execution with dependency tracking.
- [ ] Unified response/streaming and transparent model handoff.
- [ ] Bounded retry, partial failure recovery, cancellation, and approval gates.
- [ ] Contract/acceptance tests from [ORCHESTRATOR_SPEC.md](ORCHESTRATOR_SPEC.md).

Exit gate: pass the single-model continuity, model handoff, Vision-to-Coder handoff, sequential and parallel workflow, partial failure, cancellation, context integrity, and resource safety tests.

Phase 2 gateway work must expose transport primitives required by this core, but LiteLLM or any other gateway must not become the owner of conversation or task state. The first implementation should be a thin vertical slice, not a large framework adoption. The current code proves persistence and same-conversation context reconstruction for one configured upstream model; it does not yet prove dynamic worker handoff or multi-model execution.

