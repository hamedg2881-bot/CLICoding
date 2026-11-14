# DGX Spark — controlled staging checklist

**Status:** Staging instructions only. This document does not authorize installation, service changes, model changes, enabling workers, or runtime restarts.

**Transfer policy:** Download a checksummed ZIP artifact from GitHub Actions on the exact reviewed commit and transfer it with WinSCP or another approved LAN file-transfer method. The DGX host does not need Git and must not use Git commands.

## 1. Select and download the exact source artifact

1. Open the Draft PR for the reviewed candidate: https://github.com/hamedg2881-bot/CLICoding/pull/14
2. Confirm the PR is still open and Draft. Record its exact head commit SHA.
3. Open the successful **push** GitHub Actions run whose head SHA exactly equals the PR head. Do not use the `pull_request` run artifact: GitHub tests that event on a synthetic merge commit. Download the source artifact named `CDS_AI-release-<full-commit-sha>` from the matching push run.
4. The downloaded artifact contains:
   - `CDS_AI_<12-char-sha>.zip` — source archive;
   - matching `.zip.sha256` — SHA-256 checksum;
   - `RELEASE_INFO.txt` — full source revision and archive filename.
5. Do not use a GitHub branch ZIP, a stale artifact from another run, or a ZIP whose commit SHA does not match the reviewed PR head.

The CI workflow creates the archive from the exact tested commit and publishes it only after its test job succeeds. The artifact is retained for 30 days; download and preserve it in the approved release store before expiry.

## 2. Transfer and verify on DGX (read-only staging)

Transfer the three artifact files to a new, version-specific staging directory, for example `/opt/ai/clicoding/releases/<full-commit-sha>/`. Do not overwrite the current application directory.

On DGX:

```bash
cd /opt/ai/clicoding/releases/<full-commit-sha>
sha256sum -c CDS_AI_<12-char-sha>.zip.sha256
unzip -t CDS_AI_<12-char-sha>.zip
cat RELEASE_INFO.txt
```

Confirm that:
- the checksum reports `OK`;
- ZIP integrity validation succeeds;
- the full revision in `RELEASE_INFO.txt` exactly matches the reviewed PR head;
- the archive contains the expected `CDS_AI/` root directory.

Extract only into a new, empty staging directory:

```bash
mkdir source
unzip -q CDS_AI_<12-char-sha>.zip -d source
test -f source/CDS_AI/pyproject.toml
test -f source/CDS_AI/orchestrator/README.md
```

Do not copy files over a live installation during staging.

## 3. Read-only DGX preflight

Before creating a virtual environment, installing packages, changing a service, or touching model runtimes, record:

```bash
uname -a
uname -m
python3 --version
python3 -m pip --version
free -h
df -h
nvidia-smi
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
```

Confirm ARM64 architecture, Python 3.11+, disk location for SQLite and attachments, available host memory, Docker availability for the existing sandbox, and the actual OpenAI-compatible worker endpoints. Do not infer worker ports or model aliases from an example file or old notes.

## 4. Offline dependency preparation (not yet an install command)

1. From that same successful **push** Actions run and exact source commit, download the artifact named `CDS_AI-wheelhouse-cp312-arm64-<full-commit-sha>`.
2. Transfer its `.tar.gz` and matching `.tar.gz.sha256` sidecar to the DGX staging directory.
3. Verify and extract:

```bash
sha256sum -c CDS_AI-wheelhouse-cp312-arm64-<12-char-sha>.tar.gz.sha256
tar -xzf CDS_AI-wheelhouse-cp312-arm64-<12-char-sha>.tar.gz
```

4. Inspect `wheelhouse-manifest.json`: the Python implementation/version and `aarch64` machine must match the DGX target. Verify every wheel against its SHA-256 entry before installation.
5. Keep the wheelhouse, virtual environment, secrets and runtime data outside the source tree.
6. Install only with pip offline mode and an explicit local wheelhouse; public package indexes must not be fallback sources.

