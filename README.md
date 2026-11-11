# CDS_AI — Offline AI Development Assistant

**Assistant identity:** CDS_AI. **Source repository:** CLICoding.

**Project status:** Conversation persistence, capability routing, planner-backed multi-model execution, and an OpenAI-compatible API are implemented and CI-tested. Open WebUI integration and DGX deployment validation remain pending.  
**Operating mode:** Internet unavailable during development; internal LAN available.  
**IDE policy:** IDE-agnostic.  
**Canonical project repository:** https://github.com/hamedg2881-bot/CLICoding

## Mission

Build a reliable, secure, LAN-hosted AI software-development workflow for .NET and web applications. Developers use their preferred IDE while local AI models run behind an internal gateway. The workflow must support repository understanding, planning, human-approved code changes, real builds/tests, screenshot-based visual review, and iterative UI correction — without requiring internet access at runtime.

## Non-negotiable principles

1. No runtime dependency on public internet, cloud LLM APIs, public package feeds, or external telemetry.
2. Centralized model serving over LAN; developer workspaces and execution tools remain on developer machines.
3. IDE-agnostic architecture. Integrations are adapters, not the system's core.
4. The agent must distinguish planning from execution and obtain approval for consequential actions.
5. Code changes must be inspectable as diffs and reversible through Git.
6. Build, test, browser execution, and screenshots are real tool outputs, never model claims.
7. UI visual validation is a first-class workflow: manual screenshot upload and automated Playwright capture are both required.
8. Vision input must reach a genuinely multimodal model; passing only a local file path is not image understanding.
9. Model selection, concurrency, and resource limits are controlled centrally.
10. GitHub is the canonical project record. Record decisions, implementation status, commands, test evidence, and known issues. Never commit secrets.

## Target architecture

- **Developer workstation:** IDE, agent client/adapter, project workspace, Git, .NET SDK, build/test tools, browser, Playwright.
- **Agent/tool orchestration:** discovery, planning, approvals, context assembly, tool routing, diff presentation, iterative workflow.
- **LAN AI platform:** authenticated OpenAI-compatible gateway, model registry/router, queue/resource scheduler, fast MoE reasoning/planning model, coding MoE specialist, and multimodal MoE general/vision model; optional embedding models remain future work.
- **Internal services:** Git hosting, NuGet mirror/feed/cache, documentation, project index/vector store, observability.
- **Trust boundary:** model server does not mount or directly browse developer workspaces. The workstation-side agent exposes only approved tools and scoped workspace access.

## Final delivery workflow

1. Intake — capture task, acceptance criteria, constraints.
2. Discovery — inspect solution, project instructions, dependencies, relevant files, and current Git state.
3. Planning — produce a file-level plan, risks, and validation plan; do not modify files yet.
4. Approval — developer approves or revises the plan.
5. Implementation — apply scoped edits and present diffs.
6. Engineering validation — run actual build, tests, analyzers, and relevant runtime checks.
7. Visual validation when UI is involved — run the app, capture screenshots manually or with Playwright, send image content to Vision, compare against acceptance criteria.
8. Iteration — fix evidenced issues and repeat relevant tests/screenshots, with bounded attempts.
9. Review — inspect final diff, test evidence, security and regression risks.
10. Delivery — developer approves and commits; record outcome and evidence.

## Implementation phases

See [docs/ROADMAP.md](docs/ROADMAP.md) for phases and exit gates.

0. Architecture and repository baseline.
1. Infrastructure/model capability inventory and benchmark.
2. LAN model gateway, authentication, routing, queue, health.
3. Agent integration and IDE adapters.
4. .NET engineering loop: Git, build, test, analyzers, approval.
5. Vision and screenshot workflow: manual image input, Playwright, visual iteration.
6. Project knowledge: local indexing, embeddings, retrieval.
7. Multi-user operation, quotas, resource scheduling, resilience.
8. Offline packaging, security validation, deployment and acceptance.

## Current status

