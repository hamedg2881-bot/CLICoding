from __future__ import annotations

from collections import defaultdict, deque
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


TaskStatus = Literal[
    "proposed", "ready", "running", "succeeded", "failed", "blocked", "cancelled"
]


class PlannedTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
    objective: str = Field(min_length=1, max_length=8000)
    required_capabilities: list[str] = Field(min_length=1, max_length=8)
    depends_on: list[str] = Field(default_factory=list, max_length=32)
    expected_output: str = Field(min_length=1, max_length=2000)


class ExecutionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1, max_length=8000)
    acceptance_criteria: list[str] = Field(min_length=1, max_length=32)
    tasks: list[PlannedTask] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_graph(self) -> "ExecutionPlan":
        ids = [task.task_id for task in self.tasks]
        if len(ids) != len(set(ids)):
            raise ValueError("task_id values must be unique")
        known = set(ids)
        for task in self.tasks:
            if task.task_id in task.depends_on:
                raise ValueError(f"task {task.task_id!r} cannot depend on itself")
            unknown = set(task.depends_on) - known
            if unknown:
                raise ValueError(
                    f"task {task.task_id!r} has unknown dependencies: {sorted(unknown)}"
                )
            if len(task.depends_on) != len(set(task.depends_on)):
                raise ValueError(f"task {task.task_id!r} has duplicate dependencies")
            if not all(value.strip() for value in task.required_capabilities):
                raise ValueError(f"task {task.task_id!r} has an empty capability")
        self.topological_batches()
        return self

    def topological_batches(self) -> list[list[str]]:
        """Return dependency-safe waves; tasks in a wave are mutually independent."""
        by_id = {task.task_id: task for task in self.tasks}
        indegree = {task_id: len(task.depends_on) for task_id, task in by_id.items()}
        dependants: dict[str, list[str]] = defaultdict(list)
        for task in self.tasks:
            for dependency in task.depends_on:
                dependants[dependency].append(task.task_id)

        ready = deque(
            task.task_id for task in self.tasks if indegree[task.task_id] == 0
        )
        batches: list[list[str]] = []
        visited = 0
        while ready:
            batch = list(ready)
            ready.clear()
            batches.append(batch)
            visited += len(batch)
            next_ready: list[str] = []
            for task_id in batch:
                for dependant in dependants[task_id]:
                    indegree[dependant] -= 1
                    if indegree[dependant] == 0:
                        next_ready.append(dependant)
            ready.extend(next_ready)

        if visited != len(self.tasks):
            raise ValueError("execution plan contains a dependency cycle")
        return batches

    def task_map(self) -> dict[str, PlannedTask]:
        return {task.task_id: task for task in self.tasks}
