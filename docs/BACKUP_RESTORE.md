# CDS_AI backup and restore

The packaged offline utility backs up the SQLite conversation database and exactly the attachment files referenced by that database. It uses SQLite's online backup API, checks database integrity, verifies attachment size/SHA-256, writes a versioned manifest, and publishes a backup directory only after verification succeeds.

## Create and verify a backup

Run from an environment where the CDS_AI package is installed:

```bash
cds-ai-backup backup \
  --database /opt/ai/clicoding/data/clicoding.db \
  --attachments /opt/ai/clicoding/data/attachments \
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

If an attachment is being deleted while the backup is copied, verification fails rather than publishing a partial backup. The utility never includes `.env`, model weights, worker configuration or service credentials.

## Restore

**Stop CDS_AI before restoring.** The utility does not stop services or change service state.

Restore to new empty destinations:

```bash
cds-ai-backup restore \
  --backup /secure/backups/cds-ai/cds-ai-backup-<timestamp>-<id> \
  --database /opt/ai/clicoding/data/clicoding.db \
  --attachments /opt/ai/clicoding/data/attachments
```

If destinations already exist, restore refuses to overwrite them. After reviewing the verified backup and ensuring the service is stopped, explicitly pass `--replace`:

```bash
cds-ai-backup restore \
  --backup /secure/backups/cds-ai/cds-ai-backup-<timestamp>-<id> \
  --database /opt/ai/clicoding/data/clicoding.db \
  --attachments /opt/ai/clicoding/data/attachments \
  --replace
```

When replacing existing destinations, the utility renames them to sibling `pre-restore` paths and retains them for manual rollback. It verifies the staged database and copied attachment hashes before installation, and attempts to restore the previous paths if installation fails. Database and attachment replacement cannot be one filesystem transaction, so inspect the printed paths and verify the result before starting CDS_AI.

## Security and operational policy

- Conversation databases and attachments are sensitive user data. Store backups in an access-controlled location and encrypt them at rest using the organization's offline backup mechanism.
- Do not place the backup directory inside the active attachment storage directory.
- Keep at least one backup on a separate physical device; periodically test restore on a disposable path.
- Back up deployment configuration separately through a secret-management process. Never add `.env` or credentials to this backup archive.
- Schedule backups using the host's approved offline scheduler; the application does not create timers or run backup jobs automatically.
- This utility does not provide encryption, remote replication, retention scheduling or service orchestration.
