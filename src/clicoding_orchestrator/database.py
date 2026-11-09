from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import uuid
from typing import Any, Iterator


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    """Small SQLite repository for the first persistent-conversation slice."""

    def __init__(self, path: str):
        self.path = path
        self._uri = path == ":memory:"
        self._dsn = (
            f"file:clicoding-{uuid.uuid4().hex}?mode=memory&cache=shared"
            if self._uri else path
        )
        # Keep one connection alive so SQLite's shared in-memory database survives
        # the short-lived connections used by each repository operation.
        self._keeper = (
            sqlite3.connect(self._dsn, uri=True, timeout=15) if self._uri else None
        )
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._dsn, uri=self._uri, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 15000")
        return connection

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.session() as connection:
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

                CREATE TABLE IF NOT EXISTS executions (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL
                        REFERENCES conversations(id) ON DELETE CASCADE,
                    goal TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS ix_executions_conversation
                    ON executions(conversation_id, created_at);

                CREATE TABLE IF NOT EXISTS execution_tasks (
                    id TEXT NOT NULL,
                    execution_id TEXT NOT NULL
                        REFERENCES executions(id) ON DELETE CASCADE,
                    objective TEXT NOT NULL,
                    capabilities_json TEXT NOT NULL,
                    dependencies_json TEXT NOT NULL,
                    expected_output TEXT NOT NULL,
                    status TEXT NOT NULL,
                    output TEXT,
                    error TEXT,
                    worker_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (execution_id, id)
                );
                """
            )

    def create_conversation(self) -> dict[str, str]:
        conversation_id = str(uuid.uuid4())
        now = utc_now()
        with self.session() as connection:
            connection.execute(
                "INSERT INTO conversations (id, created_at, updated_at) VALUES (?, ?, ?)",
                (conversation_id, now, now),
            )
        return {"id": conversation_id, "created_at": now, "updated_at": now}

    def conversation_exists(self, conversation_id: str) -> bool:
        with self.session() as connection:
            row = connection.execute(
                "SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
        return row is not None

    def append_event(
        self, conversation_id: str, role: str, content: str, producer: str | None = None
    ) -> dict[str, Any]:
        now = utc_now()
        event_id = str(uuid.uuid4())
        with self.session() as connection:
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
        with self.session() as connection:
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
        with self.session() as connection:
            row = connection.execute(
                "SELECT id, created_at, updated_at FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["events"] = self.list_events(conversation_id)
        return result

    def create_execution(self, conversation_id: str, plan: dict[str, Any]) -> dict[str, Any]:
        """Persist a validated plan and its tasks before any worker is dispatched."""
        import json

        execution_id = str(uuid.uuid4())
        now = utc_now()
        tasks = plan.get("tasks", [])
        with self.session() as connection:
            if connection.execute(
                "SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone() is None:
                raise ValueError("conversation does not exist")
            connection.execute(
                """
                INSERT INTO executions (id, conversation_id, goal, plan_json, status,
                                        created_at, updated_at)
                VALUES (?, ?, ?, ?, 'planned', ?, ?)
                """,
                (
                    execution_id,
                    conversation_id,
                    plan["goal"],
                    json.dumps(plan, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            for task in tasks:
                connection.execute(
                    """
                    INSERT INTO execution_tasks
                        (id, execution_id, objective, capabilities_json,
                         dependencies_json, expected_output, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'proposed', ?, ?)
                    """,
                    (
                        task["task_id"],
                        execution_id,
                        task["objective"],
                        json.dumps(task["required_capabilities"]),
                        json.dumps(task.get("depends_on", [])),
                        task["expected_output"],
                        now,
                        now,
                    ),
                )
        return self.get_execution(execution_id)

    def update_execution_task(
        self,
        execution_id: str,
        task_id: str,
        status: str,
        output: str | None = None,
        error: str | None = None,
        worker_id: str | None = None,
    ) -> None:
        allowed = {"proposed", "ready", "running", "succeeded", "failed", "blocked", "cancelled"}
        if status not in allowed:
            raise ValueError(f"unsupported task status: {status}")
        now = utc_now()
        with self.session() as connection:
            cursor = connection.execute(
                """
                UPDATE execution_tasks
                SET status = ?, output = ?, error = ?, worker_id = ?,
                    updated_at = ?
                WHERE execution_id = ? AND id = ?
                """,
                (
                    status, output, error, worker_id, now, execution_id, task_id
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("execution task does not exist")
            connection.execute(
                "UPDATE executions SET updated_at = ? WHERE id = ?",
                (now, execution_id),
            )

    def finish_execution(self, execution_id: str, status: str) -> None:
        if status not in {"succeeded", "failed", "partial"}:
            raise ValueError(f"unsupported execution status: {status}")
        now = utc_now()
        with self.session() as connection:
            cursor = connection.execute(
                "UPDATE executions SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, execution_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("execution does not exist")

    def get_execution(self, execution_id: str) -> dict[str, Any] | None:
        import json

        with self.session() as connection:
            row = connection.execute(
                """
                SELECT id, conversation_id, goal, plan_json, status, created_at, updated_at
                FROM executions WHERE id = ?
                """,
                (execution_id,),
            ).fetchone()
            if row is None:
                return None
            task_rows = connection.execute(
                """
                SELECT id, objective, capabilities_json, dependencies_json,
                       expected_output, status, output, error, worker_id,
                       created_at, updated_at
                FROM execution_tasks WHERE execution_id = ?
                ORDER BY rowid ASC
                """,
                (execution_id,),
            ).fetchall()
        result = dict(row)
        result["plan"] = json.loads(result.pop("plan_json"))
        result["tasks"] = []
        for task_row in task_rows:
            task = dict(task_row)
            task["task_id"] = task.pop("id")
            task["required_capabilities"] = json.loads(task.pop("capabilities_json"))
            task["depends_on"] = json.loads(task.pop("dependencies_json"))
            result["tasks"].append(task)
        return result
