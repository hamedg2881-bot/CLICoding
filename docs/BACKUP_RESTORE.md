# CDS_AI backup and restore

The packaged offline utility backs up the SQLite conversation database and exactly the attachment files referenced by that database. It uses SQLite's online backup API, checks database integrity, verifies attachment size/SHA-256, writes a versioned manifest, and publishes a backup directory only after verification succeeds.

## Production paths

The deployment template stores persistent state here:

- SQLite database: `/var/lib/cds-ai/clicoding.db`
- Image attachments: `/var/lib/cds-ai/attachments`

The Orchestrator and privileged sandbox broker share this same SQLite database. Keep the database and attachment paths paired; do not back up one release's database with another environment's attachment directory. For staging or recovery drills, substitute a separate database and matching attachment directory consistently.

## Create and verify a backup

Run from an environment where the CDS_AI package is installed. The output directory must be outside the live attachment directory and preferably on a separate protected filesystem:

```bash
cds-ai-backup backup \
  --database /var/lib/cds-ai/clicoding.db \
  --attachments /var/lib/cds-ai/attachments \
  --output /secure/backups/cds-ai
```

The command prints the created backup directory. Verify it before moving it to another machine or offline storage:

```bash
cds-ai-backup verify --backup /secure/backups/cds-ai/cds-ai-backup-<timestamp>-<id>
```

The backup contains:
- `database.sqlite3`: consistent SQLite snapshot, including conversation history, summaries, token hashes, audit records and execution state.
- `attachments/`: only attachment files referenced by the database snapshot.
- `manifest.json`: format version, creation time, database hash and attachment hashes.

SQLite's online backup API provides a transactionally consistent database snapshot. Attachment files are copied and checked against the snapshot's size and SHA-256 records; if a file is concurrently removed or changed, backup creation fails rather than publishing an unverified backup. For an upgrade or maintenance window, quiesce both services before creating the final backup to avoid concurrent broker writes and attachment mutations. The utility never includes `.env`, model weights, worker configuration or service credentials.

## Restore

**Stop both CDS_AI services before restoring:** the Orchestrator and the sandbox broker share the SQLite database, and either process may write it. The utility does not stop services or change service state.

Restore to new empty destinations:

```bash
cds-ai-backup restore \
  --backup /secure/backups/cds-ai/cds-ai-backup-<timestamp>-<id> \
  --database /var/lib/cds-ai/clicoding.db \
  --attachments /var/lib/cds-ai/attachments
```

If destinations already exist, restore refuses to overwrite them. After reviewing the verified backup, confirming both services are stopped, and explicitly approving replacement, pass `--replace`:

```bash
cds-ai-backup restore \
  --backup /secure/backups/cds-ai/cds-ai-backup-<timestamp>-<id> \
  --database /var/lib/cds-ai/clicoding.db \
  --attachments /var/lib/cds-ai/attachments \
  --replace
```

When replacing existing destinations, the utility renames them to sibling `pre-restore` paths and retains them for manual rollback. It verifies the staged database and copied attachment hashes before installation, and attempts to restore the previous paths if installation fails. Database and attachment replacement cannot be one filesystem transaction, so inspect the printed paths and verify the result before starting either service. Preserve the `pre-restore` paths until application-level recovery checks have passed.

## Security and operational policy

- Conversation databases and attachments are sensitive user data. Store backups in an access-controlled location and encrypt them at rest using the organization's offline backup mechanism.
- Do not place the backup directory inside the active attachment storage directory or under `/var/lib/cds-ai`.
- Keep at least one backup on a separate physical device; periodically test restore on disposable paths with both services stopped.
- Back up deployment configuration separately through a secret-management process. Never add `.env` or credentials to this backup archive.
- Schedule backups using the host's approved offline scheduler; the application does not create timers or run backup jobs automatically.
- This utility does not provide encryption, remote replication, retention scheduling or service orchestration.
