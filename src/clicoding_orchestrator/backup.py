from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import tempfile
import uuid
from typing import Any


MANIFEST_NAME = "manifest.json"
DATABASE_NAME = "database.sqlite3"


class BackupError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_key(value: str) -> PurePosixPath:
    key = PurePosixPath(value)
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or key.is_absolute()
        or len(key.parts) != 2
        or any(part in {"", ".", ".."} for part in key.parts)
    ):
        raise BackupError("database contains an unsafe attachment storage key")
    return key


def _safe_source_file(root: Path, key: PurePosixPath) -> Path:
    current = root
    for part in key.parts:
        current = current / part
        if current.is_symlink():
            raise BackupError("attachment storage contains a symbolic link")
    resolved = current.resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise BackupError("attachment path is missing or outside its storage root")
    return resolved


def _attachment_rows(database_path: Path) -> list[dict[str, Any]]:
    with closing(sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                "SELECT storage_key, size_bytes, sha256 FROM attachments ORDER BY storage_key"
            ).fetchall()
        except sqlite3.OperationalError as exc:
            raise BackupError("database does not contain the CDS_AI attachments table") from exc
    return [dict(row) for row in rows]


def _integrity_check(database_path: Path) -> None:
    with closing(sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)) as connection:
        result = connection.execute("PRAGMA integrity_check").fetchone()
    if result is None or result[0] != "ok":
        raise BackupError("SQLite integrity_check failed")


