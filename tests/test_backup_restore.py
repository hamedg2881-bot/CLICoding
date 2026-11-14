import hashlib
import os
import sqlite3
import uuid

import pytest

from clicoding_orchestrator.backup import BackupError, create_backup, restore_backup, verify_backup


def make_source(tmp_path):
    database = tmp_path / "clicoding.db"
    attachments = tmp_path / "attachments"
    conversation_id = str(uuid.uuid4())
    attachment_id = str(uuid.uuid4())
    storage_key = f"{conversation_id}/{attachment_id}.png"
    target = attachments / storage_key
    target.parent.mkdir(parents=True)
    payload = b"\x89PNG\r\n\x1a\nbackup-test-image"
    target.write_bytes(payload)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE attachments (storage_key TEXT, size_bytes INTEGER, sha256 TEXT)"
        )
        connection.execute(
            "CREATE TABLE app_state (value TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO app_state(value) VALUES ('original')")
        connection.execute(
            "INSERT INTO attachments(storage_key, size_bytes, sha256) VALUES (?, ?, ?)",
            (storage_key, len(payload), hashlib.sha256(payload).hexdigest()),
        )
    return database, attachments, storage_key, payload


def test_backup_verify_and_restore_preserves_database_and_attachment_bytes(tmp_path):
    database, attachments, storage_key, payload = make_source(tmp_path)
    backup = create_backup(database, attachments, tmp_path / "backups")
    manifest = verify_backup(backup)
    assert manifest["format"] == "cds-ai-backup"
    assert len(manifest["attachments"]) == 1

    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE app_state SET value = 'modified'")
    (attachments / storage_key).write_bytes(b"modified attachment")

    restored = restore_backup(
        backup, database, attachments, replace_existing=True
    )
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT value FROM app_state").fetchone()[0] == "original"
    assert (attachments / storage_key).read_bytes() == payload
    assert restored["previous_database"] is not None
    assert restored["previous_attachments"] is not None


def test_backup_verification_rejects_tampered_attachment(tmp_path):
    database, attachments, storage_key, _ = make_source(tmp_path)
    backup = create_backup(database, attachments, tmp_path / "backups")
    (backup / "attachments" / storage_key).write_bytes(b"tampered")
    with pytest.raises(BackupError, match="missing or corrupt"):
        verify_backup(backup)


def test_restore_refuses_to_overwrite_without_explicit_flag(tmp_path):
    database, attachments, _, _ = make_source(tmp_path)
    backup = create_backup(database, attachments, tmp_path / "backups")
    with pytest.raises(BackupError, match="already exist"):
        restore_backup(backup, database, attachments)


def test_backup_verification_rejects_unreferenced_files(tmp_path):
    database, attachments, _, _ = make_source(tmp_path)
    backup = create_backup(database, attachments, tmp_path / "backups")
    extra = backup / "attachments" / "unreferenced.bin"
    extra.write_bytes(b"not in the database snapshot")
    with pytest.raises(BackupError, match="unreferenced attachment"):
        verify_backup(backup)



def test_backup_verification_rejects_unreferenced_top_level_content(tmp_path):
    database, attachments, _, _ = make_source(tmp_path)
    backup = create_backup(database, attachments, tmp_path / "backups")
    (backup / "unexpected.txt").write_text("not part of this backup")
    with pytest.raises(BackupError, match="top-level content"):
        verify_backup(backup)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="named pipes are unavailable")
def test_backup_verification_rejects_special_files(tmp_path):
    database, attachments, _, _ = make_source(tmp_path)
    backup = create_backup(database, attachments, tmp_path / "backups")
    os.mkfifo(backup / "attachments" / "unexpected.pipe")
    with pytest.raises(BackupError, match="special file"):
        verify_backup(backup)


def test_backup_verification_rejects_symlinked_backup_root(tmp_path):
    database, attachments, _, _ = make_source(tmp_path)
    backup = create_backup(database, attachments, tmp_path / "backups")
    alias = tmp_path / "backup-alias"
    alias.symlink_to(backup, target_is_directory=True)
    with pytest.raises(BackupError, match="symbolic link"):
        verify_backup(alias)
