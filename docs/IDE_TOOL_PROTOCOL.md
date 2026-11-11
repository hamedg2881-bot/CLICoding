# IDE Tool Protocol Contract

**Status:** Design contract for the next implementation slice  
**Date:** 2026-11-11

## Deployment boundary

- DGX Spark hosts inference, the Conversation Engine, model routing, durable conversation state, and specialist orchestration.
- The developer workstation hosts the IDE extension/agent and owns the checked-out workspace, file operations, terminal, build/test, browser, and screenshot tools.
- The IDE adapter is the tool host for workstation-scoped tools. It does not connect directly to model runtimes.
- The existing DGX sandbox broker remains a separate, approval-gated execution facility; it is not a substitute for workspace-aware workstation tools.

## Tool-call lifecycle

1. The IDE adapter sends the conversation transcript and only the tool definitions it is prepared to execute.
2. The Conversation Engine routes the request to a tool-capable chat model and forwards the declared schemas.
3. The model may return either ordinary assistant text or structured tool-call requests.
4. The IDE adapter validates the tool name and JSON arguments against its local allow-list and active workspace scope.
5. Read-only operations may run under the adapter's configured policy. File writes, shell commands, build/test, package operations, and other side effects require the relevant user approval policy.
6. The adapter returns a tool result with the matching call ID; the model receives that result as a tool message and may continue the conversation or request another tool.
7. The assistant reports results based on actual tool evidence, including exit codes and changed-file diffs.

## Required call contract

Each tool call has:
- unique call ID;
- registered tool name;
- JSON arguments validated against a versioned JSON Schema;
- conversation ID and originating assistant event ID;
- workspace ID (opaque, registered by the IDE adapter; never a raw arbitrary server path);
- idempotency key for operations that may be retried;
- policy classification: read-only, workspace-write, process-execution, network, or privileged;
- deadline/cancellation token.

Each result has:
- matching tool-call ID and tool name;
- status: succeeded, failed, denied, cancelled, or timed_out;
- structured JSON result;
- bounded stdout/stderr where applicable;
- exit code and duration for process/build/test tools;
- changed-file list and diff references for edits;
- evidence references (test logs, screenshots, artifact IDs);
- redaction applied before returning secrets or sensitive data.

## Security invariants

- A tool call is untrusted model output, not authorization.
- Tool names must be registered; no dynamic import, arbitrary executable, shell string, or server path may be selected by the model.
- Every path is resolved under a registered workspace root and checked against traversal and symlink escapes.
- Shell execution uses argument arrays and an explicit command policy; shell interpretation is disabled unless a separately approved tool requires it.
- Network access is denied by default in the offline development environment.
- Writes are represented as reviewable diffs before application where feasible.
- Approval is bound to an immutable operation/diff digest and expires.
- Duplicate call IDs/idempotency keys cannot repeat side effects.
- Tool execution runs on the workstation under the developer's OS identity and never grants the model process direct workspace access.

## Initial tool catalog (phased)

1. Read-only: list workspace files, inspect file, search text/symbols, inspect project/build metadata.
2. Review: propose patch/diff without applying it.
3. Approved write: apply an approved diff and report changed paths.
4. Validation: run allow-listed build/test commands and return actual exit code/output.
5. Visual: capture a local application screenshot and return an image attachment/evidence reference for vision review.

No general-purpose shell tool is enabled by default. A tool is added only with a schema, policy, tests, audit record, and explicit approval semantics.

## Acceptance tests

- unknown tool name is denied;
- malformed or oversized arguments are denied before execution;
- path traversal and symlink escape are denied;
- read-only tools cannot mutate files;
- write operations require approval bound to the exact diff digest;
- expired or replayed approval is rejected;
- duplicate tool-call delivery does not repeat a side effect;
- tool timeout/cancellation returns a typed result;
- build/test reports real exit code and bounded output;
- the model receives tool results under the matching call ID and can continue the same conversation;
- no IDE client can reach internal model endpoints directly.
