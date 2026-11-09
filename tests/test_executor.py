import asyncio

from clicoding_orchestrator.executor import SequentialWaveExecutor
from clicoding_orchestrator.planning import ExecutionPlan


def make_plan():
    return ExecutionPlan.model_validate(
        {
            "goal": "Analyze and synthesize",
            "acceptance_criteria": ["All required tasks finish"],
            "tasks": [
                {
                    "task_id": "a",
                    "objective": "First independent analysis",
                    "required_capabilities": ["analysis"],
                    "expected_output": "Analysis A",
                },
                {
                    "task_id": "b",
                    "objective": "Second independent analysis",
                    "required_capabilities": ["vision"],
                    "expected_output": "Analysis B",
                },
                {
                    "task_id": "combine",
                    "objective": "Combine both analyses",
                    "required_capabilities": ["reasoning"],
                    "depends_on": ["a", "b"],
                    "expected_output": "Combined result",
                },
            ],
        }
    )


def test_executor_runs_independent_tasks_and_passes_dependency_outputs():
    async def scenario():
        started = set()
        overlap_observed = asyncio.Event()

        async def runner(task, dependencies):
            started.add(task.task_id)
            if {"a", "b"}.issubset(started):
                overlap_observed.set()
            await asyncio.sleep(0.01)
            if task.task_id == "combine":
                assert dependencies == {"a": "result-a", "b": "result-b"}
                return "combined"
            return f"result-{task.task_id}"

        result = await SequentialWaveExecutor(max_parallel_tasks=2).execute(
            make_plan(), runner
        )
        assert overlap_observed.is_set()
        assert result.succeeded
        assert result.results["combine"].output == "combined"

    asyncio.run(scenario())


def test_executor_blocks_dependants_but_retains_successful_sibling():
    async def scenario():
        async def runner(task, dependencies):
            if task.task_id == "a":
                raise RuntimeError("worker unavailable")
            return "result-b"

        result = await SequentialWaveExecutor(max_parallel_tasks=2).execute(
            make_plan(), runner
        )
        assert result.results["a"].status == "failed"
        assert result.results["b"].status == "succeeded"
        assert result.results["combine"].status == "blocked"
        assert not result.succeeded

    asyncio.run(scenario())


def test_executor_rejects_zero_parallelism():
    try:
        SequentialWaveExecutor(max_parallel_tasks=0)
    except ValueError as exc:
        assert "at least one" in str(exc)
    else:
        raise AssertionError("zero parallelism must be rejected")
