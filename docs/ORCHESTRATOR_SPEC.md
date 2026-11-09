# Conversation Router & Task Orchestrator Specification

**Status:** Accepted architecture contract; conversation persistence, deterministic capability handoff, OpenAI-compatible text API, validated plans, structured LLM Planner, SQLite execution/task persistence, and capability-routed multi-model orchestration are implemented. With `CLICODING_ENABLE_PLANNING=true`, the standard OpenAI chat route and internal conversation route use the Orchestrator behind one public assistant identity. Open WebUI conversation mapping and production hardening remain pending.
**Date:** 2026-11-09
**Scope:** Model-independent conversation continuity, single-model routing, multi-model task orchestration, tool coordination, and unified user experience.

## 1. Product contract

The user interacts with one assistant and one continuous conversation. The system may internally use one model or several models, sequentially or concurrently, and may invoke workstation tools. Model selection and handoffs must not force the user to restart, repeat context, or manually choose a model.

The Orchestrator, not any model, owns the execution plan and workflow state. Models are replaceable workers that receive bounded tasks and return results.

## 2. Architectural boundaries

### 2.1 Conversation Manager
Owns durable conversation identity and history:
- stable `conversation_id` independent of model identity;
- ordered user, assistant, tool, and model-worker events;
- attachments and references to images/files;
- task summaries and context snapshots;
- provenance: which worker/tool produced each result;
- retention and access-control metadata.

### 2.2 Planner
Converts a user request into an execution plan:
- intent, constraints, and acceptance criteria;
- tasks, required capabilities, and expected outputs;
- dependency graph;
- candidate execution mode (single, sequential, parallel, or hybrid);
- risk classification and approval gates;
- validation and completion criteria.

Planning output is a proposal. The Orchestrator validates it against policy, available capabilities, resource budgets, and user approvals before execution.

### 2.3 Orchestrator
Owns task state and execution:
- validates and persists plans;
- schedules ready tasks;
- dispatches tasks to model workers or tools;
- passes only relevant context and dependencies;
- tracks progress, retries, cancellation, deadlines, and resource reservations;
- validates task outputs and dependency completion;
- handles partial failure and replanning;
- produces one coherent assistant response.

A worker cannot silently rewrite the global plan, approve its own risky actions, or mark external work as successful without tool evidence.

### 2.4 Model Gateway
Provides a stable OpenAI-compatible interface where compatibility tests pass. LiteLLM is a candidate gateway, not the Conversation Manager or Orchestrator. The gateway handles model transport and gateway-level concerns; orchestration policy remains outside it.

### 2.5 Model Registry and Capability Catalog
Each model registration describes:
- stable model ID and served endpoint/alias;
- modalities: text, image, audio/video if supported;
- task capabilities: general reasoning, coding, vision, tool calling, structured output, review;
- context and output limits;
- tested API features and runtime version;
- latency/throughput observations;
- memory footprint and safe concurrency envelope;
- health/readiness and lifecycle state.

Capabilities must be evidenced by tests. A model name or provider claim alone is not proof.

### 2.6 Workstation Tool Broker
Owns scoped access to project files, Git, shell commands, build/test, browser, and Playwright. Model workers request tools through the broker. The model server does not mount developer workspaces. Tool outputs are recorded as evidence and become available to later tasks.

## 3. Core data contracts

These are logical contracts, not a commitment to a specific programming language or database schema.

### 3.1 Conversation
- `conversation_id`: stable opaque identifier.
- `user_id` / tenant scope.
- `created_at`, `updated_at`.
- `messages[]`: ordered conversation events.
- `active_task_ids[]`.
- `summary`: replaceable compact summary of older context.
- `attachments[]`: durable references plus metadata; binary content stored separately.
- `metadata`: client, workspace, policy, and retention references.

### 3.2 Message/Event
- `event_id`, `conversation_id`, monotonic sequence.
- `role`: user, assistant, tool, or internal worker event.
- `content`: typed text and/or multimodal content parts.
- `attachments[]`: IDs and modality metadata.
- `task_id`, `parent_event_id`, `correlation_id`.
- `producer`: assistant, model ID, or tool ID.
- `visibility`: user-visible or internal.
- `timestamp`.

Internal worker reasoning is not exposed as hidden chain-of-thought. Persist task status, concise rationale, inputs/outputs, and evidence—not private reasoning traces.

### 3.3 Task
- `task_id`, `conversation_id`, optional `parent_task_id`.
- `kind`: analysis, coding, vision, review, tool, validation, synthesis, or other registered type.
- `objective`, `constraints`, `acceptance_criteria`.
- `required_capabilities[]`.
- `input_refs[]`, `dependency_task_ids[]`.
- `assigned_worker`: model or tool; assigned by scheduler.
- `status`: proposed, blocked, ready, queued, running, waiting_approval, succeeded, failed, cancelled, or skipped.
- `attempt`, `deadline`, `resource_request`.
- `output_ref`, `evidence_refs[]`, `error`.
- `created_at`, `started_at`, `finished_at`.

