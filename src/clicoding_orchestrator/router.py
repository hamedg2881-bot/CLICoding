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
        candidates = [
            worker
            for worker in self.registry.enabled_workers()
            if capability in worker.capabilities
        ]
        if not candidates:
            raise NoEligibleWorker(
                f"No enabled worker supports the required capability: {capability}"
            )
        return sorted(candidates, key=lambda worker: (worker.priority, worker.id))[0]
