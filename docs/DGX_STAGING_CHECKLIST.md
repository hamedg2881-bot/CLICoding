# DGX Spark — controlled staging checklist

**Status:** Staging instructions only. This document does not authorize installation, service changes, model changes, enabling workers, or runtime restarts.

**Transfer policy:** Download the checksummed transfer-bundle artifact from GitHub Actions on the exact reviewed commit and transfer it with WinSCP or another approved LAN file-transfer method. The DGX host does not need Git and must not use Git commands.

## 1. Select and download the exact transfer artifact

1. Open the Draft PR for the reviewed candidate: https://github.com/hamedg2881-bot/CLICoding/pull/14
2. Confirm the PR is still open and Draft. Record its exact head commit SHA.
3. Open the successful **push** GitHub Actions run whose head SHA exactly equals the PR head. Do not use the \`pull_request\` run artifact: GitHub tests that event on a synthetic merge commit. Download the single artifact named \`CDS_AI-transfer-bundle-<full-commit-sha>\` from the matching push run.
4. After extracting the GitHub artifact download, it contains:
   - \`CDS_AI-transfer-bundle-<12-char-sha>.tar.gz\`;
   - matching \`.tar.gz.sha256\` checksum.
5. Do not use a GitHub branch ZIP, a stale artifact from another run, or an artifact whose commit SHA does not match the reviewed PR head.

The transfer bundle contains the exact source ZIP, its checksum and release information, plus the CPython 3.12 ARM64 wheelhouse archive and its checksum. The CI workflow publishes it only after both the test job and ARM64 wheelhouse job succeed. The artifact is retained for 30 days; download and preserve it in the approved release store before expiry.

## 2. Transfer and verify on DGX (read-only staging)

Transfer the bundle tarball and its checksum to a new, version-specific staging directory, for example \`/opt/ai/clicoding/releases/<full-commit-sha>/incoming/\`. Do not overwrite the current application directory.

On DGX:

\`\`\`bash
release_root=/opt/ai/clicoding/releases/<full-commit-sha>
sudo install -d -o "$USER" -g "$(id -gn)" -m 0750 "$release_root/incoming"
cd "$release_root/incoming"
sha256sum -c CDS_AI-transfer-bundle-<12-char-sha>.tar.gz.sha256
mkdir bundle
tar -xzf CDS_AI-transfer-bundle-<12-char-sha>.tar.gz -C bundle
cd bundle
cat TRANSFER_MANIFEST.txt
sha256sum -c CDS_AI_<12-char-sha>.zip.sha256
sha256sum -c CDS_AI-wheelhouse-cp312-arm64-<12-char-sha>.tar.gz.sha256
unzip -t CDS_AI_<12-char-sha>.zip
\`\`\`

Confirm that the full commit in \`TRANSFER_MANIFEST.txt\` matches the reviewed PR head and that both inner checksums report \`OK\`. Extract the source and wheelhouse into the versioned release root:

\`\`\`bash
sudo mkdir -p "$release_root/source"
sudo unzip -q CDS_AI_<12-char-sha>.zip -d "$release_root/source"
sudo tar -xzf CDS_AI-wheelhouse-cp312-arm64-<12-char-sha>.tar.gz -C "$release_root"
test -f "$release_root/source/CDS_AI/pyproject.toml"
test -f "$release_root/source/CDS_AI/orchestrator/README.md"
\`\`\`

Do not copy files over a live source directory. Preserve the incoming bundle, its checksum, the inner archives, their checksums, release information and extracted files together.

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

## 4. Offline dependency preparation (no installation yet)

The wheelhouse archive is already included in the transfer bundle extracted in Section 2. Verify it against the packaged script before creating any virtual environment:

\`\`\`bash
python3 "$release_root/source/CDS_AI/scripts/verify_wheelhouse.py" \
  --wheelhouse "$release_root/wheelhouse" \
  --manifest "$release_root/wheelhouse-manifest.json"
\`\`\`

The verifier checks the Python major/minor ABI, ARM64 architecture, exact wheel inventory, file sizes and every wheel SHA-256. It must succeed before installation. Keep the wheelhouse, virtual environments, secrets and runtime data outside the source tree, and use pip offline mode with an explicit local wheelhouse only; public package indexes must never be fallback sources.

The CI workflow builds the complete project plus test-dependency wheelhouse on a native ARM64 runner using CPython 3.12.3. The latest completed packaging run produced 32 wheels for aarch64. This is a reproducible candidate wheelhouse, not proof of compatibility with the exact DGX OS/glibc/runtime. Confirm the DGX Python major/minor version and platform first; do not use the artifact if they differ. No offline install on DGX has been performed.

## 5. Offline Docker sandbox image bundle

The sandbox requires the exact pinned ARM64 images in `sandbox/clicoding_sandbox_runner.py`, including the locally built TypeScript image. Do not pull these images on the DGX during offline deployment.

On an internet-connected staging workstation with Docker and Buildx (native ARM64 is preferred; Docker Desktop must support `linux/arm64`), extract the transfer bundle, then extract its source archive and run:

```bash
mkdir -p source
unzip -q CDS_AI_<12-char-sha>.zip -d source
python source/CDS_AI/scripts/export_sandbox_images.py \
  --pull \
  --build-typescript \
  --output dist/clicoding-sandbox-images-arm64.tar
