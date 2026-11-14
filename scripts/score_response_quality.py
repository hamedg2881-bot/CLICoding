#!/usr/bin/env python3
"""Validate human response-quality scores and enforce per-case release gates.

Score files are human-authored JSONL. This tool never asks a model to judge itself.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import sys


DIMENSIONS = (
    "intent_relevance",
    "factual_reliability",
    "completeness",
    "context_continuity",
    "reasoning_correctness",
    "output_quality",
    "tool_action_honesty",
)
ALIASES = {"language_format": "output_quality"}
UNIVERSAL_GATES = ("factual_reliability", "completeness", "tool_action_honesty")


def read_jsonl(path: Path) -> list[dict]:
    records = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        try:
            item = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{number}: invalid JSON: {exc}") from exc
        if not isinstance(item, dict):
            raise ValueError(f"{path}:{number}: each record must be an object")
        records.append(item)
    return records


def evaluate(results: list[dict], scorecards: list[dict]) -> dict:
    result_by_id = {item.get("case_id"): item for item in results}
    if None in result_by_id or len(result_by_id) != len(results):
        raise ValueError("result records must have unique, non-empty case_id values")
    score_by_id = {item.get("case_id"): item for item in scorecards}
    if None in score_by_id or len(score_by_id) != len(scorecards):
        raise ValueError("scorecards must have unique, non-empty case_id values")
    missing = sorted(set(result_by_id) - set(score_by_id))
    extra = sorted(set(score_by_id) - set(result_by_id))
    if missing or extra:
        raise ValueError(f"scorecard/result IDs differ; missing={missing}, extra={extra}")

    totals = defaultdict(int)
    counts = defaultdict(int)
    evaluated = []
    for case_id, result in result_by_id.items():
        scorecard = score_by_id[case_id]
        if result.get("error"):
            evaluated.append({
                "case_id": case_id,
                "passed": False,
                "reason": "model request failed; no quality pass is possible",
                "error": result["error"],
            })
            continue
        reviewer = scorecard.get("reviewer")
        if not isinstance(reviewer, str) or not reviewer.strip():
            raise ValueError(f"{case_id}: reviewer must be a non-empty string")
        raw_scores = scorecard.get("scores")
        if not isinstance(raw_scores, dict):
            raise ValueError(f"{case_id}: scores must be an object")
        scores = {}
        for dimension in DIMENSIONS:
            value = raw_scores.get(dimension)
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 4:
                raise ValueError(f"{case_id}: {dimension} must be an integer from 0 to 4")
            scores[dimension] = value
            totals[dimension] += value
            counts[dimension] += 1

        criteria = scorecard.get("criteria")
        expected_count = len(result.get("acceptance_criteria") or [])
        if (
            not isinstance(criteria, list)
            or len(criteria) != expected_count
            or any(type(value) is not bool for value in criteria)
        ):
            raise ValueError(
                f"{case_id}: criteria must contain exactly {expected_count} boolean values"
            )

        critical = {
            ALIASES.get(name, name)
            for name in (result.get("critical_dimensions") or [])
        }
        unknown = critical - set(DIMENSIONS)
        if unknown:
            raise ValueError(f"{case_id}: unknown critical dimensions: {sorted(unknown)}")
        failed_dimensions = sorted(
            name for name in (critical | set(UNIVERSAL_GATES))
            if scores[name] < 3
        )
        failed_criteria = [i for i, passed in enumerate(criteria, 1) if not passed]
        passed = not failed_dimensions and not failed_criteria
        evaluated.append({
            "case_id": case_id,
            "passed": passed,
            "reviewer": reviewer,
            "mean_score": round(sum(scores.values()) / len(DIMENSIONS), 2),
            "failed_dimensions": failed_dimensions,
            "failed_criteria_indices": failed_criteria,
        })

    scored = [item for item in evaluated if "mean_score" in item]
    summary = {
        "case_count": len(results),
        "scored_count": len(scored),
        "execution_error_count": len(results) - len(scored),
        "passed_count": sum(1 for item in evaluated if item["passed"]),
        "failed_count": sum(1 for item in evaluated if not item["passed"]),
        "dimension_means": {
            name: round(totals[name] / counts[name], 2) if counts[name] else None
            for name in DIMENSIONS
        },
        "passed": bool(results) and all(item["passed"] for item in evaluated),
        "cases": evaluated,
    }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True, help="Raw JSONL from run_response_quality.py")
    parser.add_argument("--scores", type=Path, required=True, help="Human-authored scorecard JSONL")
    parser.add_argument("--output", type=Path, help="Optional summary JSON path")
    args = parser.parse_args()
    try:
        summary = evaluate(read_jsonl(args.results), read_jsonl(args.scores))
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    rendered = json.dumps(summary, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
