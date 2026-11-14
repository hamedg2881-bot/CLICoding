import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "score_response_quality.py"


def _module():
    spec = importlib.util.spec_from_file_location("score_response_quality", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _result(case_id="case-1"):
    return {
        "case_id": case_id,
        "acceptance_criteria": ["criterion one", "criterion two"],
        "critical_dimensions": ["reasoning_correctness", "language_format"],
        "error": None,
    }


def _score(case_id="case-1", **overrides):
    scores = {
        "intent_relevance": 4,
        "factual_reliability": 4,
        "completeness": 4,
        "context_continuity": 4,
        "reasoning_correctness": 4,
        "output_quality": 4,
        "tool_action_honesty": 4,
    }
    scores.update(overrides)
    return {
        "case_id": case_id,
        "reviewer": "human-reviewer",
        "scores": scores,
        "criteria": [True, True],
    }


def test_human_scorecard_passes_only_when_all_gates_pass():
    summary = _module().evaluate([_result()], [_score()])
    assert summary["passed"] is True
    assert summary["passed_count"] == 1
    assert summary["dimension_means"]["output_quality"] == 4


def test_critical_dimension_below_three_fails_even_with_high_average():
    summary = _module().evaluate(
        [_result()],
        [_score(reasoning_correctness=2)],
    )
    assert summary["passed"] is False
    assert summary["cases"][0]["failed_dimensions"] == ["reasoning_correctness"]


def test_universal_reliability_gate_cannot_be_hidden_by_average():
    summary = _module().evaluate(
        [_result()],
        [_score(tool_action_honesty=2)],
    )
    assert summary["passed"] is False
    assert "tool_action_honesty" in summary["cases"][0]["failed_dimensions"]


def test_failed_acceptance_criterion_fails_case():
    score = _score()
    score["criteria"] = [True, False]
    summary = _module().evaluate([_result()], [score])
    assert summary["passed"] is False
    assert summary["cases"][0]["failed_criteria_indices"] == [2]


def test_missing_or_extra_scorecards_are_rejected():
    with pytest.raises(ValueError, match="IDs differ"):
        _module().evaluate([_result()], [])


def test_execution_error_cannot_be_marked_as_quality_pass():
    result = _result()
    result["error"] = "HTTP 503"
    summary = _module().evaluate([result], [_score()])
    assert summary["passed"] is False
    assert summary["execution_error_count"] == 1


def test_invalid_score_range_is_rejected():
    with pytest.raises(ValueError, match="0 to 4"):
        _module().evaluate([_result()], [_score(output_quality=5)])
