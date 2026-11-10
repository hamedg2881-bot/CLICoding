# Execution Validation Layer — Integration Design

Status: approval persistence, Unix-socket broker, and one end-to-end approved execution smoke test are implemented and verified on DGX Spark.

## Trust boundaries

1. Model workers are untrusted code producers. Their output is never executed directly.
2. The Orchestrator remains the owner of conversations, executions, task state, approval state, and validation results.
3. The Orchestrator service account must not join the Docker group and must never receive `/var/run/docker.sock`.
4. The root-owned `clicoding-sandbox-run` is a narrow execution primitive. It accepts only the fixed Python/JavaScript runtime allowlist and fixed resource/security flags.
5. A separate privileged broker is the only component permitted to invoke the runner. It exposes a local Unix-domain socket, not a network listener.

## Required approval flow

1. A coding task completes and its output is persisted.
2. A candidate code artifact is selected and its allowlisted language determined. Extraction is not execution.
3. An approval request stores execution ID, task ID, language, exact source snapshot, SHA-256 digest, creation time, expiry, and `pending` status.
4. The user reviews the exact source and explicitly approves or rejects that exact digest. Any code change requires a new approval.
5. The Orchestrator asks the broker to execute by approval ID only after the approval is approved.
6. The broker reads the immutable source snapshot from SQLite, verifies approved status, expiry and SHA-256, and creates a unique execution claim before invoking the fixed runner. It accepts no Docker arguments, image names, mounts, environment overrides, or arbitrary commands.
7. The Orchestrator persists the runner's structured result: status, exit code, stdout, stderr, duration, limits, and source digest.
8. A task is execution-validated only when the runner returns `succeeded` and the required acceptance checks pass. Worker claims and printed expected output are never execution evidence.

## Implemented approval persistence/API

The Orchestrator persists approval requests in SQLite in `sandbox_approvals`.
Each request is bound to an existing succeeded execution task, an allowlisted language,
the exact UTF-8 source snapshot, and its SHA-256 digest. Requests expire after 15 minutes
by default (configurable between 60 and 3600 seconds).

Local API routes:
- `POST /api/v1/executions/{execution_id}/tasks/{task_id}/sandbox-approvals`
  creates a pending request with JSON `{"language":"python","source":"..."}`.
- `GET /api/v1/executions/{execution_id}/tasks/{task_id}/sandbox-approvals`
  lists requests for the task.
- `GET /api/v1/sandbox-approvals/{approval_id}` returns the exact source and digest
  for human review.
- `POST /api/v1/sandbox-approvals/{approval_id}/decision` accepts
  `{"decision":"approve"}` or `{"decision":"reject"}`.
- `POST /api/v1/sandbox-approvals/{approval_id}/execute` requests broker execution.
- `GET /api/v1/sandbox-approvals/{approval_id}/result` retrieves the persisted result.

The API is protected by the configured bearer key and the service is bound to loopback.

## Broker implementation and deployment

Implemented files:
- `sandbox/clicoding_sandbox_broker.py`
- `sandbox/clicoding-sandbox-broker.service`

The broker accepts newline-delimited JSON containing exactly `approval_id`. It reads source
from the existing `sandbox_approvals` row, checks approved status and expiry, verifies
SHA-256 with constant-time comparison, and creates a unique `sandbox_execution_claims`
row before execution. Duplicate claims are rejected. Results are written to
`sandbox_execution_results`.

The broker is installed and running on the DGX Spark as a root-owned systemd service.
Its Unix socket is `/run/clicoding-sandbox/broker.sock`, owned by root and the dedicated
`clicoding-sandbox` group, with mode `0660`. The Orchestrator service account is a member
of that group. The Orchestrator has no Docker socket access.

## Verified end-to-end smoke test — 2026-11-10

Execution/task:
- Execution ID: `b94f4b9e-314b-41b2-a12d-0c9211004c50`
- Task ID: `t1`
- Approval ID: `0326a3c5-a2aa-42a9-bc98-98b5e091a27f`
- Language: Python
- Source SHA-256: `601a4ed1cfc985e0d98113f69137ccde12b3bad41f98a01b5a4ad02d68c34eda`

The exact source was retrieved and reviewed while the approval was pending. The user then
explicitly approved it. The broker executed that immutable source snapshot through the
sandbox runner. The API returned and SQLite persisted:

- status: `succeeded`
- exit_code: `0`
- stdout: `{2: 2, 3: 3}\n`
- stderr: empty
- duration_ms: `255`
- limits: wall time 10 seconds, memory 256 MiB, CPU 1, PIDs 32, output 65536 bytes.

This verifies the functional happy path from worker-produced artifact through human
approval, broker, sandbox, and persisted result. It does **not** prove resistance to
malicious code, container escape, kernel/runtime vulnerabilities, or all failure modes.
Do not treat this smoke test as a security audit or production-hardening sign-off.

## Runner behavior and current scope

The standalone runner supports Python 3.12 and Node.js 24 only. It has been manually tested
for successful execution, timeout, output limiting, unsupported-language rejection,
malformed-JSON rejection, non-root rejection, and temporary-container cleanup. It is now
invoked by the broker for the approved execution path.

## Remaining validation

Still to be tested end-to-end: rejection path, expiry, digest mismatch/tampering, replay,
concurrent requests, broker outage, runner failure, and cleanup after broker/host interruption.
Security hardening is tracked separately in `docs/security-hardening-backlog.md`.
