from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import secrets
import sqlite3
import threading
import time
import uuid
from typing import Any, Iterator


class SecurityStore:
    """SQLite-backed token registry, fixed-window rate limiter and audit ledger."""

    def __init__(self, database_path: str):
        self.path = database_path
        self._memory = database_path == ":memory:"
        self._lock = threading.RLock()
        self._keeper = (
            sqlite3.connect(":memory:", timeout=15, check_same_thread=False)
            if self._memory else None
        )
        self._initialize()

    @contextmanager
    def _session(self) -> Iterator[sqlite3.Connection]:
        if self._memory:
            assert self._keeper is not None
            with self._lock:
                connection = self._keeper
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute("PRAGMA busy_timeout = 15000")
                with connection:
                    yield connection
            return
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 15000")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._session() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS api_tokens (
                    id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    label TEXT NOT NULL,
                    token_sha256 TEXT NOT NULL UNIQUE,
                    token_suffix TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    revoked_at TEXT
                )
            """)
            connection.execute("""
                CREATE INDEX IF NOT EXISTS ix_api_tokens_owner
                ON api_tokens(owner_id, created_at)
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS api_rate_limits (
                    owner_id TEXT NOT NULL,
                    window_start INTEGER NOT NULL,
                    request_count INTEGER NOT NULL,
                    PRIMARY KEY(owner_id, window_start)
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS api_audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    route TEXT NOT NULL,
                    status_code INTEGER NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE INDEX IF NOT EXISTS ix_api_audit_created
                ON api_audit_events(created_at, id)
            """)

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def has_active_tokens(self) -> bool:
        with self._session() as connection:
            row = connection.execute(
                "SELECT 1 FROM api_tokens WHERE revoked_at IS NULL LIMIT 1"
            ).fetchone()
        return row is not None

    def authenticate_token(self, token: str) -> str | None:
        digest = self._hash(token)
        with self._session() as connection:
            row = connection.execute(
                "SELECT owner_id FROM api_tokens WHERE token_sha256 = ? AND revoked_at IS NULL",
                (digest,),
            ).fetchone()
        return str(row["owner_id"]) if row else None

    def issue_token(self, owner_id: str, label: str) -> dict[str, str]:
        if (
            not isinstance(owner_id, str)
            or not owner_id.strip()
            or len(owner_id) > 128
            or owner_id in {"admin", "shared"}
            or any(character.isspace() for character in owner_id)
        ):
            raise ValueError("owner_id is invalid or reserved")
        if not isinstance(label, str) or not label.strip() or len(label) > 80:
            raise ValueError("token label must contain 1 to 80 characters")
        token = secrets.token_urlsafe(32)
        token_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()
        with self._session() as connection:
            connection.execute(
                """
                INSERT INTO api_tokens
                    (id, owner_id, label, token_sha256, token_suffix, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (token_id, owner_id, label.strip(), self._hash(token), token[-6:], created_at),
            )
        return {
            "id": token_id,
            "owner_id": owner_id,
            "label": label.strip(),
            "token": token,
            "token_suffix": token[-6:],
            "created_at": created_at,
        }

    def revoke_token(self, token_id: str) -> bool:
        revoked_at = datetime.now(timezone.utc).isoformat()
        with self._session() as connection:
            cursor = connection.execute(
                """
                UPDATE api_tokens SET revoked_at = ?
                WHERE id = ? AND revoked_at IS NULL
                """,
                (revoked_at, token_id),
            )
        return cursor.rowcount == 1

    def list_tokens(self, owner_id: str | None = None) -> list[dict[str, Any]]:
        query = (
            "SELECT id, owner_id, label, token_suffix, created_at, revoked_at "
            "FROM api_tokens"
        )
        parameters: tuple[Any, ...] = ()
        if owner_id is not None:
            query += " WHERE owner_id = ?"
            parameters = (owner_id,)
        query += " ORDER BY created_at DESC"
        with self._session() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [dict(row) for row in rows]

    def consume_rate_limit(
        self, owner_id: str, limit: int, window_seconds: int = 60, now: int | None = None
    ) -> tuple[bool, int, int]:
        if limit < 1 or window_seconds < 1:
            raise ValueError("rate limit settings must be positive")
        timestamp = int(time.time()) if now is None else int(now)
        window_start = timestamp - (timestamp % window_seconds)
        with self._session() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT request_count FROM api_rate_limits
                WHERE owner_id = ? AND window_start = ?
                """,
                (owner_id, window_start),
            ).fetchone()
            count = int(row["request_count"]) + 1 if row else 1
            connection.execute(
                """
                INSERT INTO api_rate_limits(owner_id, window_start, request_count)
                VALUES (?, ?, ?)
                ON CONFLICT(owner_id, window_start)
                DO UPDATE SET request_count = excluded.request_count
                """,
                (owner_id, window_start, count),
            )
            # Bound the ledger without requiring a separate scheduler.
            connection.execute(
                "DELETE FROM api_rate_limits WHERE window_start < ?",
                (window_start - (2 * 24 * 60 * 60),),
            )
        retry_after = max(1, window_seconds - (timestamp - window_start))
        return count <= limit, count, retry_after

    def append_audit(
        self,
        request_id: str,
        owner_id: str,
        action: str,
        route: str,
        status_code: int,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        safe_metadata = metadata or {}
        encoded = json.dumps(safe_metadata, ensure_ascii=False, separators=(",", ":"))
        with self._session() as connection:
            connection.execute(
                """
                INSERT INTO api_audit_events
                    (request_id, owner_id, action, route, status_code, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id, owner_id, action, route, int(status_code), encoded,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def list_audit(self, limit: int = 100, before_id: int | None = None) -> list[dict[str, Any]]:
        if limit < 1 or limit > 1000:
            raise ValueError("audit page size must be between 1 and 1000")
        query = (
            "SELECT id, request_id, owner_id, action, route, status_code, "
            "metadata_json, created_at FROM api_audit_events"
        )
        parameters: tuple[Any, ...] = ()
        if before_id is not None:
            query += " WHERE id < ?"
            parameters = (before_id,)
        query += " ORDER BY id DESC LIMIT ?"
        parameters += (limit,)
        with self._session() as connection:
            rows = connection.execute(query, parameters).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item.pop("metadata_json"))
            result.append(item)
        return result
