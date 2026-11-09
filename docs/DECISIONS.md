# Architecture Decision Log

This file records accepted project-level decisions. Superseded decisions remain visible with status and replacement rationale.

## D-001 — LAN-hosted models, workstation-side tools
- **Status:** Accepted baseline
- **Decision:** Serve models centrally over the internal LAN. Keep workspaces, IDEs, build/test execution, browser automation, and Git operations on developer workstations.
- **Rationale:** Centralizes expensive model hardware while preserving local source control, tool execution, and workspace isolation.
- **Consequence:** A workstation-side agent/tool broker is required; model server does not directly mount developer source trees.

## D-002 — Internet-independent runtime
- **Status:** Accepted
- **Decision:** No public internet dependency during normal development.
- **Rationale:** Explicit project requirement.
- **Consequence:** All installers, model weights, runtimes, SDKs, extensions, browser binaries, packages, docs, and authentication dependencies must be staged internally before isolation. Offline behavior must be verified with egress blocked.

## D-003 — IDE-agnostic core
- **Status:** Accepted
- **Decision:** No single IDE or extension is the architectural core. Use adapters around a stable agent/tool contract.
- **Rationale:** IDE is explicitly selectable.
- **Consequence:** Start with one reference client for the vertical slice, then validate another IDE before declaring portability.

## D-004 — Screenshot/Vision is a first-class workflow
- **Status:** Accepted
- **Decision:** Support both manual screenshot attachment and automated Playwright screenshots, routed as image content to a verified multimodal model.
- **Rationale:** UI correction must be evidence-based and usable in the main development workflow.
- **Consequence:** Phase 5 is mandatory; Vision capability and image transport are acceptance-tested. A model that only accepts text is insufficient.

## D-005 — Human approval and reversible edits
- **Status:** Accepted
- **Decision:** Separate planning from implementation; require approval before implementation and before destructive/high-impact operations. Present diffs and preserve rollback.
- **Rationale:** Protect developer work and avoid autonomous unsafe changes.
- **Consequence:** Agent client/tool broker must implement approval gates; Git status must be checked before editing.

## D-006 — Real engineering evidence
- **Status:** Accepted
- **Decision:** Build, test, browser, and screenshot results must come from actual tools and be captured as evidence.
- **Rationale:** Model-generated assertions are not proof of correctness.
- **Consequence:** Tool output and exit codes are part of completion reports.

## D-007 — Defer RAG until justified
- **Status:** Accepted
- **Decision:** Implement code/document retrieval after the first working agent/build/test/vision vertical slice.
- **Rationale:** Avoid adding indexing and vector-store complexity before measuring the baseline.
- **Consequence:** Phase 6 requires an evaluation showing retrieval benefit.

## D-008 — Capability-based model roles
- **Status:** Accepted
- **Decision:** Define Coding, Vision, Fast, and Embedding as logical roles; roles may share one model if verified capabilities and resource limits allow.
- **Rationale:** Model count should be determined by capability and hardware evidence, not by labels.
- **Consequence:** Maintain a measured compatibility matrix before final model selection.

## D-009 — DGX Spark as the target AI server
- **Status:** Accepted by user; technical inventory pending
- **Decision:** Use the user's NVIDIA DGX Spark as the target central AI server for this project.
- **Rationale:** Explicit user selection on 2026-11-09.
- **Consequence:** Phase 1 must verify current hardware, OS, storage, driver/runtime, model inventory, workload sharing, and available capacity before selecting or installing any runtime/model. Historical specs are not treated as fresh evidence.

## Open decisions (must be resolved with evidence)
- Whether DGX Spark is dedicated to CLICoding or shared with existing workloads.
- Workstation OS mix and target number of concurrent developers.
- Initial coding and vision model/runtime.
- Gateway implementation and agent/tool protocol.
- First reference IDE/client and second IDE for portability test.
- Internal authentication/TLS and Git/package service topology.
- Required latency, concurrency, context length, and retention targets.


