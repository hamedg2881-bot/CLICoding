import asyncio
import json

from clicoding_orchestrator.database import Database
from clicoding_orchestrator.gateway import Completion
from clicoding_orchestrator.orchestration import MultiModelOrchestrator
from clicoding_orchestrator.registry import Worker, WorkerRegistry
from clicoding_orchestrator.router import CapabilityRouter


class FakeGateway:
    def __init__(self, worker_id):
        self.worker_id = worker_id
        self.calls = 0

    async def complete(self, messages):
        self.calls += 1
        if self.worker_id == "planner":
            plan = {
                "goal": "Analyze source and visual design",
                "acceptance_criteria": ["Both analyses are combined"],
                "tasks": [
                    {
                        "task_id": "source",
                        "objective": "Inspect source code",
                        "required_capabilities": ["coding"],
                        "expected_output": "Code findings",
                    },
                    {
                        "task_id": "visual",
                        "objective": "Inspect screenshot",
                        "required_capabilities": ["vision"],
                        "expected_output": "Visual findings",
                    },
                ],
            }
            return Completion(json.dumps(plan), "planner-model")
        if self.worker_id == "synth":
            return Completion("Unified answer", "synth-model")
        return Completion(f"result-from-{self.worker_id}", self.worker_id)


def test_orchestrator_dispatches_workers_and_persists_plan_and_results(tmp_path):
    async def scenario():
        db = Database(str(tmp_path / "orchestration.sqlite3"))
        conversation = db.create_conversation()
        registry = WorkerRegistry(
            [
                Worker("planner", "http://planner/v1", "planner", frozenset({"chat"}), priority=1),
                Worker("coder", "http://coder/v1", "coder", frozenset({"coding"})),
                Worker("vision", "http://vision/v1", "vision", frozenset({"vision"})),
                Worker("synth", "http://synth/v1", "synth", frozenset({"chat"}), priority=2),
            ]
        )
        gateways = {}

        def gateway_factory(worker):
            gateways.setdefault(worker.id, FakeGateway(worker.id))
            return gateways[worker.id]

        orchestrator = MultiModelOrchestrator(
            db, CapabilityRouter(registry), max_parallel_tasks=2,
            gateway_factory=gateway_factory,
        )
        result = await orchestrator.run(
            conversation["id"], "Review the code and screenshot", "Prior chat context"
        )

        assert result.answer == "Unified answer"
        assert result.execution.succeeded
        saved = db.get_execution(result.execution_id)
        assert saved["status"] == "succeeded"
        assert {task["worker_id"] for task in saved["tasks"]} == {"coder", "vision"}
        assert {task["status"] for task in saved["tasks"]} == {"succeeded"}
        assert gateways["planner"].calls == 1
        assert gateways["synth"].calls == 1

    asyncio.run(scenario())
