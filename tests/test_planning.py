import pytest
from pydantic import ValidationError

from clicoding_orchestrator.planning import ExecutionPlan


def test_plan_groups_independent_tasks_into_parallel_waves():
    plan = ExecutionPlan.model_validate(
        {
            "goal": "Review a feature and prepare a safe implementation",
            "acceptance_criteria": ["All findings are addressed", "Tests pass"],
            "tasks": [
                {
                    "task_id": "inspect-code",
                    "objective": "Inspect the relevant source",
                    "required_capabilities": ["coding"],
                    "expected_output": "Source findings",
                },
                {
                    "task_id": "inspect-design",
                    "objective": "Inspect the supplied UI screenshot",
                    "required_capabilities": ["vision"],
                    "expected_output": "Visual findings",
                },
                {
                    "task_id": "synthesize",
                    "objective": "Combine findings into an implementation plan",
                    "required_capabilities": ["reasoning"],
                    "depends_on": ["inspect-code", "inspect-design"],
                    "expected_output": "Unified plan",
                },
                {
                    "task_id": "review",
                    "objective": "Review the proposed plan",
                    "required_capabilities": ["review"],
                    "depends_on": ["synthesize"],
                    "expected_output": "Review report",
                },
            ],
        }
    )

    assert plan.topological_batches() == [
        ["inspect-code", "inspect-design"],
        ["synthesize"],
        ["review"],
    ]


@pytest.mark.parametrize(
    "tasks",
    [
        [
            {
                "task_id": "a",
                "objective": "A",
                "required_capabilities": ["chat"],
                "depends_on": ["missing"],
                "expected_output": "A result",
            }
        ],
        [
            {
                "task_id": "a",
                "objective": "A",
                "required_capabilities": ["chat"],
                "depends_on": ["b"],
                "expected_output": "A result",
            },
            {
                "task_id": "b",
                "objective": "B",
                "required_capabilities": ["chat"],
                "depends_on": ["a"],
                "expected_output": "B result",
            },
        ],
    ],
)
def test_plan_rejects_unknown_dependencies_and_cycles(tasks):
    with pytest.raises(ValidationError):
        ExecutionPlan.model_validate(
            {
                "goal": "Invalid plan",
                "acceptance_criteria": ["Must be valid"],
                "tasks": tasks,
            }
        )


def test_plan_rejects_duplicate_task_ids():
    task = {
        "task_id": "same",
        "objective": "Do work",
        "required_capabilities": ["chat"],
        "expected_output": "Result",
    }
    with pytest.raises(ValidationError):
        ExecutionPlan.model_validate(
            {
                "goal": "Duplicate IDs",
                "acceptance_criteria": ["Unique IDs"],
                "tasks": [task, task],
            }
        )