def create_backup(database: str | Path, attachments: str | Path, output: str | Path) -> Path:
    database_path = Path(database).expanduser().resolve(strict=True)
    attachment_root = Path(attachments).expanduser().resolve(strict=True)
    output_root = Path(output).expanduser().resolve()
    if not database_path.is_file() or not attachment_root.is_dir():
        raise BackupError("database must be a file and attachment storage must be a directory")
    output_root.mkdir(parents=True, exist_ok=True)
    backup_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    staging = output_root / f".cds-ai-backup-{backup_id}.tmp"
    destination = output_root / f"cds-ai-backup-{backup_id}"
    staging.mkdir(mode=0o700)
    try:
        snapshot = staging / DATABASE_NAME
        with closing(sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)) as source, closing(sqlite3.connect(snapshot)) as target:
            source.backup(target)
        os.chmod(snapshot, 0o600)
        _integrity_check(snapshot)

        copied: list[dict[str, Any]] =
        backup_attachment_root = staging / "attachments"
        backup_attachment_root.mkdir(mode=0o700)
        for row in _attachment_rows(snapshot):
            key = _safe_key(row["storage_key"])
            source_file = _safe_source_file(attachment_root, key)
            data_size = source_file.stat().st_size
            if data_size != row["size_bytes"] or _sha256(source_file) != row["sha256"]:
                raise BackupError(f"attachment integrity check failed: {key.as_posix()}")
            destination_file = backup_attachment_root.joinpath(*key.parts)
            destination_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copyfile(source_file, destination_file)
            os.chmod(destination_file, 0o600)
            if destination_file.stat().st_size != row["size_bytes"] or _sha256(destination_file) != row["sha256"]:
                raise BackupError(f"attachment changed while being backed up: {key.as_posix()}")
            copied.append({
                "storage_key": key.as_posix(),
                "size_bytes": row["size_bytes"],
                "sha256": row["sha256"],
            })

        manifest = {
            "format": "cds-ai-backup",
            "version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "database": {
                "file": DATABASE_NAME,
                "size_bytes": snapshot.stat().st_size,
                "sha256": _sha256(snapshot),
            },
            "attachments": copied,
        }
        (staging / MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        verify_backup(staging)
        os.replace(staging, destination)
        return destination
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _verify_attachment_tree(root: Path, expected_records: list[dict[str, Any]]) -> None:
    if root.is_symlink() or not root.is_dir():
        raise BackupError("attachment backup directory is missing or unsafe")
    expected_files = {item["storage_key"] for item in expected_records}
    expected_directories: set[str] = set()
    for key in expected_files:
        parts = _safe_key(key).parts
        expected_directories.add(parts[0])
    for path in root.rglob("*"):
        if path.is_symlink():
            raise BackupError("backup contains a symbolic link")
        relative = path.relative_to(root).as_posix()
        if path.is_file() and relative not in expected_files:
            raise BackupError("backup contains an unreferenced attachment file")
        if path.is_dir() and relative not in expected_directories:
            raise BackupError("backup contains an unexpected attachment directory")


def verify_backup(backup: str | Path) -> dict[str, Any]:
    root = Path(backup).expanduser().resolve(strict=True)
    manifest_path = root / MANIFEST_NAME
    database_path = root / DATABASE_NAME
    attachments_path = root / "attachments"
    if (
        manifest_path.is_symlink() or database_path.is_symlink() or attachments_path.is_symlink()
        or not manifest_path.is_file() or not database_path.is_file() or not attachments_path.is_dir()
    ):
        raise BackupError("backup manifest, database snapshot or attachment directory is missing or unsafe")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupError("backup manifest is invalid") from exc
    if manifest.get("format") != "cds-ai-backup" or manifest.get("version") != 1:
        raise BackupError("unsupported backup format or version")
    db_record = manifest.get("database", {})
    if (
        db_record.get("file") != DATABASE_NAME
        or db_record.get("size_bytes") != database_path.stat().st_size
        or db_record.get("sha256") != _sha256(database_path)
    ):
        raise BackupError("database snapshot hash or size does not match the manifest")
    _integrity_check(database_path)

    expected = manifest.get("attachments")
    if not isinstance(expected, list):
        raise BackupError("attachment manifest must be a list")
    rows = _attachment_rows(database_path)
    if len(rows) != len(expected):
        raise BackupError("attachment manifest does not match the database snapshot")
    actual_by_key = {item.get("storage_key"): item for item in expected if isinstance(item, dict)}
    if len(actual_by_key) != len(expected):
        raise BackupError("attachment manifest contains duplicate or invalid entries")
    _verify_attachment_tree(attachments_path, expected)
    for row in rows:
        key = _safe_key(row["storage_key"])
        record = actual_by_key.get(key.as_posix())
        if record is None or record.get("size_bytes") != row["size_bytes"] or record.get("sha256") != row["sha256"]:
            raise BackupError(f"attachment manifest mismatch: {key.as_posix()}")
        path = root / "attachments"
        for part in key.parts:
            path = path / part
            if path.is_symlink():
                raise BackupError("backup contains a symbolic link")
        if not path.is_file() or path.stat().st_size != row["size_bytes"] or _sha256(path) != row["sha256"]:
            raise BackupError(f"attachment backup is missing or corrupt: {key.as_posix()}")
    return manifest


def restore_backup(
    backup: str | Path,
    database: str | Path,
    attachments: str | Path,
    *,
    replace_existing: bool = False,
) -> dict[str, str | None]:
    manifest = verify_backup(backup)
    backup_root = Path(backup).expanduser().resolve(strict=True)
    database_target = Path(database).expanduser().resolve()
    attachments_target = Path(attachments).expanduser().resolve()
    if database_target == attachments_target or database_target in attachments_target.parents or attachments_target in database_target.parents:
        raise BackupError("database and attachment destinations must not overlap")
    for target in (database_target, attachments_target):
        if target == backup_root or target in backup_root.parents or backup_root in target.parents:
            raise BackupError("restore destinations must not overlap the backup directory")
    if (database_target.exists() or attachments_target.exists()) and not replace_existing:
        raise BackupError("restore destinations already exist; pass replace_existing=True after reviewing them")
    database_target.parent.mkdir(parents=True, exist_ok=True)
    attachments_target.parent.mkdir(parents=True, exist_ok=True)

    restore_id = uuid.uuid4().hex
    staged_database = database_target.parent / f".{database_target.name}.{restore_id}.restore"
    staged_attachments = attachments_target.parent / f".{attachments_target.name}.{restore_id}.restore"
    previous_database = database_target.parent / f".{database_target.name}.pre-restore-{restore_id}"
    previous_attachments = attachments_target.parent / f".{attachments_target.name}.pre-restore-{restore_id}"
    moved_database = False
    moved_attachments = False
    installed_database = False
    installed_attachments = False
    try:
        shutil.copyfile(backup_root / DATABASE_NAME, staged_database)
        os.chmod(staged_database, 0o600)
        with staged_database.open("rb+") as stream:
            os.fsync(stream.fileno())
        shutil.copytree(backup_root / "attachments", staged_attachments)
        _integrity_check(staged_database)
        _verify_attachment_tree(staged_attachments, manifest["attachments"])
        for item in manifest["attachments"]:
            key = _safe_key(item["storage_key"])
            staged_file = staged_attachments.joinpath(*key.parts)
            if (
                not staged_file.is_file()
                or staged_file.stat().st_size != item["size_bytes"]
                or _sha256(staged_file) != item["sha256"]
            ):
                raise BackupError(f"attachment changed during restore: {key.as_posix()}")
        if database_target.exists():
            os.replace(database_target, previous_database)
            moved_database = True
        if attachments_target.exists():
            os.replace(attachments_target, previous_attachments)
            moved_attachments = True
        os.replace(staged_database, database_target)
        installed_database = True
        os.replace(staged_attachments, attachments_target)
        installed_attachments = True
    except Exception:
        if installed_attachments:
            shutil.rmtree(attachments_target, ignore_errors=True)
        if installed_database:
            database_target.unlink(missing_ok=True)
        if moved_database and previous_database.exists():
            os.replace(previous_database, database_target)
        if moved_attachments and previous_attachments.exists():
            os.replace(previous_attachments, attachments_target)
        raise
    finally:
        staged_database.unlink(missing_ok=True)
        shutil.rmtree(staged_attachments, ignore_errors=True)

    return {
        "database": str(database_target),
        "attachments": str(attachments_target),
        "previous_database": str(previous_database) if moved_database else None,
        "previous_attachments": str(previous_attachments) if moved_attachments else None,
        "backup_created_at": str(manifest["created_at"]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="CDS_AI SQLite and attachment backup utility")
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup_parser = subparsers.add_parser("backup", help="create a consistent SQLite and attachment backup")
    backup_parser.add_argument("--database", required=True)
    backup_parser.add_argument("--attachments", required=True)
    backup_parser.add_argument("--output", required=True)

    verify_parser = subparsers.add_parser("verify", help="verify a backup manifest, database and attachment hashes")
    verify_parser.add_argument("--backup", required=True)

    restore_parser = subparsers.add_parser("restore", help="restore a verified backup")
    restore_parser.add_argument("--backup", required=True)
    restore_parser.add_argument("--database", required=True)
    restore_parser.add_argument("--attachments", required=True)
    restore_parser.add_argument("--replace", action="store_true")

    args = parser.parse_args()
    if args.command == "backup":
        result = {"backup": str(create_backup(args.database, args.attachments, args.output))}
    elif args.command == "verify":
        manifest = verify_backup(args.backup)
        result = {"verified": True, "created_at": manifest["created_at"]}
    else:
        result = restore_backup(
            args.backup, args.database, args.attachments,
            replace_existing=args.replace,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
