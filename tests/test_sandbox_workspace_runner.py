import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

_runner_path = Path(__file__).parents[1] / "sandbox" / "clicoding_sandbox_runner.py"
_runner_spec = importlib.util.spec_from_file_location("clicoding_sandbox_runner", _runner_path)
runner = importlib.util.module_from_spec(_runner_spec)
_runner_spec.loader.exec_module(runner)


def workspace_request():
    files = [
        {
            "path": "Demo/Demo.csproj",
            "content": "<Project Sdk=\"Microsoft.NET.Sdk\" />",
        },
        {"path": "Demo/Program.cs", "content": "Console.WriteLine(42);"},
    ]
    manifest = [
        {
            "path": item["path"],
            "sha256": hashlib.sha256(item["content"].encode()).hexdigest(),
            "bytes": len(item["content"].encode()),
        }
        for item in files
    ]
    for item, metadata in zip(files, manifest, strict=True):
        item["sha256"] = metadata["sha256"]
        item["bytes"] = metadata["bytes"]
    canonical = json.dumps(
        sorted(manifest, key=lambda item: item["path"]),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return {
        "mode": "workspace",
        "approval_id": "d0a0f0d0-1111-4222-8333-444455556666",
        "action": "build",
        "project_path": "Demo/Demo.csproj",
        "files": files,
        "snapshot_sha256": hashlib.sha256(canonical).hexdigest(),
    }


def test_runner_validates_workspace_snapshot_and_accepts_fixed_action():
    request = workspace_request()
    validated = runner.validate_workspace_request(request)
    assert validated["action"] == "build"
    assert validated["project_path"] == "Demo/Demo.csproj"


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda value: value["files"][0].update(path="../escape.csproj"), "unsafe path"),
        (lambda value: value["files"][0].update(path="Demo/.env"), "credential file"),
        (lambda value: value.update(action="shell"), "Unsupported workspace"),
        (lambda value: value.update(snapshot_sha256="0" * 64), "digest mismatch"),
        (lambda value: value.update(extra="unexpected"), "unexpected or missing"),
    ],
)
def test_runner_rejects_invalid_workspace_requests(mutate, message):
    request = workspace_request()
    mutate(request)
    with pytest.raises(runner.RequestError, match=message):
        runner.validate_workspace_request(request)


def test_runner_preserves_existing_single_file_request_contract():
    language, source = runner.parse_code_request(
        {"language": "python", "code": "print(2 + 3)"}
    )
    assert language == "python"
    assert source == b"print(2 + 3)"

    with pytest.raises(runner.RequestError, match="only language and code"):
        runner.parse_code_request(
            {"language": "python", "code": "print(1)", "mode": "workspace"}
        )
