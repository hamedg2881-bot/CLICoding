# CLICoding Sandbox Runner

The root-owned runner supports eleven single-file languages plus an approved
multi-file .NET 10 workspace Build/Test mode. Both modes execute only through
an immutable, human-approved snapshot and the privileged Unix-socket broker;
model output is never executed automatically.

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

After staging the verified release ZIP under `/opt/ai/clicoding/releases/<full-commit-sha>/source/CDS_AI` (see the orchestrator deployment runbook), use that exact versioned source tree:

```bash
cd /opt/ai/clicoding/releases/<full-commit-sha>/source/CDS_AI
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
# Inspect existing identities first; create only those that are absent.
getent group clicoding-sandbox >/dev/null || sudo groupadd --system clicoding-sandbox
id cds-ai >/dev/null 2>&1 || sudo useradd --system --home-dir /var/lib/cds-ai --shell /usr/sbin/nologin cds-ai
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
sudo chmod 0600 /etc/cds-ai/sandbox-broker.env
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

### Multi-file .NET workspace mode

The same runner and broker now accept a workspace approval created by the
Orchestrator's \`/api/v1/workspace-sandbox-approvals\` API. The approval binds:
workspace ID, action (\`build\` or \`test\`), project path, every file path and
content digest, and the aggregate snapshot SHA-256. The broker revalidates the
snapshot and atomically claims the approval before invoking the runner. The
single-file API and its behavior remain backward-compatible.

Limits and policy:
- Up to 200 files, 5 MiB total UTF-8 source, and 1 MiB per file.
- Relative paths only; traversal, absolute paths, duplicate/case-colliding
  paths, symlinks, credential files and sensitive directories are rejected.
- The selected target must be an included \`.sln\`, \`.slnx\` or \`.csproj\`.
- The current workspace mode is .NET SDK 10.0.402 Build/Test only.
- The project snapshot is mounted read-only; the container copies it into its
  private writable \`/tmp\` workspace for compilation.
- Restore uses only the root-owned local feed at
  \`/var/cache/clicoding-sandbox/nuget-feed\`; no public package source or
  network is available. Missing packages cause restore to fail closed.
- Container root filesystem is read-only; no host home, Docker socket, GPU,
  network or general host workspace mount is exposed.
- The SDK image must already be present locally and is selected by a pinned
  digest. The runner uses \`--pull=never\`.

Create the offline feed directory during approved host provisioning:

\`\`\`bash
sudo install -d -o root -g root -m 0755 \\
  /var/cache/clicoding-sandbox/nuget-feed
\`\`\`

Stage only reviewed \`.nupkg\` files into this directory using the approved
offline package process. Do not place credentials or source configuration in
the feed. The directory and every file must remain root-owned and not
group/world writable; the runner rejects unsafe ownership or permissions.

### Remaining sandbox gates

- Binary workspace assets are not yet included; workspace snapshots currently
  accept UTF-8 text files only. A project containing required binary assets
  will fail snapshot creation rather than silently omit them.
- Remote Stop/cancel is routed through the broker, terminates the runner process
  group and removes the Docker container by approval label. This still requires
  live DGX race/cancellation acceptance.
- The root-owned local NuGet feed must be populated and verified for the exact
  projects being built. The runner does not download or install packages.
- No live DGX Docker execution test has been performed for this workspace mode.
- OS/container-runtime escape resistance remains defense-in-depth, not a
  mathematical isolation guarantee.
