# Execution Validation Layer — Integration Design

Status: approval persistence and Unix-socket broker implemented; rollout remains disabled until host installation and tests pass.

## Trust boundaries

1. Model workers are untrusted code producers. Their output is never executed directly.
2. The Orchestrator remains the owner of conversations, executions, task state, approval state, and validation results.
3. The Orchestrator service account must not join the Docker group and must never receive `/var/run/docker.sock`.
4. The existing root-owned `clicoding-sandbox-run` remains a narrow execution primitive. It accepts only the fixed Python/JavaScript runtime allowlist and fixed resource/security flags.
5. A separate privileged broker is the only component permitted to invoke the runner. It exposes a local Unix-domain socket, not a network listener.

## Required approval flow

1. A coding task completes and its output is persisted.
2. The Orchestrator extracts one candidate code artifact and determines its allowlisted language. Extraction is not execution.
3. The Orchestrator stores an approval request containing execution ID, task ID, language, exact source snapshot, SHA-256 digest, creation time, expiry, and status `pending`.
4. The user reviews the exact source and explicitly approves or rejects that exact digest. Any code change invalidates the approval and requires a new approval.
5. Only after approval, the Orchestrator issues a short-lived, single-use execution ticket bound to execution ID, task ID, language, source digest, and nonce.
6. The broker verifies the ticket and source digest, rejects expired/replayed tickets, and invokes the fixed runner. It does not accept Docker arguments, image names, mounts, environment overrides, or arbitrary commands.
7. The Orchestrator persists the runner's structured result: status, exit code, stdout, stderr, duration, limits, and source digest.
8. A task is execution-validated only when the runner returns `succeeded` and the required acceptance checks pass. Worker claims and printed expected output are never treated as execution evidence.

## Implemented approval persistence/API slice

The Orchestrator now persists approval requests in SQLite in `sandbox_approvals`.
Each request is bound to an existing succeeded execution task, an allowlisted language,
the exact UTF-8 source snapshot, and its SHA-256 digest. Requests expire after 15 minutes
by default (configurable in the repository method between 60 and 3600 seconds).

Local API routes:
- `POST /api/v1/executions/{execution_id}/tasks/{task_id}/sandbox-approvals`
  creates a pending request with JSON `{"language":"python","source":"..."}`.
- `GET /api/v1/executions/{execution_id}/tasks/{task_id}/sandbox-approvals`
  lists requests for the task.
- `GET /api/v1/sandbox-approvals/{approval_id}` returns the exact source and digest
  for human review.
- `POST /api/v1/sandbox-approvals/{approval_id}/decision` accepts
  `{"decision":"approve"}` or `{"decision":"reject"}`. A decision is single-use;
  expired or already-decided requests cannot be changed.

This API only records human decisions. It does not execute code, issue broker tickets,
or grant the Orchestrator Docker access. Keep the service bound to loopback while no
authenticated approval UI is available.

## Broker implementation slice

Implemented files:
- `sandbox/clicoding_sandbox_broker.py`
- `sandbox/clicoding-sandbox-broker.service`

The broker accepts newline-delimited JSON containing exactly `approval_id`. It reads source from the existing `sandbox_approvals` row, checks `approved` status and expiry, verifies SHA-256 with constant-time comparison, and creates a unique `sandbox_execution_claims` row before execution. Duplicate claims are rejected. Results are written to `sandbox_execution_results`. The Orchestrator now has `POST /api/v1/sandbox-approvals/{approval_id}/execute` and `GET /api/v1/sandbox-approvals/{approval_id}/result`; execution returns 503 if the socket is unavailable and 409 if the approval is not approved or has already been consumed.

The broker service is not installed, enabled, or started automatically. It requires a dedicated `clicoding-sandbox` group, a local environment file with the absolute database path, and a verified service-account socket permission setup. Keep it disabled until the operator confirms the Orchestrator service identity and completes the broker smoke tests.

## Broker requirements

- Root-owned systemd service; dedicated Unix socket under `/run/clicoding-sandbox/`.
- Socket access granted only to the Orchestrator service identity through a dedicated group; never grant Docker group membership.
- No TCP listener, no Docker socket mount into the Orchestrator, no caller-controlled Docker options.
- Verify a short-lived single-use ticket and exact source SHA-256 before execution.
- Enforce one active run globally, matching the current project concurrency limit.
- Fixed maximum request size, wall time, memory, CPU, process count, and captured output.
- Audit every request and decision without logging secrets or full source by default.
- Fail closed on missing approval, invalid signature, digest mismatch, expiry, replay, malformed input, or broker unavailability.

## Approval state machine

`pending -> approved -> consumed`

`pending -> rejected`

`pending -> expired`

An approval is immutable and bound to one source digest. Replacing source creates a new pending approval; it never edits an approved request in place.

## Validation status semantics

- `not_requested`: no execution requested.
- `pending_approval`: waiting for the user.
- `rejected`: user declined or policy rejected it.
- `running`: broker accepted a valid single-use ticket and execution started.
- `succeeded`: process exit code is zero and required checks passed.
- `failed`: process ran but returned non-zero or acceptance checks failed.
- `timeout`, `output_limit`, `runtime_error`: distinct runner outcomes; none count as success.

## Rollout sequence

1. Install the root-owned broker and systemd unit, but do not enable it until the socket group and Orchestrator service identity are confirmed.
2. Verify approval, rejection, digest mismatch, expiry, replay, timeout, output limit, broker outage, and cleanup.
3. Enable the broker only after all tests pass. Do not change model services or connect Open WebUI as part of this work.

## Current verified prototype limits

The standalone runner currently supports Python 3.12 and Node.js 24 only. It has been manually tested on the DGX Spark for successful execution, timeout, output limiting, unsupported-language rejection, malformed-JSON rejection, non-root rejection, and temporary-container cleanup. It is not yet connected to the Orchestrator and is not an absolute security boundary against kernel or container-runtime vulnerabilities.