- The canonical repository contains the FastAPI/SQLite Orchestrator, YAML worker registry, capability router, structured planner, dependency-wave executor, durable execution/task state, and OpenAI-compatible API.
- The public assistant identity is `CDS_AI`. Ordinary text chat bypasses Planner and routes directly to a chat-capable worker; explicit coding/review/vision or multi-step requests may use specialist orchestration. Legacy model aliases remain accepted during migration.
- Worker `max_concurrency` limits are enforced across concurrent requests within one service process.
- An optional inbound bearer key (`CLICODING_API_KEY`) protects API and diagnostic endpoints; `/health` remains public. Configure a strong key before any LAN exposure and use internal TLS.
- Direct ordinary chat supports upstream token/delta SSE streaming. Specialist task graphs still return a buffered final answer. Text and base64 image data-URI content are accepted; remote image URLs and audio/video parts are not supported. Live vision behavior remains a DGX acceptance gate.
- The intended three-model topology is documented separately and remains subject to live DGX resource/capability validation. A worker is not considered tool-capable until its exact served runtime passes tool-call tests and is explicitly configured with the `tool_calling` capability. Exact served aliases/endpoints and simultaneous resource headroom must be verified on DGX before deployment.
- The code has not yet been deployed or validated on DGX Spark. Hardware/runtime inventory, ARM64 dependency staging, service configuration, and end-to-end model compatibility are still deployment gates.
- Latest implementation and test evidence is recorded in [docs/STATUS.md](docs/STATUS.md). See [orchestrator/README.md](orchestrator/README.md) for API/run instructions, [docs/DGX_STAGING_CHECKLIST.md](docs/DGX_STAGING_CHECKLIST.md) for controlled DGX staging prerequisites, and [docs/DGX_RUNTIME_PROFILES.md](docs/DGX_RUNTIME_PROFILES.md) for the three-model runtime/memory plan. Run the read-only inventory with `bash scripts/dgx_spark_model_preflight.sh` before preparing model containers.

## Security and offline requirements

- Use an internal CA/TLS where practical; never expose model runtime ports broadly.
- Gateway is the only supported model entry point for clients.
- Restrict agent tools to the active workspace and approved commands.
- Require explicit approval for destructive commands, package installation, broad filesystem access, and Git commit/push.
- Treat MCP servers and extensions as executable code; allowlist and review them.
- Pre-stage IDE installers, extensions, SDKs, targeting packs, NuGet dependencies, browser binaries, model weights, runtimes, and documentation.
- Verify offline operation by blocking internet egress and inspecting network traffic, not by configuration claims alone.
- Keep credentials in environment variables or a secret manager; commit only sanitized examples.

## Repository maintenance protocol

For each implementation session:
1. Read this README and the current status/roadmap/decision records first.
2. Confirm the current repository branch and latest commit before writing.
3. Update the relevant design/implementation documents in the same work unit.
4. Record exact commands, environment assumptions, test results, and unresolved issues.
5. Never report a GitHub update or test as successful without tool confirmation.
6. Keep this repository as the single source of truth for this project.

## Conversation and multi-model orchestration

CDS_AI presents one assistant identity and one persistent conversation while the central Orchestrator assigns specialist work to internal model workers. Ordinary chat is direct and Planner-independent. The API supports true direct-chat SSE streaming and a validated, single-call-at-a-time OpenAI function-tool round-trip. The workstation reference adapter provides workspace-scoped read/search/write, Git status/diff, and approved .NET build/test tools. Tool execution remains on the workstation; the DGX model server receives neither workspace mounts nor shell authority.

The tool adapter is an initial Python reference library, not yet a packaged IDE extension. Conservative per-worker context admission control is implemented; automatic summarization/compaction and a context-inspection UI remain release gates. Multi-user authorization, automatic context compaction, user-facing cancellation/retry/regeneration, richer attachment lifecycle, and end-to-end IDE acceptance also remain open. OpenAI-compatible tool-call SSE deltas are implemented, while the Python workstation client still uses buffered tool rounds. See [docs/TOOL_CALLING_SUPPORT.md](docs/TOOL_CALLING_SUPPORT.md), [docs/WORKSTATION_ADAPTER.md](docs/WORKSTATION_ADAPTER.md), [docs/CONTEXT_BUDGETING.md](docs/CONTEXT_BUDGETING.md), [docs/IDENTITY_AND_ACCESS.md](docs/IDENTITY_AND_ACCESS.md), [docs/CHAT_EXPERIENCE_SPEC.md](docs/CHAT_EXPERIENCE_SPEC.md), and [docs/IDE_TOOL_PROTOCOL.md](docs/IDE_TOOL_PROTOCOL.md).