## D-010 — Keep required models resident concurrently
- **Status:** Accepted by user; capacity validation pending
- **Decision:** Prefer a multi-model serving layout in which the required models remain resident and available simultaneously. Routine per-request sleep/load swapping is not the desired architecture.
- **Rationale:** Avoid model-load latency and make Coding and Vision capabilities available at the same time.
- **Consequence:** Model selection must optimize the combined memory footprint, not the fit of each model independently. Explicit KV-cache limits, concurrency controls, and a protected host/service reserve are mandatory. No deployment is approved until concurrent fit is measured.

## D-011 — Prefer MoE; exclude Qwen3.8-27B Dense from preferred candidates
- **Status:** Accepted user constraint
- **Decision:** Prefer suitable MoE models; do not use Qwen3.8-27B Dense as a preferred production candidate because the user expects its decode speed to be too low.
- **Rationale:** The user prioritizes practical inference performance and simultaneous multi-model availability.
- **Consequence:** Compare MoE candidates using end-to-end coding, tool-use, Vision, latency, and total resident-memory measurements.

## D-012 — One assistant identity and model-independent conversations
- **Status:** Accepted
- **Decision:** A conversation has a stable identity and canonical history independent of whichever model handles an individual turn or task. The user interacts with one assistant and should not need to restart or repeat context after model handoffs.
- **Rationale:** Transparent model switching and continuity are core product requirements.
- **Consequence:** Conversation Manager and context assembly are separate logical components from the model gateway and model runtimes.

## D-013 — Central Task Orchestrator for complex requests
- **Status:** Accepted
- **Decision:** Use a central Orchestrator to own execution plans, task decomposition, dependency graphs, sequential/parallel/hybrid scheduling, worker dispatch, validation, retries, cancellation, approvals, and final synthesis. Models are workers, not workflow owners.
- **Rationale:** Complex requests may require multiple models and workstation tools, with controlled handoffs and a coherent result.
- **Consequence:** Persist plans, task states, outputs, and evidence. Do not permit untracked free-form model-to-model delegation.

## D-014 — Capability- and resource-aware routing
- **Status:** Accepted
- **Decision:** Select model workers by tested capabilities and modalities, health/readiness, task requirements, and measured resource availability. Do not route solely by model name or use multiple models when one is sufficient.
- **Rationale:** Model roles and actual capabilities vary; concurrent models share finite unified memory and compute.
- **Consequence:** Maintain a tested capability catalog and global/per-model concurrency budgets. Queue rather than interrupt in-flight requests.

## D-015 — Shared context with task-specific assembly
- **Status:** Accepted
- **Decision:** Preserve a canonical shared conversation/event history, but assemble bounded, task-specific context for each worker. Preserve attachment references, multimodal content, dependency outputs, tool calls/results, user constraints, and approvals.
- **Rationale:** Sending identical raw history to every model is inefficient and may be incompatible with text-only workers; dropping context breaks continuity.
- **Consequence:** Vision findings must be available as structured/text output to coding workers; original images remain retrievable for later vision tasks.

## D-016 — Unified user-facing experience
- **Status:** Accepted
- **Decision:** Model handoffs and multi-model execution are transparent by default. The user sees one assistant identity, one conversation, and one coherent response stream; internal progress may be shown truthfully.
- **Rationale:** The user should not have to understand model topology to complete work.
- **Consequence:** Client integration must preserve conversation identity and streaming across worker boundaries; diagnostics belong in operator views.

## D-017 — Gateway is not the orchestrator
- **Status:** Accepted
- **Decision:** LiteLLM may be evaluated as a replaceable OpenAI-compatible gateway, but it is not the Conversation Manager, Task Planner, Orchestrator, tool broker, or resource manager.
- **Rationale:** Gateway routing/fallback does not by itself provide persistent conversation state or complex multi-worker execution semantics.
- **Consequence:** Keep stable model-independent contracts above the gateway; choose the smallest implementation stack that passes acceptance tests. LangGraph is not mandatory.

## D-018 — Thin-slice implementation and evidence-based adoption
- **Status:** Accepted
- **Decision:** Implement in thin vertical slices, beginning with persistent conversation continuity and a single-model path, then handoff, sequential/parallel orchestration, tools, and recovery. Prefer ready-made components and configuration; custom code is limited to missing orchestration semantics.
- **Rationale:** Minimize custom code and framework complexity while proving the core behavior early.
- **Consequence:** No large framework or runtime deployment is selected until a small proof-of-concept and compatibility tests demonstrate need.

