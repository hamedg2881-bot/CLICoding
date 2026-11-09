from clicoding_orchestrator.registry import Worker, WorkerRegistry
from clicoding_orchestrator.router import CapabilityRouter, NoEligibleWorker


def test_router_selects_enabled_worker_by_capability_and_priority():
    registry = WorkerRegistry(
        [
            Worker("vision", "http://vision/v1", "vision-model", frozenset({"vision"}), True, 5),
            Worker("coder-slow", "http://coder2/v1", "coder2", frozenset({"coding"}), True, 20),
            Worker("coder-fast", "http://coder1/v1", "coder1", frozenset({"coding"}), True, 10),
            Worker("disabled", "http://disabled/v1", "disabled", frozenset({"coding"}), False, 0),
        ]
    )
    router = CapabilityRouter(registry)

    assert router.select("coding").id == "coder-fast"
    assert router.select("vision").id == "vision"


def test_router_never_selects_disabled_or_wrong_capability_worker():
    registry = WorkerRegistry(
        [Worker("chat-only", "http://chat/v1", "chat", frozenset({"chat"}), True)]
    )
    router = CapabilityRouter(registry)

    try:
        router.select("coding")
        assert False, "Expected NoEligibleWorker"
    except NoEligibleWorker:
        pass
