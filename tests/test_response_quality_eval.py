import importlib.util
import json
from pathlib import Path


CASES_PATH = (
    Path(__file__).parents[1]
    / "evals"
    / "response_quality"
    / "cases.jsonl"
)


def test_response_quality_benchmark_cases_are_valid_and_versionable():
    cases = [
        json.loads(line)
        for line in CASES_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert len(cases) >= 15
    identifiers = [case["id"] for case in cases]
    assert len(identifiers) == len(set(identifiers))
    assert any("fa-" in case["id"] for case in cases)
    assert any("en-" in case["id"] for case in cases)

    for case in cases:
        assert case["task_type"] in {"chat", "coding", "review", "vision"}
        assert case["messages"]
        assert case["messages"][-1]["role"] == "user"
        assert case["acceptance_criteria"]
        assert case["critical_dimensions"]


def test_quality_runner_documents_human_scoring_and_does_not_auto_judge():
    runner = (
        Path(__file__).parents[1]
        / "scripts"
        / "run_response_quality.py"
    ).read_text(encoding="utf-8")

    assert "never assigns an automatic quality" in runner
    assert "Score each response manually" in runner
    assert "CDS_AI_API_KEY" in runner
    assert "Authorization" in runner



def test_quality_runner_loads_the_versioned_case_file():
    runner_path = Path(__file__).parents[1] / "scripts" / "run_response_quality.py"
    spec = importlib.util.spec_from_file_location("response_quality_runner", runner_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    cases = module.load_cases(CASES_PATH)
    assert len(cases) == 15
    assert cases[0]["id"] == "fa-ordinary-001"
    assert cases[-1]["id"] == "en-uncertainty-015"



def _load_runner_module():
    runner_path = Path(__file__).parents[1] / "scripts" / "run_response_quality.py"
    spec = importlib.util.spec_from_file_location("response_quality_runner", runner_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_quality_runner_records_model_usage_tool_calls_and_orchestration(monkeypatch):
    module = _load_runner_module()

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps({
                "id": "chatcmpl-test",
                "model": "CDS_AI",
                "system_fingerprint": "fp-test",
                "choices": [{
                    "message": {
                        "content": "Answer",
                        "tool_calls": [{"id": "call-1"}],
                    },
                    "finish_reason": "tool_calls",
                }],
                "usage": {"prompt_tokens": 10, "completion_tokens": 4},
                "x_clicoding": {"orchestrated": True},
            }).encode("utf-8")

    monkeypatch.setattr(module, "urlopen", lambda *_args, **_kwargs: FakeResponse())
    result = module.request_case(
        "http://localhost/v1/chat/completions",
        "test-token",
        {
            "id": "case-1",
            "title": "test",
            "task_type": "chat",
            "messages": [{"role": "user", "content": "Hi"}],
            "acceptance_criteria": ["Returns an answer"],
        },
        10,
    )

    assert result["error"] is None
    assert result["response"] == "Answer"
    assert result["tool_calls"] == [{"id": "call-1"}]
    assert result["usage"]["prompt_tokens"] == 10
    assert result["orchestration"]["orchestrated"] is True
    assert result["response_id"] == "chatcmpl-test"


def test_quality_runner_rejects_empty_or_malformed_chat_completion(monkeypatch):
    module = _load_runner_module()

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"choices": []}'

    monkeypatch.setattr(module, "urlopen", lambda *_args, **_kwargs: FakeResponse())
    result = module.request_case(
        "http://localhost/v1/chat/completions",
        None,
        {
            "id": "case-invalid",
            "title": "invalid response",
            "task_type": "chat",
            "messages": [{"role": "user", "content": "Hi"}],
            "acceptance_criteria": ["Must return content"],
        },
        10,
    )

    assert result["response"] is None
    assert "no valid first choice" in result["error"]
