# Execution Validation Layer — Integration Design

Status: approval persistence, Unix-socket broker, one end-to-end approved execution, replay rejection, explicit rejection enforcement, and source-digest tampering detection are verified on DGX Spark.

## Trust boundaries

1. Model workers are untrusted code producers. Their output is never executed directly.
2. The Orchestrator owns conversations, executions, task state, approvals, and validation results.
3. The Orchestrator service account must not join the Docker group and must never receive `/var/run/docker.sock`.
4. The root-owned `clicoding-sandbox-run` is a narrow execution primitive with fixed Python/JavaScript runtimes and fixed security/resource flags.
5. A separate privileged broker is the only component permitted to invoke the runner. It exposes a local Unix-domain socket, not a network listener.

## Approval and execution flow

1. A coding task completes and its output is persisted.
2. A candidate artifact and allowlisted language are selected; extraction is not execution.
3. An approval stores execution/task IDs, language, exact source snapshot, SHA-256, creation/expiry times and pending status.
4. The user reviews the exact source and explicitly approves or rejects that digest. Any source change requires a new approval.
5. The Orchestrator asks the broker to execute by approval ID only after approval.
6. The broker checks approved status, expiry and SHA-256, creates a unique execution claim, and invokes the fixed runner. It accepts no caller-supplied Docker arguments, images, mounts, environment overrides or commands.
7. The Orchestrator persists status, exit code, stdout, stderr, duration, limits and source digest.
8. Only runner success plus required acceptance checks counts as execution validation. Worker claims and printed expected output are not evidence.

## Implemented API

SQLite table: `sandbox_approvals`. Requests are bound to an existing succeeded task and exact UTF-8 source. Default expiry is 15 minutes (configurable 60–3600 seconds).

- `POST /api/v1/executions/{execution_id}/tasks/{task_id}/sandbox-approvals`: create pending approval.
- `GET /api/v1/executions/{execution_id}/tasks/{task_id}/sandbox-approvals`: list task approvals.
- `GET /api/v1/sandbox-approvals/{approval_id}`: retrieve exact source and digest.
- `POST /api/v1/sandbox-approvals/{approval_id}/decision`: approve or reject.
- `POST /api/v1/sandbox-approvals/{approval_id}/execute`: request broker execution.
- `GET /api/v1/sandbox-approvals/{approval_id}/result`: retrieve persisted result.

The API uses bearer authentication and the Orchestrator binds to loopback.

## Broker deployment

Files: `sandbox/clicoding_sandbox_broker.py`, `sandbox/clicoding-sandbox-broker.service`.

The root-owned broker reads source from SQLite, validates approval/expiry/digest, claims each approval once, runs the fixed runner and persists results. Socket: `/run/clicoding-sandbox/broker.sock`, root:`clicoding-sandbox`, mode `0660`. Orchestrator is in the dedicated socket group and has no Docker socket access.

## Verified end-to-end execution — 2026-11-10

- Execution: `b94f4b9e-314b-41b2-a12d-0c9211004c50`, task `t1`.
- Approval: `0326a3c5-a2aa-42a9-bc98-98b5e091a27f`.
- Language: Python.
- Source SHA-256: `601a4ed1cfc985e0d98113f69137ccde12b3bad41f98a01b5a4ad02d68c34eda`.
- User reviewed the exact pending source and explicitly approved it.
- Result persisted: `succeeded`, exit code `0`, stdout `{2: 2, 3: 3}\n`, empty stderr, 255 ms.
- Limits: 10-second wall time, 256 MiB memory, 1 CPU, 32 PIDs, 65536 output bytes.

This proves the functional happy path for this sample only. It is not a security audit and does not prove resistance to malicious code, container escape, kernel/runtime vulnerabilities or all failure modes.

## Verified replay protection — 2026-11-10

A second POST to the execute endpoint using the already-consumed approval returned HTTP `409` with `approval has already been consumed`. The broker rejected the replay; no second execution was started.

## Verified rejection enforcement — 2026-11-10

- Created a fresh approval for a harmless Python marker program; API returned HTTP `201`, status `pending`.
- Explicitly rejected it; decision API returned HTTP `200`, status `rejected`.
- Attempted execution afterward; API returned HTTP `409` with `Sandbox approval is not approved (status=rejected)`.
- The runner was not invoked for that rejected approval; the marker program was not executed.

This verifies that an explicitly rejected approval cannot proceed through the execution API.

## Verified source-digest tampering detection — 2026-11-10

- Created a fresh Python approval and received HTTP `201`; explicitly approved it (HTTP `200`).
- Modified only the stored `source_sha256` value in the local SQLite database to an invalid 64-character digest, leaving the source unchanged.
- Attempted execution; API returned HTTP `409` with `approved source digest mismatch`.
- The broker detected the mismatch before creating an execution claim or invoking the runner. The marker source was not executed.

This test verifies integrity checking against a modified stored digest. It does not test an attacker who can modify both the source and its digest, nor does it protect the database file from privileged host access.

## Runner scope

Python 3.12 and Node.js 24 only. Manual runner tests cover success, timeout, output limit, unsupported language, malformed JSON, non-root rejection and temporary-container cleanup. The broker now invokes this runner for approved requests.

## Remaining validation

Still open: expiry, concurrent requests, broker outage, runner failure and cleanup after broker/host interruption. Security hardening is tracked in `docs/security-hardening-backlog.md`.

## Verified approval expiry transition — 2026-11-10

- Created a pending Python approval record with a 60-second expiry for the existing succeeded task.
- Advanced its stored `expires_at` timestamp into the past in SQLite to avoid waiting for the TTL.
- Retrieved the approval through the authenticated API; the API returned status `expired` and the past expiry timestamp.
- This verifies lazy expiry transition on retrieval. It does not yet verify that the execute endpoint rejects an expired approval; that remains a separate check.

## Verified expired-approval execution rejection — 2026-11-10

- Attempted execution through the authenticated API using the approval whose expiry had been moved into the past.
- API returned HTTP `409`: `Sandbox approval is not approved (status=expired)`.
- The expired approval was rejected before broker execution; the marker source was not run.

## Verified concurrent execution claim — 2026-11-10

- Created and approved one fresh Python approval for a harmless marker program.
- Submitted two execute requests concurrently for the same approval.
- Exactly one request returned HTTP `200`, status `succeeded`, exit code `0`, stdout `CONCURRENT_TEST_OK\n` (221 ms).
- The competing request returned HTTP `409`: `approval has already been consumed`.
- This verifies one-time claim enforcement for concurrent requests to the same approval. It does not establish behavior under broader multi-approval load or host/broker interruption.