The CI workflow now builds the complete project plus test-dependency wheelhouse on a native ARM64 runner using CPython 3.12.3. The last passing packaging run produced 32 wheels for aarch64. This is a reproducible candidate wheelhouse, not proof of compatibility with the exact DGX OS/glibc/runtime. Confirm the DGX Python minor version and platform first; do not use this artifact if they differ. No offline install on DGX has been performed yet.

## 5. Initial safe runtime configuration (after source and dependency gates)

- Start with `CLICODING_ENABLE_PLANNING=false`.
- Use an absolute persistent `CLICODING_DATABASE_PATH` and attachment storage path outside the source tree.
- Copy `config/workers.example.yaml` to a private configuration file outside Git. Replace every sample endpoint and model alias only after a read-only live preflight confirms them.
- The example worker entries are disabled. Do not enable a worker merely because its entry exists.
- Keep worker concurrency at one until measured on the actual runtime and memory headroom is accepted.
- Supply worker credentials only through protected environment variables. Never commit or include secrets in the transfer ZIP.
- Generate a random inbound secret of at least 32 ASCII characters and configure `CLICODING_API_KEY` before any network exposure.
- Initially bind Uvicorn to `127.0.0.1`; use one process until live supervisor and multi-process acceptance is complete.
- Do not connect Open WebUI until client acceptance validates conversation-ID persistence, SSE, Stop, regenerate/edit and the progress-event behavior.

## 6. Acceptance order

1. Verify archive checksum, source revision and extracted file inventory.
2. Verify the offline wheelhouse against the exact DGX Python/ARM64 target.
3. Create an isolated candidate environment without replacing any current environment.
4. Install from the wheelhouse with network access disabled.
5. Run the full test suite and record the exact command, result and environment.
6. Start a candidate instance bound only to localhost and using a new, isolated test database and attachment directory.
7. Verify `GET /health`, authenticated `GET /v1/models`, direct chat, conversation persistence, SSE completion, cancellation, regenerate/edit and tool-call behavior.
8. Validate each actual worker endpoint independently; test tool calling only if the exact model/runtime/parser supports it.
9. Test backup, restore to a separate candidate path, and rollback without touching production data.
10. Complete Open WebUI/client, sandbox and real-model quality acceptance before any production cutover.

## 7. Explicit release blockers

The source candidate is not yet a production-ready DGX installation package. At minimum, the following remain open and must be closed with evidence:

- Exact DGX ARM64/Python wheelhouse and a clean offline install.
- A reviewed Orchestrator systemd service template, secret/environment file layout, filesystem ownership and health/restart policy.
- Existing Docker sandbox runtime images available locally, including the TypeScript image, plus a safe image inventory/checksum procedure.
- Multi-file workspace snapshot build/test integration through the existing approval broker and Docker runner. The current runner supports approved single-file snippets only; workstation .NET build/test still runs on the workstation host.
- Packaged IDE integration and approval/diff/apply UI.
- Attachment expiry/retention, robust image decoding/content validation and malware scanning.
- Audit retention/signing/alerting and internal TLS/reverse-proxy verification.
- Live multi-process/supervisor acceptance; retain the conservative single-worker configuration until then.
- Real-model Persian/English quality, tool-call, vision, context, latency, cancellation, concurrency and unified-memory tests.
- Open WebUI acceptance, including custom `x_clicoding.progress` event rendering or a documented fallback.
- Versioned database/config backup, migration-on-copy and tested rollback.
- Final operator runbook with exact install, health-check, upgrade, backup and rollback commands.

## 8. Cutover authorization

Staging is not installation. Do not replace the current application, modify systemd units, change model containers, enable the broker, enable workers, or restart any service until:
- all source and offline-install gates are closed;
- the exact artifact SHA and test evidence are recorded;
- a separate deployment plan and rollback point are reviewed; and
- the user explicitly approves the deployment step.

Record the deployed commit, archive SHA-256, dependency inventory, configuration revision (never secret values), test results, changed paths, service actions and rollback point.
