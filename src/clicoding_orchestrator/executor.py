from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable

from .planning import ExecutionPlan, PlannedTask


@dataclass(frozen=True)
class TaskResult:
    task_id: str
    status: str
    output: str | None = None
    error: str | None = None


TaskRunner = Callable[
    [PlannedTask, dict[str, str]], Awaitable[str]
]


@dataclass(frozen=True)
class PlanExecution:
    results: dict[str, TaskResult]

    @property
    def succeeded(self) -> bool:
        return all(result.status == "succeeded" for result in self.results.values())


class SequentialWaveExecutor:
    """Executes dependency waves with bounded parallelism and partial-result retention.

    Tasks in one wave may run concurrently. A task is blocked when any dependency
    did not succeed. The executor never starts a dependent task after a failure.
    """

    def __init__(self, max_parallel_tasks: int = 3):
        if max_parallel_tasks < 1:
            raise ValueError("max_parallel_tasks must be at least one")
        self.max_parallel_tasks = max_parallel_tasks

    async def execute(
        self, plan: ExecutionPlan, runner: TaskRunner
    ) -> PlanExecution:
        tasks = plan.task_map()
        results: dict[str, TaskResult] = {}
        semaphore = asyncio.Semaphore(self.max_parallel_tasks)

        async def run_one(task: PlannedTask) -> TaskResult:
            failed_dependencies = [
                dependency
                for dependency in task.depends_on
                if results[dependency].status != "succeeded"
            ]
            if failed_dependencies:
                return TaskResult(
                    task_id=task.task_id,
                    status="blocked",
                    error=(
                        "Dependencies did not succeed: "
                        + ", ".join(failed_dependencies)
                    ),
                )

            dependency_outputs = {
                dependency: results[dependency].output or ""
                for dependency in task.depends_on
            }
            async with semaphore:
                try:
                    output = await runner(task, dependency_outputs)
                    if not isinstance(output, str) or not output.strip():
                        raise ValueError("Task runner returned an empty/non-text result")
                    return TaskResult(
                        task_id=task.task_id, status="succeeded", output=output
                    )
                except Exception as exc:
                    return TaskResult(
                        task_id=task.task_id,
                        status="failed",
                        error=f"{type(exc).__name__}: {exc}",
                    )

        for wave in plan.topological_batches():
            # Every task in a wave depends only on earlier waves, so this read-only
            # dependency snapshot is safe while tasks in this wave execute.
            wave_results = await asyncio.gather(
                *(run_one(tasks[task_id]) for task_id in wave)
            )
            for result in wave_results:
                results[result.task_id] = result

        return PlanExecution(results=results)
