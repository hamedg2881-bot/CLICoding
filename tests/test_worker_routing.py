import os

from clicoding_orchestrator.registry import RegistryError, Worker, WorkerRegistry
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


def test_yaml_registry_reads_secret_from_environment_and_hides_it(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_CLICODING_WORKER_KEY", "do-not-display")
    path = tmp_path / "workers.yaml"
    path.write_text(
        "workers:\n"
        "  - id: coder\n"
        "    base_url: http://localhost:8000/v1/\n"
        "    model: coder-alias\n"
        "    capabilities: [chat, coding]\n"
        "    enabled: true\n"
        "    api_key_env: TEST_CLICODING_WORKER_KEY\n",
        encoding="utf-8",
    )

    registry = WorkerRegistry.from_yaml(str(path))
    assert registry.enabled_workers()[0].api_key == "do-not-display"
    assert "do-not-display" not in str(registry.describe())


def test_yaml_registry_rejects_non_list_capabilities(tmp_path):
    path = tmp_path / "workers.yaml"
    path.write_text(
        "workers:\n"
        "  - id: bad\n"
        "    base_url: http://localhost:8000/v1\n"
        "    model: bad\n"
        "    capabilities: coding\n",
        encoding="utf-8",
    )
    try:
        WorkerRegistry.from_yaml(str(path))
        assert False, "Expected RegistryError"
    except RegistryError:
        pass


def test_registry_rejects_non_http_worker_endpoint():
    try:
        WorkerRegistry([
            Worker("bad", "file:///etc/passwd", "model", frozenset({"chat"}))
        ])
    except RegistryError as exc:
        assert "HTTP(S)" in str(exc)
    else:
        raise AssertionError("non-HTTP worker endpoint must be rejected")


def test_registry_rejects_invalid_worker_concurrency():
    try:
        WorkerRegistry([
            Worker(
                "bad", "http://localhost:8000/v1", "model",
                frozenset({"chat"}), max_concurrency=0
            )
        ])
    except RegistryError as exc:
        assert "invalid limits" in str(exc)
    else:
        raise AssertionError("zero worker concurrency must be rejected")


def test_yaml_registry_rejects_missing_secret_environment_variable(tmp_path, monkeypatch):
    monkeypatch.delenv("MISSING_CLICODING_SECRET", raising=False)
    path = tmp_path / "workers.yaml"
    path.write_text(
        "workers:\\n"
        "  - id: coder\\n"
        "    base_url: http://localhost:8000/v1\\n"
        "    model: coder\\n"
        "    capabilities: [coding]\\n"
        "    api_key_env: MISSING_CLICODING_SECRET\\n",
        encoding="utf-8",
    )
    try:
        WorkerRegistry.from_yaml(str(path))
    except RegistryError as exc:
        assert "unset or empty" in str(exc)
    else:
        raise AssertionError("missing configured secret must fail closed")
