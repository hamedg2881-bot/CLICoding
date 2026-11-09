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

## Open decisions (must be resolved with evidence)
- Target server hardware/OS and whether it is dedicated to this project.
- Workstation OS mix and target number of concurrent developers.
- Initial coding and vision model/runtime.
- Gateway implementation and agent/tool protocol.
- First reference IDE/client and second IDE for portability test.
- Internal authentication/TLS and Git/package service topology.
- Required latency, concurrency, context length, and retention targets.
