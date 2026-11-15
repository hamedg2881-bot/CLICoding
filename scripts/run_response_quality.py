#!/usr/bin/env python3
"""Run the versioned CDS_AI response-quality cases against an OpenAI-compatible API.

The runner records raw answers and timing only. It never assigns an automatic quality
score: review the outputs against each case's acceptance_criteria and RESPONSE_QUALITY.md.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_CASES = (
    Path(__file__).resolve().parents[1]
    / "evals"
    / "response_quality"
    / "cases.jsonl"
)


def load_cases(path: Path) -> list[dict]:
    cases: list[dict] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        try:
            case = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
        required = {"id", "title", "task_type", "messages", "acceptance_criteria"}
        missing = required - case.keys()
        if missing:
            raise ValueError(f"{path}:{line_number}: missing fields: {sorted(missing)}")
        if case["task_type"] not in {"chat", "coding", "review", "vision"}:
            raise ValueError(f"{path}:{line_number}: unsupported task_type")
        if not isinstance(case["messages"], list) or not case["messages"]:
            raise ValueError(f"{path}:{line_number}: messages must be a non-empty list")
        if not isinstance(case["acceptance_criteria"], list) or not case["acceptance_criteria"]:
            raise ValueError(f"{path}:{line_number}: acceptance_criteria must be a non-empty list")
        cases.append(case)
    identifiers = [case["id"] for case in cases]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError(f"{path}: duplicate case IDs")
    return cases


def request_case(endpoint: str, api_key: str | None, case: dict, timeout: float) -> dict:
    payload = {
        "model": "CDS_AI",
        "messages": case["messages"],
        "task_type": case["task_type"],
        "stream": False,
    }
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    started = time.monotonic()
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            parsed = json.loads(body)
        elapsed_ms = round((time.monotonic() - started) * 1000)
        if not isinstance(parsed, dict):
            raise ValueError("API response must be a JSON object")
        choices = parsed.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError("API response contains no valid first choice")
        if not isinstance(choices[0], dict):
            raise ValueError("API response contains an invalid first choice")
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise ValueError("API response first choice has no valid message")
        response_text = message.get("content")
        tool_calls = message.get("tool_calls")
        if response_text is not None and not isinstance(response_text, str):
            raise ValueError("API response assistant content must be text or null")
        if tool_calls is not None and (
            not isinstance(tool_calls, list)
            or any(not isinstance(call, dict) for call in tool_calls)
        ):
            raise ValueError("API response tool_calls must be a list of objects")
        if not response_text and not tool_calls:
            raise ValueError("API response contains neither assistant content nor tool calls")
        return {
            "case_id": case["id"],
            "title": case["title"],
            "task_type": case["task_type"],
            "acceptance_criteria": case["acceptance_criteria"],
            "critical_dimensions": case.get("critical_dimensions", []),
            "elapsed_ms": elapsed_ms,
            "served_model": parsed.get("model"),
            "response_id": parsed.get("id"),
            "system_fingerprint": parsed.get("system_fingerprint"),
            "response": response_text,
            "tool_calls": tool_calls,
            "finish_reason": choices[0].get("finish_reason"),
            "usage": parsed.get("usage"),
            "orchestration": parsed.get("x_clicoding"),
            "error": None,
        }
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        elapsed_ms = round((time.monotonic() - started) * 1000)
        return {
            "case_id": case["id"],
            "title": case["title"],
            "task_type": case["task_type"],
            "acceptance_criteria": case["acceptance_criteria"],
            "critical_dimensions": case.get("critical_dimensions", []),
            "elapsed_ms": elapsed_ms,
            "response": None,
            "tool_calls": None,
            "usage": None,
            "orchestration": None,
            "error": f"HTTP {exc.code}: {detail[:2000]}",
        }
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        elapsed_ms = round((time.monotonic() - started) * 1000)
        return {
            "case_id": case["id"],
            "title": case["title"],
            "task_type": case["task_type"],
            "acceptance_criteria": case["acceptance_criteria"],
            "critical_dimensions": case.get("critical_dimensions", []),
            "elapsed_ms": elapsed_ms,
            "response": None,
            "error": f"{type(exc).__name__}: {exc}",
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=os.environ.get("CDS_AI_BASE_URL", "http://127.0.0.1:8100/v1"),
        help="OpenAI-compatible API base URL (default: CDS_AI_BASE_URL or localhost:8100/v1)",
    )
    parser.add_argument(
        "--cases",
        type=Path,
        default=DEFAULT_CASES,
        help="JSONL benchmark case file",
    )
    parser.add_argument(
        "--case-id",
        action="append",
        default=[],
        help="Run only this case ID; may be repeated",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Result JSONL path (default: response-quality-<UTC timestamp>.jsonl)",
    )
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args()

    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    try:
        cases = load_cases(args.cases)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.case_id:
        wanted = set(args.case_id)
        cases = [case for case in cases if case["id"] in wanted]
        missing = wanted - {case["id"] for case in cases}
        if missing:
            print(f"Unknown case IDs: {', '.join(sorted(missing))}", file=sys.stderr)
            return 2
    if not cases:
        print("No benchmark cases selected.", file=sys.stderr)
        return 2

    base_url = args.base_url.rstrip("/")
    endpoint = base_url if base_url.endswith("/chat/completions") else base_url + "/chat/completions"
    output = args.output or Path(
        "response-quality-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".jsonl"
    )
    api_key = os.environ.get("CDS_AI_API_KEY")
    failures = 0

    with output.open("w", encoding="utf-8") as stream:
        for case in cases:
            result = request_case(endpoint, api_key, case, args.timeout)
            result["recorded_at"] = datetime.now(timezone.utc).isoformat()
            result["endpoint"] = endpoint
            stream.write(json.dumps(result, ensure_ascii=False) + "\n")
            stream.flush()
            if result["error"]:
                failures += 1
                print(f"FAIL {case['id']}: {result['error']}", file=sys.stderr)
            else:
                print(f"OK   {case['id']}: {result['elapsed_ms']} ms")

    print(f"Recorded {len(cases)} cases to {output}")
    print("Score each response manually using docs/RESPONSE_QUALITY.md; no automatic judge was used.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