### 3.4 Execution Plan
- `plan_id`, `conversation_id`, `version`.
- `goal`, `acceptance_criteria[]`.
- `tasks[]` and dependency edges.
- `execution_policy`: parallelism, retry bounds, deadline, budget.
- `approval_requirements[]`.
- `validation_tasks[]`.
- `status`: draft, awaiting_approval, approved, running, completed, failed, cancelled, or superseded.

Plans are versioned. Replanning creates a new version and preserves prior execution history.

### 3.5 Model Capability
- `model_id`, `capabilities[]`, `modalities[]`.
- `context_limit`, `output_limit`.
- `tool_calling`, `structured_output`, `streaming`.
- `tested_runtime`, `compatibility_evidence[]`.
- `resource_profile`, `concurrency_limit`, `health`.

### 3.6 Task Result
- `task_id`, `worker_id`, `status`.
- `content_ref`: textual or structured result.
- `artifacts[]`: files, patches, images, logs, test reports.
- `claims[]`: claims requiring verification.
- `evidence_refs[]`: tool outputs or source references.
- `limitations[]`, `created_at`.

## 4. Planning and execution semantics

1. Accept a user message and append it to the canonical conversation event log.
2. Determine whether the request is simple enough for one worker or requires a plan.
3. For complex work, create a dependency graph with explicit outputs and acceptance criteria per task.
4. Validate the plan: no cycles, every dependency exists, required capabilities are available, resource budget is feasible, and approval gates are present.
5. Persist the plan before dispatching work.
6. Schedule tasks whose dependencies are satisfied. Independent tasks may run concurrently within global and per-model limits.
7. Assemble task-specific context from the conversation, project context, attachments, dependency outputs, and tool evidence.
8. Dispatch to the selected worker through the gateway or tool broker.
9. Validate output shape and task acceptance criteria. Tool-dependent claims require actual tool evidence.
10. On success, persist the result and unblock dependants. On failure, apply bounded retry, alternate worker, or replan policy.
11. When all required tasks complete, run required validation and synthesis.
12. Append one user-facing assistant result to the same conversation.

Parallel execution is an optimization, not a requirement. Resource limits, fairness, and safe host reserve take precedence.

### Current implementation boundary
The source currently validates task IDs, capability declarations, dependency references, and acyclic graphs. It computes topological waves so independent tasks can share a wave. The executor bounds concurrent tasks, passes dependency outputs to later tasks, retains successful siblings, and blocks dependants when prerequisites fail. Plans and task states are persisted in SQLite before dispatch. The orchestration service selects a planning worker, validates the plan, selects workers that satisfy each task's complete capability set, records task results, and synthesizes multiple successful outputs. It is exposed through both the standard OpenAI `/v1/chat/completions` endpoint and the internal conversation route when `CLICODING_ENABLE_PLANNING=true`; with the flag disabled, the standard endpoint retains deterministic single-worker routing. This is not yet a production scheduler: no cancellation/retry, cross-process locks, resource-aware admission, multimodal payload transport, or verified Open WebUI conversation mapping.

## 5. Context continuity and multimodal handoff

The conversation is model-independent. The same full raw history is not blindly sent to every model.

For each task, Context Assembly selects:
- recent conversation turns relevant to the task;
- a maintained summary of older turns;
- user constraints and approved decisions;
- relevant source/file excerpts;
- required attachment content or durable references;
- outputs from declared dependency tasks;
- tool results and validation evidence.

Multimodal rules:
- If a task requires image understanding, dispatch it to a verified vision-capable worker and send actual image content or a supported content reference.
- A text-only worker receives a concise structured/textual Vision result, not an unusable image payload.
- If a later task must inspect the original image, the original attachment must remain retrievable and be sent to a vision-capable worker again.
- Tool calls and tool results must preserve their causal order and correlation IDs.
- Context truncation must be explicit and measurable; never silently drop required acceptance criteria, user constraints, or dependency outputs.

## 6. User experience and streaming

- One assistant identity and one conversation ID across all model handoffs.
- Stream a coherent user-facing response. Internal task events may be shown as optional progress indicators, not as separate assistant identities.
- Do not expose raw internal prompts, private model reasoning, credentials, or unfiltered worker traces.
- Model identity changes are not user-visible by default; provide diagnostics only in an operator/admin view.
- If work takes time, show truthful progress based on task state, not fabricated percentages.
- If a worker fails, continue where safe and explain only user-relevant limitations.
- A model switch must not reset history or require a new conversation.

## 7. Failure, retry, and cancellation

- Use bounded retries with timeout and backoff.
- Retry only idempotent tasks automatically. Side-effecting tool tasks require idempotency keys or explicit reconciliation before retry.
- A failed worker does not erase successful sibling results.
- If a dependency fails, mark downstream tasks blocked and decide whether to retry, substitute a worker, replan, or ask the user.
- Cancellation propagates to active model requests and cancellable tools; record tools that cannot be safely interrupted.
- Never unload or stop a model while it has active requests. Runtime lifecycle is a separate resource-manager responsibility.
- Preserve partial results and make incomplete status explicit.

