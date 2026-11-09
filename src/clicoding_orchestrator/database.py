from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import uuid
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    """Small SQLite repository for the first persistent-conversation slice."""

    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 15000")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS conversation_events (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL
                        REFERENCES conversations(id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'tool')),
                    content TEXT NOT NULL,
                    producer TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE (conversation_id, sequence)
                );

                CREATE INDEX IF NOT EXISTS ix_events_conversation_sequence
                    ON conversation_events(conversation_id, sequence);
                """
            )

    def create_conversation(self) -> dict[str, str]:
        conversation_id = str(uuid.uuid4())
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO conversations (id, created_at, updated_at) VALUES (?, ?, ?)",
                (conversation_id, now, now),
            )
        return {"id": conversation_id, "created_at": now, "updated_at": now}

    def conversation_exists(self, conversation_id: str) -> bool:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
        return row is not None

    def append_event(
        self, conversation_id: str, role: str, content: str, producer: str | None = None
    ) -> dict[str, Any]:
        now = utc_now()
        event_id = str(uuid.uuid4())
        with self.connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence "
                "FROM conversation_events WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            sequence = int(row["next_sequence"])
            connection.execute(
                """
                INSERT INTO conversation_events
                    (id, conversation_id, sequence, role, content, producer, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (event_id, conversation_id, sequence, role, content, producer, now),
            )
            connection.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now, conversation_id),
            )
        return {
            "id": event_id,
            "conversation_id": conversation_id,
            "sequence": sequence,
            "role": role,
            "content": content,
            "producer": producer,
            "created_at": now,
        }

    def list_events(self, conversation_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, conversation_id, sequence, role, content, producer, created_at
                FROM conversation_events
                WHERE conversation_id = ?
                ORDER BY sequence ASC
                """,
                (conversation_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT id, created_at, updated_at FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["events"] = self.list_events(conversation_id)
        return result
