# CDS_AI tool-calling transport contract

**Status:** Implemented API transport slice; workstation execution remains a separate release gate.

## Supported round-trip

1. A client sends an OpenAI-compatible `tools` list and an ordinary user message.
2. CDS_AI validates each function name and its bounded JSON Schema, then routes only to a worker explicitly registered with the `tool_calling` capability.
3. The upstream completion is parsed as either assistant text or a function tool call.
4. A model tool call is validated against the exact schemas supplied in that request. The current release slice allows one pending function call at a time.
5. CDS_AI returns the standard assistant message with `tool_calls` and `finish_reason: "tool_calls"`.
6. The client-side tool host independently checks its own allow-list, workspace scope, approval policy, and idempotency rules, executes locally, and submits a `role: "tool"` message with the matching `tool_call_id`.
7. CDS_AI verifies that the tool result matches the outstanding call ID and function name, persists it, and sends the complete tool-call/result history back to the model for continuation.

## Security boundary

- Tool definitions are client declarations, not grants of authority.
- Model-generated calls are untrusted requests. CDS_AI does not execute them.
- The workstation adapter must enforce tool allow-lists, workspace IDs, path containment, approval, idempotency, timeouts, cancellation, bounded output, and audit.
- The DGX model-serving process must never receive direct workspace mounts, shell authority, workstation credentials, or unrestricted network access.
- Do not add `tool_calling` to a production worker registry until that exact served model/runtime/parser has passed live compatibility tests.
- The current example worker registry intentionally does not enable `tool_calling`.

## Supported schema subset

The validator supports object, array, string, integer, number, and boolean types; properties; required; enum; additionalProperties; items; basic length/item bounds; and numeric minimum/maximum. Unsupported JSON Schema keywords are rejected rather than silently ignored. Function names and argument payloads are size-bounded.

## Current limitations

- One tool call per assistant turn; parallel tool calls are disabled.
- OpenAI-compatible API streaming now forwards incremental `delta.tool_calls` SSE fragments and persists the validated assembled call only after the stream completes. Tool-call streams still allow only one function call per assistant turn.
- The workstation Python reference client still uses buffered HTTP tool rounds; Open WebUI and other SSE clients can receive streamed tool-call deltas.
- Approval and execution are responsibilities of the future workstation-side tool host; no tool result should be submitted until its local policy has allowed the action.
- The model's live tool-call compatibility remains unverified on the DGX runtime.

## Release gates

Before enabling this in production, add a reference workstation adapter and tests for path traversal, symlinks, workspace isolation, approval denial, duplicate/idempotent requests, result-size limits, timeout/cancel, malicious model arguments, and actual model/runtime tool-call behavior.
