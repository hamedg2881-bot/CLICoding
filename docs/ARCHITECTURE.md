# Architecture Baseline

**Status:** Proposed baseline, pending infrastructure validation.  
**Network:** Internet egress unavailable during normal operation; internal LAN available.  
**IDE:** User-selectable; no IDE may become a hard architectural dependency.

## 1. Logical components

### A. Developer workstation
- IDE of choice (Visual Studio, VS Code, Rider, or terminal workflow).
- Agent client/adapter.
- Local project workspace and Git.
- .NET SDK, compiler, analyzers, test runners.
- Local application runtime and browser.
- Playwright/browser binaries staged before isolation.
- Screenshot capture and image attachment path.

### B. Workstation-side agent and tool broker
- Receives the developer's task and project context.
- Performs discovery and builds a bounded context package.
- Calls the internal model gateway.
- Exposes scoped tools: read/search files, edit files, Git diff/status, build, test, browser, screenshot.
- Enforces approval gates and records tool evidence.
- Does not silently run destructive or broad-scope commands.
- Does not grant the model direct unrestricted shell or filesystem access.

### C. LAN AI platform
- Authenticated internal API gateway, preferably OpenAI-compatible where compatibility tests pass.
- Model registry with declared capabilities: chat, coding, tool calling, vision, embeddings.
- Router selecting a model by capability and task.
- Queue, concurrency limits, timeout/cancellation, and resource-aware scheduling.
- Coding model as the first priority.
- Vision-capable model for screenshots; may be the same multimodal model if verified.
- Optional fast model for autocomplete and embedding model for retrieval.
- Health, metrics, request audit metadata, and model lifecycle management.

### D. Internal engineering services
- Internal Git service/repository access.
- NuGet mirror or curated local package feed and prewarmed caches.
- Internal documentation and project instruction repository.
- Optional code index/vector store.
- Internal package/extension/model artifact store.

## 2. Trust boundaries

1. Developer workspace remains on the workstation.
2. The model server receives only context and images intentionally sent by the workstation agent.
3. Model runtime ports are not exposed directly to all clients; clients use the gateway.
4. Agent tools are scoped to the selected workspace and explicit allowlists.
5. Model output is untrusted input. Validate commands, paths, patches, and tool arguments.
6. MCP servers/extensions are executable components and require review and allowlisting.
7. Secrets are never sent to the model unless explicitly required and approved; secrets must not be committed to Git.

## 3. API and compatibility policy

An OpenAI-compatible API is a preferred integration contract, not proof of full compatibility. Before selecting a gateway/runtime/client combination, test:
- streaming chat responses;
- tool/function calling and multi-step tool loops;
- JSON/structured output if required;
- image input with actual image bytes/content;
- context limits and truncation behavior;
- cancellation, timeouts, and concurrent requests;
- model identity and capability routing.

Maintain a compatibility matrix in the repository with exact versions and test evidence.

## 4. Model roles

| Logical role | Required capabilities | Initial priority |
|---|---|---|
| Coding/Agent | coding quality, long context, reliable tool calling | P0 |
| Vision | multimodal image understanding, visual reasoning | P0 for UI workflow |
| Fast | low latency, coding completion | P1 |
| Embedding | local embeddings for code/docs retrieval | P2 |
| Reviewer | code review and risk analysis | P1; can initially reuse Coding |

Logical roles do not require separate concurrently loaded models. The scheduler may map multiple roles to one model when capabilities and resources permit.

## 5. Visual development architecture

Two supported image-ingress paths:
- Manual: user attaches a screenshot to the agent UI/client; image content is sent to a verified vision-capable model.
- Automated: Playwright runs on the workstation, captures configured viewport/state, and the agent sends screenshot content to Vision.

The visual loop is: run app → capture screenshot → inspect against acceptance criteria → identify concrete visual issues → propose/apply scoped changes → run tests → capture again → compare. Include desktop/mobile viewports and relevant interaction states.

Image annotations/region selection are a planned UX enhancement. The selected region coordinates and screenshot must be included in the agent context; this is not assumed to exist in the first vertical slice.

## 6. Reliability and resource management