```

This pulls the pinned upstream images for ARM64, builds `clicoding-typescript:7.0.2` from the repository Dockerfile and pinned Node base image, tags every image under a transfer-local name, and writes:
- `clicoding-sandbox-images-arm64.tar`
- `clicoding-sandbox-images-arm64.tar.sha256`
- `sandbox-image-lock.json`
- `sandbox-image-lock.json.sha256`

The lock records the source reference, local transfer tag, immutable Docker image ID and ARM64 platform. Transfer all four files to the DGX. The image archive may be large; ensure sufficient staging disk space.

After explicit deployment approval, transfer all four files to \`$release_root/incoming/\`, then load the archive and install the lock:

\`\`\`bash
release_root=/opt/ai/clicoding/releases/<full-commit-sha>
cd "$release_root/incoming"
sha256sum -c clicoding-sandbox-images-arm64.tar.sha256
sha256sum -c sandbox-image-lock.json.sha256
sudo docker load -i clicoding-sandbox-images-arm64.tar
sudo install -d -o root -g root -m 0755 /etc/cds-ai
sudo install -o root -g root -m 0600 \
  sandbox-image-lock.json /etc/cds-ai/sandbox-image-lock.json
sudo python3 "$release_root/source/CDS_AI/scripts/check_sandbox_runtime.py" \
  --nuget-feed /var/cache/clicoding-sandbox/nuget-feed
\`\`\`

The runner resolves each pinned source reference to its locked local image ID at execution time; it does not depend on registry access or mutable tags. The preflight inspects every locked image ID/platform and the feed ownership/permissions without pulling images or changing Docker state. Do not start the broker if the image lock or any image check fails.


## 6. Initial safe runtime configuration (after source, dependency and image gates)

- Start with `CLICODING_ENABLE_PLANNING=false`.
- Use an absolute persistent `CLICODING_DATABASE_PATH` and attachment storage path outside the source tree.
- Copy `config/workers.example.yaml` to a private configuration file outside Git. Replace every sample endpoint and model alias only after a read-only live preflight confirms them.
- The example worker entries are disabled. Do not enable a worker merely because its entry exists.
- Keep worker concurrency at one until measured on the actual runtime and memory headroom is accepted.
- Supply worker credentials only through protected environment variables. Never commit or include secrets in the transfer ZIP.
- Generate a random inbound secret of at least 32 ASCII characters and configure `CLICODING_API_KEY` before any network exposure.
- Initially bind Uvicorn to `127.0.0.1`; use one process until live supervisor and multi-process acceptance is complete.
- Do not connect Open WebUI until client acceptance validates conversation-ID persistence, SSE, Stop, regenerate/edit and the progress-event behavior.

## 7. Acceptance order

1. Verify archive checksum, source revision and extracted file inventory.
2. Verify the offline wheelhouse against the exact DGX Python/ARM64 target.
3. Create an isolated candidate environment without replacing any current environment.
4. Install from the wheelhouse with network access disabled.
5. Run the full test suite and record the exact command, result and environment.
6. Start a candidate instance bound only to localhost and using a new, isolated test database and attachment directory.
7. Verify `GET /health`, authenticated `GET /v1/models`, direct chat, conversation persistence, SSE completion, cancellation, regenerate/edit and tool-call behavior.
8. Validate each actual worker endpoint independently; test tool calling only if the exact model/runtime/parser supports it.
9. Test backup, restore to a separate candidate path, and rollback without touching production data.
10. Run a real multi-file .NET sample Build and Test through the IDE adapter; verify exact approval hash, no-network Docker execution, offline NuGet restore and Stop/cancel.
11. Complete Open WebUI/client and real-model response-quality acceptance before any production cutover.

## 8. Explicit release blockers

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

## 9. Cutover authorization

Staging is not installation. Do not replace the current application, modify systemd units, change model containers, enable the broker, enable workers, or restart any service until:
- all source and offline-install gates are closed;
- the exact artifact SHA and test evidence are recorded;
- a separate deployment plan and rollback point are reviewed; and
- the user explicitly approves the deployment step.

Record the deployed commit, archive SHA-256, dependency inventory, configuration revision (never secret values), test results, changed paths, service actions and rollback point.
