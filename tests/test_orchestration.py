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
                Worker("planner", "http://planner/v1", "planner", frozenset({"planning"}), priority=1),
                Worker("coder", "http://coder/v1", "coder", frozenset({"coding"})),
                Worker("vision", "http://vision/v1", "vision", frozenset({"vision"})),
                Worker("synth", "http://synth/v1", "synth", frozenset({"chat"}), priority=1),
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


def test_orchestrator_returns_worker_outputs_when_no_synthesis_worker_exists(tmp_path):
    async def scenario():
        db = Database(str(tmp_path / "orchestration-no-synth.sqlite3"))
        conversation = db.create_conversation()
        registry = WorkerRegistry(
            [
                Worker("planner", "http://planner/v1", "planner", frozenset({"planning"}), priority=1),
                Worker("coder", "http://coder/v1", "coder", frozenset({"coding"})),
                Worker("vision", "http://vision/v1", "vision", frozenset({"vision"})),
            ]
        )

        def gateway_factory(worker):
            return FakeGateway(worker.id)

        orchestrator = MultiModelOrchestrator(
            db, CapabilityRouter(registry), max_parallel_tasks=2,
            gateway_factory=gateway_factory,
        )
        result = await orchestrator.run(
            conversation["id"], "Review the code and screenshot", ""
        )

        assert result.execution.succeeded
        assert "final synthesis was unavailable" in result.answer
        assert "result-from-coder" in result.answer
        assert "result-from-vision" in result.answer

    asyncio.run(scenario())


def test_worker_concurrency_limit_is_shared_across_parallel_executions(tmp_path):
    async def scenario():
        import asyncio

        from clicoding_orchestrator.gateway import Completion

        db = Database(str(tmp_path / "worker-concurrency.sqlite3"))
        registry = WorkerRegistry(
            [
                Worker(
                    "chat-alias", "http://chat/v1", "chat",
                    frozenset({"chat"}), max_concurrency=1
                ),
                Worker(
                    "planner-alias", "http://chat/v1", "chat",
                    frozenset({"planning"}), max_concurrency=1
                ),
            ]
        )
        active = 0
        maximum_active = 0

        class SlowGateway:
            async def complete(self, messages):
                nonlocal active, maximum_active
                active += 1
                maximum_active = max(maximum_active, active)
                await asyncio.sleep(0.02)
                active -= 1
                return Completion("ok", "chat")

        orchestrator = MultiModelOrchestrator(
            db, CapabilityRouter(registry),
            gateway_factory=lambda worker: SlowGateway(),
        )
        chat_worker, planner_worker = registry.enabled_workers()
        await asyncio.gather(
            orchestrator._complete(chat_worker, [{"role": "user", "content": "one"}]),
            orchestrator._complete(planner_worker, [{"role": "user", "content": "two"}]),
        )
        assert maximum_active == 1

    asyncio.run(scenario())