## 8. Safety and approvals

- Planning and read-only discovery may run without implementation approval.
- File modifications require approval of the plan in the agreed workflow.
- Destructive commands, broad filesystem access, package installation, external network access, Git commit/push, and other consequential actions require explicit approval according to policy.
- Workers cannot approve their own actions.
- Tool broker validates workspace scope, paths, command allowlists, and arguments.
- Model output is untrusted input.
- Secrets are not placed in conversation history or model context unless strictly necessary and approved; never log or commit secrets.

## 9. Resource-aware scheduling

Scheduler inputs include:
- model readiness and capability;
- current in-flight requests and per-model limits;
- measured unified-memory use and safe headroom;
- KV-cache/context/concurrency budget;
- task priority, user fairness, deadline, and estimated cost;
- whether the task can wait without blocking unrelated work.

Initial policy:
- prefer required models resident concurrently when measured capacity permits;
- queue rather than interrupt active inference;
- do not assume per-runtime memory-utilization percentages compose safely;
- reserve memory for OS, gateway, agent, Open WebUI, workstation-side services, and build/test workloads;
- no automatic model download or deployment as a side effect of discovery;
- no model lifecycle mutation without the separately approved lifecycle policy.

## 10. API boundaries

The first implementation should expose a small stable surface:
- OpenAI-compatible chat endpoint for clients.
- Conversation/session operations for create, retrieve, append, cancel, and resume.
- Internal plan/task operations for create, approve, inspect, cancel, and retry.
- Model registry/capability read operations.
- Health/readiness and operational metrics.

Exact routes and payload schemas are to be frozen after choosing the minimal implementation stack. Do not expose internal task APIs as a requirement for ordinary chat clients.

## 11. Implementation strategy

Build in thin vertical slices:
1. Contract tests and persistent conversation/event model.
2. Single-model chat through the gateway while retaining conversation identity and history.
3. Deterministic capability-based model selection and transparent model handoff.
4. Sequential two-worker workflow with structured task results.
5. Dependency-graph execution with bounded parallel tasks.
6. Tool broker integration and evidence capture.
7. Failure recovery, cancellation, streaming, and resource-aware scheduling.
8. End-to-end tests proving continuity across model switches and multi-model workflows.

Prefer existing components and configuration for gateway, persistence, queues, and observability. Add custom code only for the orchestration and context semantics not supplied reliably by those components. Do not adopt a large framework until a small proof-of-concept demonstrates a concrete need.

## 12. Acceptance tests

The architecture is not accepted as implemented until these tests pass:

1. **Conversation continuity:** model A answers, model B is selected next, and model B receives the required prior context without a new conversation.
2. **Vision-to-code handoff:** Vision analyzes an attached screenshot; Coder uses the structured findings to make a relevant change.
3. **Sequential workflow:** task B cannot start until task A's required output is persisted and validated.
4. **Parallel workflow:** two independent analysis tasks can execute concurrently within configured limits and are combined into one answer.
5. **Tool evidence:** a model cannot claim build/test success without a corresponding successful tool result.
6. **Partial failure:** one worker fails while another succeeds; successful output is retained and the plan recovers or reports a blocker.
7. **Cancellation:** cancellation stops or safely marks active work and prevents new dependent tasks from starting.
8. **Context integrity:** required constraints, approvals, attachments, and dependency outputs survive summarization/truncation.
9. **Resource safety:** scheduler queues work rather than exceeding configured memory/concurrency budgets or interrupting in-flight inference.
10. **User experience:** the user sees one assistant and one conversation throughout all handoffs.

## 13. Explicit non-goals for the first slice

- No autonomous unrestricted agent swarm.
- No model-to-model free-form delegation without a persisted task contract.
- No mandatory LangGraph dependency.
- No RAG/vector database before demonstrated need.
- No automatic model downloading, installing, or runtime reconfiguration.
- No assumption that every request should use multiple models.
- No requirement to expose model identities to end users.

## 14. Client integration boundary

The normal user-facing client (initially Open WebUI, subject to compatibility tests) connects only to the CLICoding Orchestrator's OpenAI-compatible endpoint. It must not connect directly to individual worker runtimes or expose worker model IDs as the normal user model selector.

The Orchestrator exposes one stable assistant alias (for example, `clicoding` or `assistant`). Internal model selection and multi-worker execution happen behind that endpoint. LiteLLM, if selected, remains an internal downstream gateway. This prevents the client from owning model choice and avoids separate model-specific conversation histories.

The client remains responsible for its normal chat UI and may persist its own conversation view, but the Orchestrator's conversation/task identifiers and durable event state are the authoritative execution context. Integration tests must verify that client retries, regenerate, edit/resend, and conversation resume do not duplicate side effects or lose the canonical conversation mapping.

