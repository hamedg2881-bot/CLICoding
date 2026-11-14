# CDS_AI Orchestrator — offline service deployment runbook

**This is an operator procedure, not authorization to run it.** Execute only after the source artifact, ARM64 wheelhouse, runtime configuration, migration test and rollback plan have been reviewed and the deployment is explicitly approved.

## Fixed layout

| Purpose | Path |
|---|---|
| Versioned source releases | `/opt/ai/clicoding/releases/<full-commit-sha>/source/CDS_AI` |
| Active source symlink | `/opt/ai/clicoding/current` |
| Versioned Python environment | `/opt/ai/clicoding/releases/<sha>/venv` |
| Versioned offline wheelhouse | `/opt/ai/clicoding/releases/<sha>/wheelhouse` |
| Private service configuration | `/etc/cds-ai/orchestrator.env` |
| Private worker registry | `/etc/cds-ai/workers.yaml` |
| Broker configuration | `/etc/cds-ai/sandbox-broker.env` |
| Database and attachments | `/var/lib/cds-ai/` |
| Orchestrator service | `cds-ai-orchestrator.service` |
| Sandbox broker service | `clicoding-sandbox-broker.service` |

The service binds to loopback port 8090 and runs one Uvicorn worker. It does not start, stop or reconfigure model containers.

## 1. Read-only validation

Before installation, verify the release ZIP checksum and revision using
[DGX_STAGING_CHECKLIST.md](../docs/DGX_STAGING_CHECKLIST.md). Confirm:
- ARM64 host and CPython 3.12.x (the published wheelhouse is built for CPython 3.12; Python 3.11, PyPy or another ABI is not compatible);
- an offline wheelhouse whose manifest verifies against the host Python ABI and ARM64 platform;
- every worker endpoint and served model alias using read-only requests;
- Docker runtime images are already present locally and match the approved image inventory;
- a separate candidate database and attachment directory are available for acceptance.

Do not install from the public package index. Do not use a wheelhouse built for x86_64.

## 2. Create service identities and directories

Run only once, after approval:

```bash
sudo groupadd --system clicoding-sandbox
sudo useradd --system --home-dir /var/lib/cds-ai --shell /usr/sbin/nologin cds-ai
sudo install -d -o root -g cds-ai -m 0750 /etc/cds-ai
sudo install -d -o cds-ai -g cds-ai -m 0750 /var/lib/cds-ai
sudo install -d -o root -g root -m 0755 /opt/ai/clicoding
sudo install -d -o root -g root -m 0755 /opt/ai/clicoding/releases
```

If either identity already exists, inspect it and do not recreate or change it blindly.

## 3. Install the exact release and Python dependencies

After transferring and verifying the artifact, extract it into its commit-specific release directory. Keep the release root and source files root-owned and readable but not writable by the service account. The service unit's `WorkingDirectory` resolves through `/opt/ai/clicoding/current/source/CDS_AI`; the `current` symlink must therefore point to the release root (not directly to `source/CDS_AI`).

Extract both verified artifacts into one versioned release root. The source ZIP creates `source/CDS_AI/`; the wheelhouse tarball creates `wheelhouse/` and `wheelhouse-manifest.json`.

```bash
release_root=/opt/ai/clicoding/releases/<full-commit-sha>
sudo install -d -o root -g root -m 0755 "$release_root"
sudo unzip -q CDS_AI_<12-char-sha>.zip -d "$release_root/source"
sudo tar -xzf CDS_AI-wheelhouse-cp312-arm64-<12-char-sha>.tar.gz -C "$release_root"
```

Verify the wheelhouse, create an isolated test environment, and run the complete suite before preparing the production environment:

```bash
python3 "$release_root/source/CDS_AI/scripts/verify_wheelhouse.py" \
  --wheelhouse "$release_root/wheelhouse" \
  --manifest "$release_root/wheelhouse-manifest.json"

sudo python3 -m venv "$release_root/test-venv"
sudo "$release_root/test-venv/bin/python" -m pip install \
  --no-index --find-links="$release_root/wheelhouse" \
  'clicoding-orchestrator[test]'
cd "$release_root/source/CDS_AI"
sudo "$release_root/test-venv/bin/python" -m pytest

sudo python3 -m venv "$release_root/venv"
sudo "$release_root/venv/bin/python" -m pip install \
  --no-index --find-links="$release_root/wheelhouse" \
  'clicoding-orchestrator'
sudo "$release_root/venv/bin/python" -m pip check
sudo "$release_root/venv/bin/python" -c "import clicoding_orchestrator; print('package import OK')"
```

