from clicoding_orchestrator.database import Database


def sample_plan():
    return {
        "goal": "Analyze and implement",
        "acceptance_criteria": ["Tests pass"],
        "tasks": [
            {
                "task_id": "inspect",
                "objective": "Inspect source",
                "required_capabilities": ["coding"],
                "depends_on": [],
                "expected_output": "Findings",
            },
            {
                "task_id": "implement",
                "objective": "Implement based on findings",
                "required_capabilities": ["coding"],
                "depends_on": ["inspect"],
                "expected_output": "Patch",
            },
        ],
    }


def test_execution_plan_and_task_states_survive_database_reopen(tmp_path):
    path = str(tmp_path / "executions.sqlite3")
    db = Database(path)
    conversation = db.create_conversation()
    execution = db.create_execution(conversation["id"], sample_plan())
    execution_id = execution["id"]

    db.update_execution_task(
        execution_id, "inspect", "succeeded", output="Found the target module",
        worker_id="coder-a"
    )
    db.update_execution_task(
        execution_id, "implement", "running", worker_id="coder-b"
    )
    db.finish_execution(execution_id, "partial")

    reopened = Database(path)
    restored = reopened.get_execution(execution_id)

    assert restored is not None
    assert restored["conversation_id"] == conversation["id"]
    assert restored["status"] == "partial"
    assert restored["plan"]["goal"] == "Analyze and implement"
    assert restored["tasks"][0]["status"] == "succeeded"
    assert restored["tasks"][0]["output"] == "Found the target module"
    assert restored["tasks"][0]["worker_id"] == "coder-a"
    assert restored["tasks"][1]["status"] == "running"


def test_execution_requires_existing_conversation(tmp_path):
    db = Database(str(tmp_path / "executions.sqlite3"))
    try:
        db.create_execution("missing-conversation", sample_plan())
    except ValueError as exc:
        assert "conversation does not exist" in str(exc)
    else:
        raise AssertionError("execution must be tied to a real conversation")


def test_finish_execution_rejects_unknown_execution(tmp_path):
    db = Database(str(tmp_path / "executions.sqlite3"))
    try:
        db.finish_execution("missing-execution", "succeeded")
    except ValueError as exc:
        assert "does not exist" in str(exc)
    else:
        raise AssertionError("unknown execution must not be silently ignored")
