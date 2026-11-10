# CLICoding Sandbox Runner (first slice)

This first slice is a manually invoked, root-owned launcher for Python and
JavaScript source. It is intentionally not connected to the Orchestrator yet.
A human explicitly invokes it with `sudo`; model output is never executed
automatically.

## Supported runtimes

- Python 3.12 slim Bookworm, pinned by the image digest used during initial setup.
- Node.js 24 slim Bookworm, pinned by the image digest used during initial setup.

TypeScript is not enabled yet. It needs a separately approved compiler/runtime
image and should be added only after the JavaScript runner has passed its tests.

## Install on DGX Spark

After copying this repository version to the DGX:

```bash
cd /opt/ai/clicoding/CLICoding-main
test -f sandbox/clicoding_sandbox_runner.py
sudo install -o root -g root -m 0755 \
  sandbox/clicoding_sandbox_runner.py \
  /usr/local/sbin/clicoding-sandbox-run
```

The launcher must remain root-owned. Do not add the Orchestrator service account
to the Docker group and do not expose `/var/run/docker.sock` to the Orchestrator.

## Manual smoke tests

Python:

```bash
printf '%s' '{"language":"python","code":"print(2 + 3)"}' \
  | sudo /usr/local/sbin/clicoding-sandbox-run
```

JavaScript:

```bash
printf '%s' '{"language":"javascript","code":"console.log(6 * 7)"}' \
  | sudo /usr/local/sbin/clicoding-sandbox-run
```

The response is one JSON object with `status`, `exit_code`, `stdout`,
`stderr`, `duration_ms`, and the applied resource limits.

## Current restrictions

- No network, GPU, host mounts, package installation, or arbitrary Docker flags.
- Read-only container root filesystem; only a small non-executable `/tmp`.
- Non-root container user, all Linux capabilities dropped, no-new-privileges,
  Docker's default seccomp and AppArmor profiles, PID/memory/CPU/time/output limits.
- Request and source are limited to 128 KiB; combined output is limited to 64 KiB.
- A host-side lock permits only one active sandbox execution at a time.
- The runner accepts only `python` and `javascript`.
- Runtime images must already exist locally; `--pull=never` is enforced.
- This is defense-in-depth, not a claim of perfect isolation from kernel or
  container-runtime vulnerabilities.

## Not implemented yet

- No Orchestrator API integration or automatic execution.
- No human-approval workflow UI; manual `sudo` invocation is the approval gate
  for this initial experiment.
- No multi-file projects, external packages, build systems, or independent test
  harness.
- No TypeScript, Go, Rust, Java, C/C++, C#, PHP, or Ruby runtime.

## Approved-execution broker (second slice)

The broker is a separate root-owned Unix-domain-socket service. It accepts
exactly one field, `approval_id`; it never accepts source code, commands,
runtime image names, Docker flags, mounts, or environment overrides. It reads
the immutable source snapshot from the Orchestrator SQLite database, verifies
the SHA-256 digest and approved/unexpired state, and atomically claims the
approval in `sandbox_execution_claims`. A unique primary key prevents replay.
The structured runner result is persisted in `sandbox_execution_results`.

Files:
- `sandbox/clicoding_sandbox_broker.py`
- `sandbox/clicoding-sandbox-broker.service`

### Install (do not enable until the service account and DB path are confirmed)

```bash
sudo groupadd --system clicoding-sandbox
sudo install -d -o root -g clicoding-sandbox -m 0750 /run/clicoding-sandbox
sudo install -d -o root -g root -m 0750 /etc/clicoding-sandbox
sudo install -o root -g root -m 0755 sandbox/clicoding_sandbox_broker.py /usr/local/libexec/clicoding-sandbox-broker.py
sudo install -o root -g root -m 0644 sandbox/clicoding-sandbox-broker.service /etc/systemd/system/clicoding-sandbox-broker.service
```

Create `/etc/clicoding-sandbox/broker.env` locally (never commit it):

```ini
CLICODING_DATABASE_PATH=/opt/ai/clicoding/CLICoding-main/data/clicoding.db
CLICODING_BROKER_SOCKET=/run/clicoding-sandbox/broker.sock
CLICODING_BROKER_GROUP=clicoding-sandbox
```

Then:

```bash
sudo chown root:root /etc/clicoding-sandbox/broker.env
sudo chmod 0600 /etc/clicoding-sandbox/broker.env
sudo systemctl daemon-reload
sudo systemd-analyze verify /etc/systemd/system/clicoding-sandbox-broker.service
```

The broker is deliberately not enabled or started by these instructions. Before
starting it, add only the actual Orchestrator service identity to the dedicated
`clicoding-sandbox` group and confirm the database path. Never add that user
to the Docker group. The broker's Unix socket is the only permitted execution
entry point for the Orchestrator once API integration is enabled.

