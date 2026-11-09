from __future__ import annotations

from .registry import Worker, WorkerRegistry


class NoEligibleWorker(RuntimeError):
    pass


TASK_CAPABILITIES = {
    "chat": "chat",
    "coding": "coding",
    "review": "review",
    "vision": "vision",
}


class CapabilityRouter:
    """Deterministic routing by declared capability and operator priority."""

    def __init__(self, registry: WorkerRegistry):
        self.registry = registry

    def select(self, task_type: str) -> Worker:
        capability = TASK_CAPABILITIES.get(task_type)
        if capability is None:
            raise NoEligibleWorker(f"Unsupported task type: {task_type}")
        return self.select_capabilities({capability})

    def select_capabilities(self, required: set[str] | frozenset[str]) -> Worker:
        if not required or any(not value.strip() for value in required):
            raise NoEligibleWorker("At least one non-empty capability is required")
        candidates = [
            worker
            for worker in self.registry.enabled_workers()
            if required.issubset(worker.capabilities)
        ]
        if not candidates:
            names = ", ".join(sorted(required))
            raise NoEligibleWorker(
                f"No enabled worker supports all required capabilities: {names}"
            )
        return sorted(candidates, key=lambda worker: (worker.priority, worker.id))[0]

    def planning_worker(self) -> Worker:
        # Explicit planning capability is preferred; chat is the safe fallback.
        try:
            return self.select_capabilities({"planning"})
        except NoEligibleWorker:
            return self.select_capabilities({"chat"})
