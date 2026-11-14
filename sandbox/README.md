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

## Approved-execution broker

The broker is a separate root-owned Unix-domain-socket service. It accepts
exactly one field, `approval_id`; it never accepts source code, commands,
runtime image names, Docker flags, mounts, or environment overrides. It reads
the immutable single-file source snapshot from the Orchestrator SQLite database,
verifies the SHA-256 digest and approved/unexpired state, and atomically claims
the approval in `sandbox_execution_claims`. A unique primary key prevents
replay. The structured runner result is persisted in
`sandbox_execution_results`.

Files:
- `sandbox/clicoding_sandbox_broker.py`
- `sandbox/clicoding-sandbox-broker.service`
- `sandbox/clicoding-sandbox-broker.env.example`

### Install prerequisites (staging only; do not enable automatically)

The matching Orchestrator service template runs as `cds-ai:cds-ai` and receives
the `clicoding-sandbox` supplementary group. The broker remains root-owned.
Both services must point to the same persistent database path.

```bash
sudo groupadd --system clicoding-sandbox
sudo useradd --system --home-dir /var/lib/cds-ai --shell /usr/sbin/nologin cds-ai
sudo install -d -o root -g cds-ai -m 0750 /etc/cds-ai
sudo install -d -o cds-ai -g cds-ai -m 0750 /var/lib/cds-ai
sudo install -d -o root -g root -m 0750 /etc/clicoding-sandbox
sudo install -d -o root -g root -m 0755 /usr/local/sbin
sudo install -d -o root -g root -m 0755 /usr/local/libexec
```

Install the root-owned runner and broker and the broker unit:

```bash
sudo install -o root -g root -m 0755 \
  sandbox/clicoding_sandbox_runner.py \
  /usr/local/sbin/clicoding-sandbox-run
sudo install -o root -g root -m 0755 \
  sandbox/clicoding_sandbox_broker.py \
  /usr/local/libexec/clicoding-sandbox-broker.py
sudo install -o root -g root -m 0644 \
  sandbox/clicoding-sandbox-broker.service \
  /etc/systemd/system/clicoding-sandbox-broker.service
sudo install -o root -g root -m 0640 \
  sandbox/clicoding-sandbox-broker.env.example \
  /etc/cds-ai/sandbox-broker.env
sudo systemd-analyze verify \
  /etc/systemd/system/clicoding-sandbox-broker.service
```

Review `/etc/cds-ai/sandbox-broker.env` and confirm that
`CLICODING_DATABASE_PATH` exactly matches the Orchestrator environment file.
Keep the broker environment file root-only (mode 0600 is preferred).

Before enabling the Orchestrator service, verify that its account can access
the broker socket through the service's `SupplementaryGroups=clicoding-sandbox`
setting. Do not add the Orchestrator account to the Docker group and never
expose `/var/run/docker.sock` to the Orchestrator process. The broker is the
only permitted Docker execution boundary.

### Runtime image staging

All runtime images must already exist locally; the runner enforces
`--pull=never`. The Dockerfile for TypeScript builds the local image
`clicoding-typescript:7.0.2`, but its npm installation requires package
artifacts at image-build time. For an offline DGX deployment, build/pull all
pinned Linux ARM64 images on an approved connected ARM64 staging host, export
them as a versioned Docker image archive, transfer that archive separately,
verify its SHA-256, and load it only during the explicitly approved deployment.
Do not pull images on DGX during offline staging.

### Current limitations

This broker/runner integration still supports approved **single-file snippets**
only. It does not yet accept a multi-file workspace snapshot or run project
Build/Test. The IDE adapter's current .NET build/test functions still execute
on the workstation host after approval. Do not treat this single-file runner as
the multi-file project build security boundary; that integration remains a
release blocker and must extend this same broker and runner.