The production `venv` intentionally omits test-only dependencies; the separate `test-venv` can be retained for audit or removed after acceptance. Every install command must fail if a dependency is absent from the wheelhouse; do not remove `--no-index` or add a public index fallback. The actual Python major/minor version must match the wheelhouse build target.

Set the active release pointer only after the source, venv, package checks and staging tests have been verified:

```bash
sudo ln -sfn "$release_root" /opt/ai/clicoding/current.next
sudo mv -Tf /opt/ai/clicoding/current.next /opt/ai/clicoding/current
```

## 4. Configure secrets and workers

Create the private environment file from the sanitized example:

```bash
sudo install -o root -g cds-ai -m 0640 \
  /opt/ai/clicoding/current/deploy/cds-ai-orchestrator.env.example \
  /etc/cds-ai/orchestrator.env
sudoedit /etc/cds-ai/orchestrator.env
```

Generate a unique random inbound API secret of at least 32 ASCII characters. Keep planning disabled until all configured workers have passed live tests. Configure `workers.yaml` separately with verified endpoints, model aliases, credentials supplied by environment-variable references, context/output limits and conservative concurrency. Do not enable sample entries as-is.

Install the service unit:

```bash
sudo install -o root -g root -m 0644 \
  /opt/ai/clicoding/current/deploy/cds-ai-orchestrator.service \
  /etc/systemd/system/cds-ai-orchestrator.service
sudo systemd-analyze verify \
  /etc/systemd/system/cds-ai-orchestrator.service
```

## 5. Install the existing single-file sandbox (only if separately approved)

The sandbox is a separate privileged component. Follow [sandbox/README.md](../sandbox/README.md) to install the root-owned runner and broker, stage all pinned ARM64 images, and configure the broker. The broker and Orchestrator must point to the exact same SQLite database path.

The runner and broker now implement approved multi-file .NET 10 workspace Build/Test snapshots. This is source-level implementation only: do not enable IDE build/test tools until the exact ARM64 image lock, root-owned offline NuGet feed, sample-project restore/build/test and broker-backed Stop/cancel behavior pass on the target DGX.

## 6. Candidate service acceptance

Use a separate test database and attachment directory for initial acceptance. Validate the systemd unit without starting it:

```bash
sudo systemd-analyze verify /etc/systemd/system/cds-ai-orchestrator.service
sudo systemctl daemon-reload
```

After explicit approval to start a candidate instance, verify:

```bash
sudo systemctl status cds-ai-orchestrator.service --no-pager
curl --fail --silent http://127.0.0.1:8090/health
curl --fail --silent \
  -H 'Authorization: Bearer <configured-test-key>' \
  http://127.0.0.1:8090/v1/models
```

Then run the API, conversation, streaming, cancellation, edit/regenerate, backup/restore and real-worker acceptance tests in the staging checklist. Do not connect Open WebUI or expose the API to LAN until authentication and proxy/TLS acceptance is complete.

## 7. Upgrade and rollback

Before every upgrade:
1. Record the current symlink target and running commit.
2. Create and verify a database/attachment backup.
3. Preserve the old release directory and exact previous environment/config revision.
4. Install the new source and dependencies side-by-side; never overwrite the active release.
5. Run tests and migration-on-copy against a copy of the production database.
6. Switch the symlink and restart only after explicit approval.

Code rollback is safe only if the prior application version is compatible with the migrated database schema. If not proven, stop the service and restore the verified pre-upgrade database/attachment backup using the documented restore procedure. Keep the old source release until the new version has passed the agreed observation period.

## 8. Explicitly out of scope for this service unit

- Starting or changing model containers.
- Enabling model workers or planning.
- Enabling the privileged sandbox broker automatically.
- Public network binding or trusted Open WebUI headers without a verified private proxy.
- Automatic backup scheduling, secret rotation, certificate provisioning or firewall changes.