- Queue requests rather than force-unloading models during active inference.
- Apply per-user concurrency and request limits.
- Expose model readiness and capability health.
- Support cancellation and bounded retries.
- Provide clear degraded-mode behavior if gateway/model server is unavailable.
- Local build/test/Git workflows must remain usable during AI service outages.

## 7. Offline acceptance

With public internet blocked:
- IDE and extensions start and operate.
- Model inference and tool calling work over LAN.
- Image input and screenshot workflow work.
- Build/test/restore work using local SDKs and package sources.
- No required external telemetry, update checks, auth, or cloud API calls occur.
- Egress monitoring confirms no unexpected public destinations.

## 8. Conversation Router and multi-model orchestration — accepted

The system uses a model-independent Conversation Manager and a central Task Orchestrator. The Orchestrator owns plans, task state, dependencies, scheduling, retries, cancellation, approvals, and result synthesis. Models are replaceable workers; they do not own the conversation or global workflow.

The Router supports simple single-model requests and complex requests decomposed into sequential, parallel, or hybrid tasks. It selects workers by tested capabilities, modalities, health, and resource availability. It assembles task-specific context from the shared conversation, summaries, attachments, dependency outputs, and real tool evidence.

The user sees one assistant identity, one conversation, and one coherent response stream. Model handoffs are transparent by default. Vision-to-text/coding handoffs preserve structured findings; original image content remains retrievable for later vision tasks.

See [ORCHESTRATOR_SPEC.md](ORCHESTRATOR_SPEC.md) for logical contracts, lifecycle, context rules, failure semantics, safety, resource scheduling, implementation slices, and acceptance tests.

## 9. Gateway boundary

LiteLLM is a candidate OpenAI-compatible transport/gateway layer only. It is not assumed to provide conversation memory, task decomposition, dependency-graph execution, tool governance, or resource-aware orchestration. The gateway can be replaced without changing Conversation, Task, or Execution Plan contracts.

## 10. Client-to-Orchestrator boundary

The user-facing client connects to one stable Orchestrator endpoint and one assistant alias. It must not connect directly to worker model runtimes or expose their model IDs in the normal user experience. Any LiteLLM instance is downstream/internal. The Orchestrator owns worker selection and handoffs, while client conversation identifiers are mapped to canonical Orchestrator conversation IDs.

## Conversation-first execution model (supersedes planner-first request flow)

The public assistant is conversation-first. The Conversation Engine owns canonical conversation history, identity, turn serialization, context assembly, and response persistence. A normal text turn is sent directly to the selected chat-capable model and does not invoke the Planner.

Specialist execution is an opt-in branch selected by an explicit capability/task hint or modality:
- ordinary chat: direct chat-capable model response;
- coding/review: specialist orchestration path;
- image input: vision-capable specialist path;
- multi-step work: Planner and durable task executor only when decomposition is justified.

The Planner is not the conversation manager and is never a mandatory preprocessor for every user message. It is a specialist planning component. Tool calls must be represented as validated, allow-listed requests; the host executes them only after policy and approval checks. The model never receives direct filesystem, shell, network, or workspace authority.

The same Conversation Engine and stable assistant identity serve both Open WebUI and IDE adapters. IDE clients may provide an explicit task/capability hint, but must not address worker endpoints directly. Workstation-side tools remain on the developer workstation; the DGX hosts model inference and shared orchestration services.

### Required invariants

1. A plain-text chat turn must succeed without Planner availability.
2. A specialist route must be selected only from validated task hints, modality, or a separately tested routing decision.
3. Conversation history is persisted independently of the model/worker that produced each turn.
4. Invalid planner output cannot enter execution or corrupt the conversation event history.
5. Tool execution is allow-listed, workspace-scoped, auditable, and approval-gated where required.
6. OpenAI-compatible chat transport and IDE adapters share the same conversation and policy contracts.
7. Streaming must represent actual incremental output before it is described as token streaming; buffered SSE is not token streaming.

### Migration rule

Do not remove the existing durable planner/executor, approval, or sandbox capabilities. Move them behind the specialist/tool-execution boundary. Migrate in small vertical slices with tests proving direct chat, specialist routing, context continuity, tool safety, and IDE compatibility. No DGX deployment is implied by a GitHub change.
