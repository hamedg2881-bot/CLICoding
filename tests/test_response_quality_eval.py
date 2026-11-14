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

    assert len(cases) >= 8
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