def test_three_model_topology_uses_small_model_for_planning_and_synthesis(tmp_path):
    async def scenario():
        db = Database(str(tmp_path / "three-model-topology.sqlite3"))
        conversation = db.create_conversation()
        registry = WorkerRegistry(
            [
                Worker(
                    "small-orchestrator", "http://small/v1", "small-fast",
                    frozenset({"chat", "planning"}), priority=1,
                ),
                Worker(
                    "coding-primary", "http://coder/v1", "qwen-coder",
                    frozenset({"coding", "review"}),
                ),
                Worker(
                    "vision-primary", "http://vision/v1", "qwen-vision",
                    frozenset({"vision"}),
                ),
            ]
        )

        class ThreeModelGateway:
            def __init__(self, worker_id):
                self.worker_id = worker_id
                self.calls = 0

            async def complete(self, messages):
                self.calls += 1
                if self.worker_id == "small-orchestrator" and self.calls == 1:
                    plan = {
                        "goal": "Review source and screenshot",
                        "acceptance_criteria": ["Both specialist results are combined"],
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
                    return Completion(json.dumps(plan), "small-fast")
                if self.worker_id == "small-orchestrator":
                    return Completion("One coherent response", "small-fast")
                return Completion(f"findings from {self.worker_id}", self.worker_id)

        gateways = {}

        def gateway_factory(worker):
            gateways.setdefault(worker.id, ThreeModelGateway(worker.id))
            return gateways[worker.id]

        orchestrator = MultiModelOrchestrator(
            db,
            CapabilityRouter(registry),
            max_parallel_tasks=2,
            gateway_factory=gateway_factory,
        )
        result = await orchestrator.run(
            conversation["id"], "Review this code and screenshot", "Earlier context"
        )

        assert result.answer == "One coherent response"
        assert result.execution.succeeded
        assert gateways["small-orchestrator"].calls == 2
        assert gateways["coding-primary"].calls == 1
        assert gateways["vision-primary"].calls == 1
        saved = db.get_execution(result.execution_id)
        assert {task["worker_id"] for task in saved["tasks"]} == {
            "coding-primary", "vision-primary"
        }

    asyncio.run(scenario())



def test_three_selected_worker_roles_route_chat_vision_and_coding(tmp_path):
    registry = WorkerRegistry(
        [
            Worker(
                "fast-reasoning", "http://fast/v1", "gpt-oss-20b",
                frozenset({"chat", "planning"}), priority=1,
            ),
            Worker(
                "coding-primary", "http://coder/v1", "qwen3-coder-next",
                frozenset({"coding", "review"}),
            ),
            Worker(
                "multimodal-primary", "http://multi/v1", "qwen3.6-35b-a3b",
                frozenset({"chat", "vision"}),
            ),
        ]
    )
    router = CapabilityRouter(registry)
    assert router.planning_worker().id == "fast-reasoning"
    assert router.select("coding").id == "coding-primary"
    assert router.select("vision").id == "multimodal-primary"
    assert router.select("chat").id == "fast-reasoning"



def test_orchestrator_forwards_image_bytes_only_to_multimodal_worker(tmp_path):
    async def scenario():
        db = Database(str(tmp_path / "orchestration-image.sqlite3"))
        conversation = db.create_conversation()
        registry = WorkerRegistry(
            [
                Worker(
                    "fast-reasoning", "http://fast/v1", "gpt-oss-20b",
                    frozenset({"chat", "planning"}), priority=1,
                ),
                Worker(
                    "coding-primary", "http://coder/v1", "qwen3-coder-next",
                    frozenset({"coding", "review"}),
                ),
                Worker(
                    "multimodal-primary", "http://multi/v1", "qwen3.6-35b-a3b",
                    frozenset({"chat", "vision"}),
                ),
            ]
        )
        image_uri = "data:image/png;base64,aGVsbG8="
        image_content = [
            {"type": "text", "text": "Inspect this screenshot"},
            {"type": "image_url", "image_url": {"url": image_uri}},
        ]
        seen = {}

        class ThreeModelGateway:
            def __init__(self, worker_id):
                self.worker_id = worker_id
                self.calls = 0

            async def complete(self, messages):
                self.calls += 1
                if self.worker_id == "fast-reasoning" and self.calls == 1:
                    plan = {
                        "goal": "Review code and screenshot",
                        "acceptance_criteria": ["Code and visual findings are combined"],
                        "tasks": [
                            {
                                "task_id": "source",
                                "objective": "Review the code",
                                "required_capabilities": ["coding"],
                                "expected_output": "Code findings",
                            },
                            {
                                "task_id": "visual",
                                "objective": "Inspect the supplied screenshot",
                                "required_capabilities": ["vision"],
                                "expected_output": "Visual findings",
                            },
                        ],
                    }
                    return Completion(json.dumps(plan), "gpt-oss-20b")
                if self.worker_id == "fast-reasoning":
                    return Completion("Unified response", "gpt-oss-20b")
                seen[self.worker_id] = messages[-1]["content"]
                return Completion(f"Findings from {self.worker_id}", self.worker_id)

        gateways = {}

        def gateway_factory(worker):
            gateways.setdefault(worker.id, ThreeModelGateway(worker.id))
            return gateways[worker.id]

        orchestrator = MultiModelOrchestrator(
            db, CapabilityRouter(registry), max_parallel_tasks=3,
            gateway_factory=gateway_factory,
        )
        result = await orchestrator.run(
            conversation["id"],
            "Inspect this screenshot and review the code",
            user_content=image_content,
        )

        assert result.execution.succeeded
        assert isinstance(seen["multimodal-primary"], list)
        assert seen["multimodal-primary"][-1]["image_url"]["url"] == image_uri
        assert isinstance(seen["coding-primary"], str)
        assert image_uri not in seen["coding-primary"]

    asyncio.run(scenario())


def test_code_artifact_contract_triggers_one_repair_attempt(tmp_path):
    async def scenario():
        db = Database(str(tmp_path / "artifact-repair.sqlite3"))
        conversation = db.create_conversation()
        registry = WorkerRegistry(
            [
                Worker(
                    "fast-reasoning", "http://fast/v1", "gpt-oss-20b",
                    frozenset({"chat", "planning"}), priority=1,
                ),
                Worker(
                    "coding-primary", "http://coder/v1", "qwen-coder",
                    frozenset({"coding"}),
                ),
            ]
        )

        class RepairGateway:
            def __init__(self, worker_id):
                self.worker_id = worker_id
                self.calls = 0

            async def complete(self, messages):
                self.calls += 1
                if self.worker_id == "fast-reasoning" and self.calls > 1:
                    return Completion(
                        "Implementation:\n\n```python\n"
                        "def find_duplicates(nums):\n"
                        "    counts = {}\n"
                        "    for value in nums:\n"
                        "        counts[value] = counts.get(value, 0) + 1\n"
                        "    return {key: count for key, count in counts.items() if count > 1}\n"
                        "```",
                        "gpt-oss-20b",
                    )
                if self.worker_id == "fast-reasoning":
                    plan = {
                        "goal": "Implement duplicate finder",
                        "acceptance_criteria": ["Return complete Python source code"],
                        "tasks": [
                            {
                                "task_id": "implement",
                                "objective": "Write find_duplicates(nums)",
                                "required_capabilities": ["coding"],
                                "expected_output": "def find_duplicates(nums):\n    return {}",
                            }
                        ],
                    }
                    return Completion(json.dumps(plan), "gpt-oss-20b")
                if self.calls == 1:
                    return Completion(
                        "The function counts values and returns duplicates.",
                        "qwen-coder",
                    )
                return Completion(
                    "def find_duplicates(nums):\n"
                    "    counts = {}\n"
                    "    for value in nums:\n"
                    "        counts[value] = counts.get(value, 0) + 1\n"
                    "    return {key: count for key, count in counts.items() if count > 1}",
                    "qwen-coder",
                )

        gateways = {}

        def gateway_factory(worker):
            gateways.setdefault(worker.id, RepairGateway(worker.id))
            return gateways[worker.id]

        orchestrator = MultiModelOrchestrator(
            db, CapabilityRouter(registry), gateway_factory=gateway_factory
        )
        result = await orchestrator.run(
            conversation["id"], "Write a Python duplicate finder"
        )

        assert result.execution.succeeded
        assert "def find_duplicates" in result.answer
        assert gateways["coding-primary"].calls == 2
        saved = db.get_execution(result.execution_id)
        assert saved["tasks"][0]["status"] == "succeeded"
        assert "def find_duplicates" in saved["tasks"][0]["output"]

    asyncio.run(scenario())



def test_full_original_request_and_acceptance_criteria_reach_workers_and_synthesis(tmp_path):
    async def scenario():
        db = Database(str(tmp_path / "full-request-contract.sqlite3"))
        conversation = db.create_conversation()
        registry = WorkerRegistry(
            [
                Worker(
                    "fast-reasoning", "http://fast/v1", "gpt-oss-20b",
                    frozenset({"chat", "planning"}), priority=1,
                ),
                Worker(
                    "coding-primary", "http://coder/v1", "qwen-coder",
                    frozenset({"coding"}),
                ),
            ]
        )
        original_request = (
            "Write find_duplicates(nums), return total occurrence counts for duplicates; "
            "for [1,2,2,3,3,3,4] the exact result must be {2: 2, 3: 3}. "
            "Include runnable code, exact output, and a Persian explanation."
        )
        captured = {}
        prior_context = (
            "user: برای تست حفظ مکالمه، این کد را به خاطر بسپار: "
            "CLICODING_SESSION_REGRESSION_20261111 فقط با کلمه OK پاسخ بده.\\n\\n"
            "assistant: OK"
        )

        class ContractGateway:
            def __init__(self, worker_id):
                self.worker_id = worker_id
                self.calls = 0

            async def complete(self, messages):
                self.calls += 1
                if self.worker_id == "fast-reasoning" and self.calls == 1:
                    plan = {
                        "goal": "Implement a duplicate finder",
                        "acceptance_criteria": [
                            "Return total occurrence counts",
                            "Include exact example output {2: 2, 3: 3}",
                            "Include runnable code and Persian explanation",
                        ],
                        "tasks": [
                            {
                                "task_id": "implement",
                                "objective": "Write the function only",
                                "required_capabilities": ["coding"],
                                "expected_output": "def find_duplicates(nums):\n    return {}",
                            }
                        ],
                    }
                    return Completion(json.dumps(plan), "gpt-oss-20b")
                if self.worker_id == "coding-primary":
                    captured["worker_message"] = messages[-1]["content"]
                    return Completion(
                        "def find_duplicates(nums):\n"
                        "    counts = {}\n"
                        "    for value in nums:\n"
                        "        counts[value] = counts.get(value, 0) + 1\n"
                        "    return {value: count for value, count in counts.items() if count > 1}\n\n"
                        "خروجی: {2: 2, 3: 3}\n\n"
                        "این تابع تعداد کل رخداد هر عدد تکراری را برمی‌گرداند.",
                        "qwen-coder",
                    )
                captured["synthesis_system"] = messages[0]["content"]
                captured["synthesis_message"] = messages[-1]["content"]
                return Completion(
                    "def find_duplicates(nums):\n"
                    "    counts = {}\n"
                    "    for value in nums:\n"
                    "        counts[value] = counts.get(value, 0) + 1\n"
                    "    return {value: count for value, count in counts.items() if count > 1}\n\n"
                    "خروجی دقیق: {2: 2, 3: 3}\n\n"
                    "این تابع تعداد کل رخداد هر عدد تکراری را برمی‌گرداند.",
                    "gpt-oss-20b",
                )

        gateways = {}

        def gateway_factory(worker):
            gateways.setdefault(worker.id, ContractGateway(worker.id))
            return gateways[worker.id]

        orchestrator = MultiModelOrchestrator(
            db, CapabilityRouter(registry), gateway_factory=gateway_factory
        )
        result = await orchestrator.run(
            conversation["id"], original_request, prior_context
        )

        assert result.execution.succeeded
        assert original_request in captured["worker_message"]
        assert "Return total occurrence counts" in captured["worker_message"]
        assert original_request in captured["synthesis_message"]
        assert prior_context in captured["worker_message"]
        assert prior_context in captured["synthesis_message"]
        assert "quote exact prior values" in captured["synthesis_system"]
        assert "Never claim that information was persisted externally" in captured["synthesis_system"]
        assert "{2: 2, 3: 3}" in result.answer
        assert "def find_duplicates" in result.answer

    asyncio.run(scenario())

def test_code_artifact_detection_accepts_fenced_blocks_for_all_languages():
    languages = ("python", "javascript", "typescript", "go", "rust", "java",
                 "c", "cpp", "csharp", "php", "ruby")
    for language in languages:
        output = f"```{language}\n// implementation\n```"
        assert MultiModelOrchestrator._expects_code_artifact(
            f"Return a {language} source code block"
        )
        assert MultiModelOrchestrator._contains_code_artifact(output)



def test_specialist_context_compaction_is_worker_budgeted(tmp_path):
    async def scenario():
        db = Database(str(tmp_path / "specialist-context.sqlite3"))
        registry = WorkerRegistry([
            Worker(
                "small-coder", "http://coder/v1", "small-coder",
                frozenset({"coding"}), context_window_tokens=2048,
                max_output_tokens=256,
            )
        ])
        calls = []

        class SummarizingGateway:
            async def complete(self, messages):
                calls.append(messages)
                return Completion("Compact factual continuity summary.", "small-coder")

        orchestrator = MultiModelOrchestrator(
            db,
            CapabilityRouter(registry),
            gateway_factory=lambda worker: SummarizingGateway(),
        )
        worker = registry.enabled_workers()[0]
        long_context = "\n".join(
            f"Conversation fact {index}: preserve the exact project decision and value."
            for index in range(200)
        )
        compacted = await orchestrator._compact_context_for_worker(
            worker, long_context, reserved_input_tokens=256
        )

        assert compacted.count("Compact factual continuity summary.") == len(calls)
        assert "Conversation fact" not in compacted
        assert len(calls) > 1
        assert all(
            "untrusted data" in message[0]["content"]
            for message in calls
        )

    asyncio.run(scenario())


def test_orchestrator_emits_structured_progress_events(tmp_path):
    async def scenario():
        db = Database(str(tmp_path / "orchestration-progress.sqlite3"))
        conversation = db.create_conversation()
        registry = WorkerRegistry([
            Worker("planner", "http://planner/v1", "planner", frozenset({"planning"}), priority=1),
            Worker("coder", "http://coder/v1", "coder", frozenset({"coding"})),
            Worker("vision", "http://vision/v1", "vision", frozenset({"vision"})),
            Worker("synth", "http://synth/v1", "synth", frozenset({"chat"}), priority=1),
        ])
        gateways = {}

        def gateway_factory(worker):
            gateways.setdefault(worker.id, FakeGateway(worker.id))
            return gateways[worker.id]

        events = []
        orchestrator = MultiModelOrchestrator(
            db,
            CapabilityRouter(registry),
            max_parallel_tasks=2,
            gateway_factory=gateway_factory,
        )
        result = await orchestrator.run(
            conversation["id"],
            "Review code and screenshot",
            "Prior conversation facts",
            progress_callback=events.append,
        )

        assert result.execution.succeeded
        assert events[0]["type"] == "plan.created"
        assert events[0]["task_count"] == 2
        assert sum(event["type"] == "task.started" for event in events) == 2
        assert sum(event["type"] == "task.succeeded" for event in events) == 2
        assert events[-1]["type"] == "execution.completed"
        assert events[-1]["status"] == "succeeded"
        assert all(event["execution_id"] == result.execution_id for event in events)

    asyncio.run(scenario())
