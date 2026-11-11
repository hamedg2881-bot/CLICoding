from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
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
                    owner_id TEXT NOT NULL DEFAULT 'shared',
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
                    metadata_json TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE (conversation_id, sequence)
                );

                CREATE INDEX IF NOT EXISTS ix_events_conversation_sequence
                    ON conversation_events(conversation_id, sequence);

                CREATE TABLE IF NOT EXISTS conversation_summaries (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL
                        REFERENCES conversations(id) ON DELETE CASCADE,
                    through_sequence INTEGER NOT NULL,
                    summary TEXT NOT NULL,
                    model TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (conversation_id, through_sequence)
                );

                CREATE INDEX IF NOT EXISTS ix_summaries_conversation_sequence
                    ON conversation_summaries(conversation_id, through_sequence);

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

                CREATE TABLE IF NOT EXISTS sandbox_approvals (
                    id TEXT PRIMARY KEY,
                    execution_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    language TEXT NOT NULL CHECK (language IN ('python', 'javascript', 'typescript', 'go', 'rust', 'java', 'c', 'cpp', 'csharp', 'php', 'ruby')),
                    source TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected', 'expired')),
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    decided_at TEXT,
                    FOREIGN KEY (execution_id, task_id)
                        REFERENCES execution_tasks(execution_id, id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS ix_sandbox_approvals_task
                    ON sandbox_approvals(execution_id, task_id, created_at);
                """
            )
        self._migrate_event_metadata()
        self._migrate_conversation_owner()
        self._migrate_sandbox_approval_languages()

    def _migrate_conversation_owner(self) -> None:
        """Add conversation ownership; legacy records remain in the shared namespace."""
        with self.session() as connection:
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(conversations)").fetchall()
            }
            if "owner_id" not in columns:
                connection.execute(
                    "ALTER TABLE conversations ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'shared'"
                )

    def _migrate_event_metadata(self) -> None:
        """Add structured event metadata without rebuilding or losing old conversations."""
        with self.session() as connection:
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(conversation_events)").fetchall()
            }
            if "metadata_json" not in columns:
                connection.execute(
                    "ALTER TABLE conversation_events ADD COLUMN metadata_json TEXT"
                )

    def _migrate_sandbox_approval_languages(self) -> None:
        """Expand the SQLite language CHECK without losing existing approvals/results."""
        languages = (
            "'python', 'javascript', 'typescript', 'go', 'rust', 'java', "
            "'c', 'cpp', 'csharp', 'php', 'ruby'"
        )
        connection = self.connect()
        try:
            row = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='sandbox_approvals'"
            ).fetchone()
            if row is None or all(name in (row["sql"] or "") for name in (
                "'typescript'", "'csharp'", "'cpp'", "'ruby'"
            )):
                return
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                f"""
                CREATE TABLE sandbox_approvals_new (
                    id TEXT PRIMARY KEY,
                    execution_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    language TEXT NOT NULL CHECK (language IN ({languages})),
                    source TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected', 'expired')),
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    decided_at TEXT,
                    FOREIGN KEY (execution_id, task_id)
                        REFERENCES execution_tasks(execution_id, id) ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                INSERT INTO sandbox_approvals_new
                    (id, execution_id, task_id, language, source, source_sha256,
                     status, created_at, expires_at, decided_at)
                SELECT id, execution_id, task_id, language, source, source_sha256,
                       status, created_at, expires_at, decided_at
                FROM sandbox_approvals
                """
            )
            connection.execute("DROP TABLE sandbox_approvals")
            connection.execute("ALTER TABLE sandbox_approvals_new RENAME TO sandbox_approvals")
            connection.execute(
                "CREATE INDEX ix_sandbox_approvals_task "
                "ON sandbox_approvals(execution_id, task_id, created_at)"
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.close()

    @staticmethod
    def _validate_owner_id(owner_id: str) -> str:
        if not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 128:
            raise ValueError("owner_id must contain 1 to 128 non-whitespace characters")
        return owner_id

    def create_conversation(self, owner_id: str = "shared") -> dict[str, str]:
        owner_id = self._validate_owner_id(owner_id)
        conversation_id = str(uuid.uuid4())
        now = utc_now()
        with self.session() as connection:
            connection.execute(
                "INSERT INTO conversations (id, owner_id, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (conversation_id, owner_id, now, now),
            )
        return {
            "id": conversation_id,
            "owner_id": owner_id,
            "created_at": now,
            "updated_at": now,
        }

    def get_or_create_conversation(
        self, conversation_id: str, owner_id: str = "shared"
    ) -> dict[str, str]:
        """Return a caller-derived stable ID only within the authenticated owner namespace."""
        owner_id = self._validate_owner_id(owner_id)
        if not conversation_id or len(conversation_id) > 128:
            raise ValueError("conversation_id must contain 1 to 128 characters")
        now = utc_now()
        with self.session() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO conversations (id, owner_id, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (conversation_id, owner_id, now, now),
            )
            row = connection.execute(
                "SELECT id, owner_id, created_at, updated_at FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
        if row is None or row["owner_id"] != owner_id:
            raise PermissionError("conversation does not belong to the authenticated owner")
        return dict(row)

    def conversation_exists(
        self, conversation_id: str, owner_id: str | None = None
    ) -> bool:
        with self.session() as connection:
            if owner_id is None:
                row = connection.execute(
                    "SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT 1 FROM conversations WHERE id = ? AND owner_id = ?",
                    (conversation_id, self._validate_owner_id(owner_id)),
                ).fetchone()
        return row is not None

    def append_event(
        self,
        conversation_id: str,
        role: str,
        content: str,
        producer: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if role not in {"user", "assistant", "tool"}:
            raise ValueError("unsupported conversation event role")
        if not isinstance(content, str):
            raise TypeError("conversation event content must be text")
        metadata_json = (
            json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))
            if metadata is not None else None
        )
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
                    (id, conversation_id, sequence, role, content, producer,
                     metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id, conversation_id, sequence, role, content, producer,
                    metadata_json, now,
                ),
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
            "metadata": metadata,
            "created_at": now,
        }

    def list_events(self, conversation_id: str) -> list[dict[str, Any]]:
        with self.session() as connection:
            rows = connection.execute(
                """
                SELECT id, conversation_id, sequence, role, content, producer,
                       metadata_json, created_at
                FROM conversation_events
                WHERE conversation_id = ?
                ORDER BY sequence ASC
                """,
                (conversation_id,),
            ).fetchall()
        events = []
        for row in rows:
            event = dict(row)
            raw_metadata = event.pop("metadata_json", None)
            if raw_metadata:
                try:
                    metadata = json.loads(raw_metadata)
                except json.JSONDecodeError:
                    raise RuntimeError(
                        f"Conversation event {event['id']} contains invalid metadata JSON"
                    )
                if not isinstance(metadata, dict):
                    raise RuntimeError(
                        f"Conversation event {event['id']} metadata must be an object"
                    )
                event["metadata"] = metadata
            else:
                event["metadata"] = None
            events.append(event)
        return events

    def get_latest_summary(self, conversation_id: str) -> dict[str, Any] | None:
        with self.session() as connection:
            row = connection.execute(
                """
                SELECT id, conversation_id, through_sequence, summary, model, created_at
                FROM conversation_summaries
                WHERE conversation_id = ?
                ORDER BY through_sequence DESC
                LIMIT 1
                """,
                (conversation_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def save_summary(
        self,
        conversation_id: str,
        through_sequence: int,
        summary: str,
        model: str,
    ) -> dict[str, Any]:
        if not isinstance(through_sequence, int) or isinstance(through_sequence, bool) or through_sequence < 1:
            raise ValueError("through_sequence must be a positive event sequence")
        if not isinstance(summary, str) or not summary.strip() or len(summary) > 24000:
            raise ValueError("summary must contain 1 to 24000 characters")
        if not isinstance(model, str) or not model.strip() or len(model) > 256:
            raise ValueError("model must contain 1 to 256 characters")
        summary_id = str(uuid.uuid4())
        now = utc_now()
        with self.session() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone() is None:
                raise ValueError("conversation does not exist")
            event = connection.execute(
                "SELECT 1 FROM conversation_events WHERE conversation_id = ? AND sequence = ?",
                (conversation_id, through_sequence),
            ).fetchone()
            if event is None:
                raise ValueError("summary boundary must reference an existing conversation event")
            latest = connection.execute(
                "SELECT through_sequence FROM conversation_summaries "
                "WHERE conversation_id = ? ORDER BY through_sequence DESC LIMIT 1",
                (conversation_id,),
            ).fetchone()
            if latest is not None and through_sequence <= latest["through_sequence"]:
                raise ValueError("summary boundary must advance beyond the previous summary")
            connection.execute(
                """
                INSERT INTO conversation_summaries
                    (id, conversation_id, through_sequence, summary, model, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (summary_id, conversation_id, through_sequence, summary, model, now),
            )
        return {
            "id": summary_id,
            "conversation_id": conversation_id,
            "through_sequence": through_sequence,
            "summary": summary,
            "model": model,
            "created_at": now,
        }

    def get_conversation(
        self, conversation_id: str, owner_id: str | None = None
    ) -> dict[str, Any] | None:
        with self.session() as connection:
            if owner_id is None:
                row = connection.execute(
                    "SELECT id, owner_id, created_at, updated_at FROM conversations WHERE id = ?",
                    (conversation_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT id, owner_id, created_at, updated_at FROM conversations "
                    "WHERE id = ? AND owner_id = ?",
                    (conversation_id, self._validate_owner_id(owner_id)),
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


    def create_sandbox_approval(
        self, execution_id: str, task_id: str, language: str, source: str,
        ttl_seconds: int = 900,
    ) -> dict[str, Any]:
        """Create a pending approval bound to an immutable source snapshot."""
        if language not in {"python", "javascript", "typescript", "go", "rust", "java", "c", "cpp", "csharp", "php", "ruby"}:
            raise ValueError("unsupported sandbox language")
        if not isinstance(source, str) or not source.strip():
            raise ValueError("source must be a non-empty string")
        source_bytes = source.encode("utf-8")
        if len(source_bytes) > 128 * 1024:
            raise ValueError("source exceeds 131072 bytes")
        if ttl_seconds < 60 or ttl_seconds > 3600:
            raise ValueError("approval TTL must be between 60 and 3600 seconds")
        now = datetime.now(timezone.utc)
        created_at = now.isoformat()
        expires_at = (now + timedelta(seconds=ttl_seconds)).isoformat()
        approval_id = str(uuid.uuid4())
        digest = hashlib.sha256(source_bytes).hexdigest()
        with self.session() as connection:
            task = connection.execute(
                "SELECT status FROM execution_tasks WHERE execution_id = ? AND id = ?",
                (execution_id, task_id),
            ).fetchone()
            if task is None:
                raise ValueError("execution task does not exist")
            if task["status"] != "succeeded":
                raise ValueError("only a succeeded task can request sandbox approval")
            connection.execute(
                """
                INSERT INTO sandbox_approvals
                    (id, execution_id, task_id, language, source, source_sha256,
                     status, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (approval_id, execution_id, task_id, language, source, digest,
                 created_at, expires_at),
            )
        return self.get_sandbox_approval(approval_id)

    def get_sandbox_approval(self, approval_id: str) -> dict[str, Any] | None:
        with self.session() as connection:
            row = connection.execute(
                """
                SELECT id, execution_id, task_id, language, source, source_sha256,
                       status, created_at, expires_at, decided_at
                FROM sandbox_approvals WHERE id = ?
                """,
                (approval_id,),
            ).fetchone()
            if row is None:
                return None
            result = dict(row)
            if result["status"] == "pending" and result["expires_at"] <= utc_now():
                connection.execute(
                    "UPDATE sandbox_approvals SET status = 'expired' WHERE id = ? AND status = 'pending'",
                    (approval_id,),
                )
                result["status"] = "expired"
        return result

    def list_sandbox_approvals(
        self, execution_id: str, task_id: str
    ) -> list[dict[str, Any]]:
        with self.session() as connection:
            rows = connection.execute(
                """
                SELECT id FROM sandbox_approvals
                WHERE execution_id = ? AND task_id = ?
                ORDER BY created_at DESC
                """,
                (execution_id, task_id),
            ).fetchall()
        return [
            approval for row in rows
            if (approval := self.get_sandbox_approval(row["id"])) is not None
        ]

    def decide_sandbox_approval(
        self, approval_id: str, decision: str
    ) -> dict[str, Any] | None:
        if decision not in {"approve", "reject"}:
            raise ValueError("decision must be approve or reject")
        now = utc_now()
        target = "approved" if decision == "approve" else "rejected"
        expired = False
        with self.session() as connection:
            row = connection.execute(
                "SELECT status, expires_at FROM sandbox_approvals WHERE id = ?",
                (approval_id,),
            ).fetchone()
            if row is None:
                return None
            if row["status"] != "pending":
                raise ValueError(f"approval is not pending (status={row['status']})")
            if row["expires_at"] <= now:
                connection.execute(
                    "UPDATE sandbox_approvals SET status = 'expired' WHERE id = ? AND status = 'pending'",
                    (approval_id,),
                )
                expired = True
            else:
                cursor = connection.execute(
                    """
                    UPDATE sandbox_approvals
                    SET status = ?, decided_at = ?
                    WHERE id = ? AND status = 'pending' AND expires_at > ?
                    """,
                    (target, now, approval_id, now),
                )
                if cursor.rowcount != 1:
                    raise ValueError("approval state changed; fetch it and try again")
        if expired:
            raise ValueError("approval has expired")
        return self.get_sandbox_approval(approval_id)

    def get_sandbox_execution_result(self, approval_id: str) -> dict[str, Any] | None:
        """Read the structured result persisted by the privileged broker."""
        import json

        with self.session() as connection:
            try:
                row = connection.execute(
                    """
                    SELECT approval_id, source_sha256, status, result_json, created_at
                    FROM sandbox_execution_results WHERE approval_id = ?
                    """,
                    (approval_id,),
                ).fetchone()
            except sqlite3.OperationalError:
                # The broker creates this table on its first accepted execution.
                return None
        if row is None:
            return None
        result = dict(row)
        result["result"] = json.loads(result.pop("result_json"))
        return result
