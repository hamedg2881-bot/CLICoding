# Chat Experience and Interaction Contract

**Status:** Target contract for the Conversation-First implementation  
**Date:** 2026-11-14

## Product objective

CLICoding must feel like a high-quality modern AI assistant in Open WebUI and supported IDEs, while using locally hosted models and a workstation-side execution boundary. The product should adopt proven public interaction patterns from leading assistants without claiming access to their proprietary internals.

## Core interaction requirements

### 1. Natural conversation
- One stable assistant identity; internal model handoffs are invisible unless useful to the user.
- Durable conversation IDs and ordered message/event history.
- Follow-up turns use prior context without requiring the client to resend the entire transcript.
- Context is assembled within the selected model's tested context budget; old turns are compacted or summarized without silently dropping recent instructions.
- System/developer instructions are applied with explicit precedence and are never confused with user messages.
- Conversation isolation by authenticated user/tenant; no cross-user history access.

### 2. Responsive generation
- Real upstream token/delta streaming for direct chat; never label buffered output as token streaming.
- OpenAI-compatible SSE framing, stable response ID, role delta, content deltas, finish reason, and terminal [DONE].
- Client disconnect cancels upstream iteration and releases the runtime semaphore.
- A partial/cancelled answer is not persisted as a completed assistant turn.
- Timeouts, upstream errors, and malformed streams produce controlled errors and diagnostic correlation IDs without exposing prompts or secrets.
- Specialist workflows may report progress events; their final answer may remain buffered until a task graph completes, and must be identified honestly.

### 3. Tool use
- Tool definitions and calls are typed, schema-validated, and scoped to a client/workspace.
- Model output is a request, never authority. The tool host validates allow-list, arguments, workspace boundaries, risk, and approval before execution.
- Tool results are recorded as tool events and returned to the model in a bounded tool loop.
- Tool-call IDs, ordering, idempotency, timeouts, cancellation, and error results are explicit.
- No direct shell, filesystem, network, or credential access from model runtimes.
- The same logical tool contract is available to IDE adapters; workstation tools execute on the workstation, not on the DGX.

### 4. Multimodal interaction
- Typed attachment references and content parts; binary payloads are not copied into unbounded conversation text.
- Images are supported first; audio/video are added only with explicit model capability tests and lifecycle/resource controls.
- Attachment size, MIME type, decoding, retention, and access are validated.
- Vision handoff preserves nearby textual context and attachment references.

### 5. User control
- Stop generation and cancel active tool/task execution.
- Retry failed generation without duplicating completed side effects.
- Regenerate and edit/resubmit user turns with clear event lineage.
- Surface task progress and approval requests for specialist work.
- Keep the transcript readable; do not expose hidden model reasoning. Show concise status, tool activity, results, and evidence instead.

### 6. IDE parity
- One shared Conversation Engine and policy layer for Open WebUI and IDE adapters.
- IDE requests can include repository/workspace context and explicit task hints.
- The assistant can inspect, plan, propose diffs, request approval, apply changes through workstation tools, run build/tests, and report real exit-code evidence.
- Workspace context is explicit and never inferred from an arbitrary server path.
- IDE adapters never connect directly to internal model worker endpoints.

## Compatibility contract

The first public adapter remains OpenAI Chat Completions compatible for common clients. Compatibility must be tested against actual client behavior, not inferred from endpoint names. Required next additions include tool-call message round-trips, cancellation, request option forwarding, and clear streaming semantics. A future Responses-style event API may be added as a richer native interface without breaking the Chat Completions adapter.

## Implementation status (2026-11-14)

- Implemented: conversation-first direct chat routing; explicit specialist routing; persistent conversation history; Open WebUI identity mapping; actual upstream SSE token streaming for direct chat; completed-turn persistence only after successful stream completion.
- Implemented: specialist plan/task lifecycle events are streamed as custom `event: progress` SSE frames with safe status metadata; final specialist answer is sent after orchestration completes and is not represented as token-by-token streaming.
- Implemented: backend tool-call round-trip and safe workstation tool broker, context-window budgeting/compaction, regenerate/edit lineage, typed image attachment storage, and IDE reference adapter.
- Not yet accepted: actual Open WebUI rendering of the custom `x_clicoding.progress` event metadata, multi-turn/SSE/Stop/regenerate/edit behavior against supported clients, and real-model/DGX acceptance. These require acceptance tests against the actual client and runtime; source-level API tests are not a substitute.
- Not yet implemented: automatic attachment expiry/retention, image decoding/content validation and malware scanning; packaged IDE UI and OS-level process sandboxing.

## Release gates

A release is not complete until tests cover:
- multi-turn direct chat and worker handoff;
- real token delta ordering and [DONE];
- upstream stream failure and disconnect/cancellation cleanup;
- no completed assistant event on interrupted output;
- context budget/compaction behavior;
- tool-call round-trip, invalid arguments, denied tools, approval, duplicate call IDs, and tool failure;
- multimodal capability and attachment validation;
- Open WebUI and at least one reference IDE end-to-end.


## Shared assistant policy

The implementation applies one internal CDS_AI system policy to all conversation paths after client-provided system messages. It defines the stable assistant identity, same-language response behavior, Persian RTL preservation, honest action reporting, untrusted file/tool-result handling, and registered-tool-only execution. Client system prompts may add task context but cannot override the core CDS_AI policy.
