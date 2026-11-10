# CLICoding Sandbox Runner

The root-owned runner supports eleven single-file languages. Execution is
available only through an immutable, human-approved source snapshot and the
privileged Unix-socket broker; model output is never executed automatically.

## Supported runtimes

| API language | Runtime/compiler image | Execution mode |
|---|---|---|
| python | Python 3.12.16 | Interpreter |
| javascript | Node.js 24.22.2 | Interpreter |
| typescript | TypeScript 7.0.2 + Node.js 24 | Compile then run |
| go | Go 1.27.3 | `go run` (standard library only) |
| rust | Rust 1.99.1 | `rustc` then run |
| java | Eclipse Temurin JDK 25.0.5 | `javac` then run `Main` |
| c | GCC 16.2.0 | C23 compile then run |
| cpp | G++ 16.2.0 | C++23 compile then run |
| csharp | .NET SDK 10.0.402 | Offline SDK build then run |
| php | PHP 8.5.12 CLI | Interpreter |
| ruby | Ruby 4.0.9 | Interpreter |

All images are Linux ARM64 and must already exist locally. TypeScript uses the
separately built local image `clicoding-typescript:7.0.2`.

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
- The runner accepts only the eleven language identifiers in the table above.
- Runtime images must already exist locally; `--pull=never` is enforced.
- This is defense-in-depth, not a claim of perfect isolation from kernel or
  container-runtime vulnerabilities.

## Current scope limitations

- Single-file source only; Java source must declare `public class Main`.
- Go, Rust, TypeScript, C/C++, Java, and C# compile in the isolated container.
- No external package downloads, network access, GPU, host mounts, or multi-file projects.
- No independent test harness or package restore from external registries.
- Human approval remains mandatory for the exact source digest.

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

