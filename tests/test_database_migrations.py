import sqlite3

from clicoding_orchestrator.database import Database


def _create_legacy_database(path):
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE conversations (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE conversation_events (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                sequence INTEGER NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'tool')),
                content TEXT NOT NULL,
                producer TEXT,
                created_at TEXT NOT NULL,
                UNIQUE (conversation_id, sequence)
            );
            CREATE TABLE execution_tasks (
                id TEXT NOT NULL,
                execution_id TEXT NOT NULL,
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
            CREATE TABLE sandbox_approvals (
                id TEXT PRIMARY KEY,
                execution_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                language TEXT NOT NULL CHECK (
                    language IN ('python', 'javascript', 'go', 'rust', 'java', 'c')
                ),
                source TEXT NOT NULL,
                source_sha256 TEXT NOT NULL,
                status TEXT NOT NULL CHECK (
                    status IN ('pending', 'approved', 'rejected', 'expired')
                ),
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                decided_at TEXT,
                FOREIGN KEY (execution_id, task_id)
                    REFERENCES execution_tasks(execution_id, id) ON DELETE CASCADE
            );
            CREATE INDEX ix_sandbox_approvals_task
                ON sandbox_approvals(execution_id, task_id, created_at);

            INSERT INTO conversations VALUES ('conversation-1', 'created', 'updated');
            INSERT INTO conversation_events
                (id, conversation_id, sequence, role, content, producer, created_at)
                VALUES ('event-1', 'conversation-1', 1, 'user', 'legacy event', NULL, 'created');
            INSERT INTO execution_tasks (
                id, execution_id, objective, capabilities_json, dependencies_json,
                expected_output, status, created_at, updated_at
            ) VALUES (
                'task-1', 'execution-1', 'legacy task', '[]', '[]',
                'result', 'completed', 'created', 'updated'
            );
            INSERT INTO sandbox_approvals (
                id, execution_id, task_id, language, source, source_sha256,
                status, created_at, expires_at
            ) VALUES (
                'approval-1', 'execution-1', 'task-1', 'python', 'print(1)', 'abc',
                'approved', 'created', 'expires'
            );
            """
        )
        connection.commit()
    finally:
        connection.close()


def test_legacy_schema_migrations_preserve_records_and_are_idempotent(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    _create_legacy_database(path)

    database = Database(str(path))
    with database.session() as connection:
        conversation_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(conversations)")
        }
        event_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(conversation_events)")
        }
        assert "owner_id" in conversation_columns
        assert "metadata_json" in event_columns
        assert connection.execute(
            "SELECT owner_id FROM conversations WHERE id = 'conversation-1'"
        ).fetchone()["owner_id"] == "shared"
        assert connection.execute(
            "SELECT content FROM conversation_events WHERE id = 'event-1'"
        ).fetchone()["content"] == "legacy event"
        legacy_approval = connection.execute(
            "SELECT language, source, status FROM sandbox_approvals WHERE id = 'approval-1'"
        ).fetchone()
        assert tuple(legacy_approval) == ("python", "print(1)", "approved")

        connection.execute(
            """
            INSERT INTO sandbox_approvals (
                id, execution_id, task_id, language, source, source_sha256,
                status, created_at, expires_at
            ) VALUES (
                'approval-2', 'execution-1', 'task-1', 'typescript', 'const x = 1',
                'def', 'pending', 'created', 'expires'
            )
            """
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

    # A second startup must not rebuild the table or lose either approval.
    Database(str(path))
    with database.session() as connection:
        approvals = connection.execute(
            "SELECT id FROM sandbox_approvals ORDER BY id"
        ).fetchall()
        assert [row["id"] for row in approvals] == ["approval-1", "approval-2"]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
