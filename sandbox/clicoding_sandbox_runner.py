#!/usr/bin/env python3
"""Narrow, root-owned CLICoding sandbox launcher.

This is a manually invoked first slice. It accepts only Python and Node.js source,
uses pinned local images, and never accepts Docker arguments, mounts, or commands
from the caller. It requires root so the Docker socket is not exposed to the
Orchestrator service account.
"""
from __future__ import annotations

import fcntl
import json
import os
import selectors
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

DOCKER = "/usr/bin/docker"
MAX_REQUEST_BYTES = 128 * 1024
MAX_OUTPUT_BYTES = 64 * 1024
TIME_LIMIT_SECONDS = 10
MEMORY_LIMIT = "256m"
CPU_LIMIT = "1"
PID_LIMIT = "32"
USER_ID = "65532:65532"

RUNTIMES = {
    "python": {
        "image": "python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df",
        "argv": ["python", "-I", "-B", "-"],
    },
    "javascript": {
        "image": "node:24-bookworm-slim@sha256:01d566e8d94d8c619591d78652babfccd3c6992a3dffa9ed5ab39e11cf6c70e9",
        "argv": ["node", "--input-type=commonjs", "-"],
    },
}


class RequestError(Exception):
    pass


def emit(value: dict[str, Any], status: int = 0) -> int:
    sys.stdout.write(json.dumps(value, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return status


def read_request() -> tuple[str, bytes]:
    raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    if len(raw) > MAX_REQUEST_BYTES:
        raise RequestError(f"Request exceeds {MAX_REQUEST_BYTES} bytes")
    try:
        request = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RequestError("Input must be one valid UTF-8 JSON object") from exc
    if not isinstance(request, dict):
        raise RequestError("Request must be a JSON object")
    language = request.get("language")
    code = request.get("code")
    if not isinstance(language, str) or language not in RUNTIMES:
        raise RequestError("Unsupported language; allowed: python, javascript")
    if not isinstance(code, str) or not code.strip():
        raise RequestError("code must be a non-empty string")
    try:
        encoded = code.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise RequestError("code must contain valid UTF-8 text") from exc
    if len(encoded) > MAX_REQUEST_BYTES:
        raise RequestError(f"Source exceeds {MAX_REQUEST_BYTES} bytes")
    return language, encoded


def cleanup_container(cid_file: str, execution_id: str) -> None:
    """Best-effort cleanup by cidfile and a unique Docker label.

    The label lookup covers the race where the daemon creates a container
    but the killed Docker CLI has not written its cidfile yet.
    """
    deadline = time.monotonic() + 2
    while not Path(cid_file).exists() and time.monotonic() < deadline:
        time.sleep(0.05)

    container_ids: set[str] = set()
    try:
        container_id = Path(cid_file).read_text(encoding="ascii").strip()
        if container_id:
            container_ids.add(container_id)
    except OSError:
        pass

    try:
        listed = subprocess.run(
            [
                DOCKER, "ps", "-aq",
                "--filter", f"label=clicoding.execution={execution_id}",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=4,
            check=False,
        )
        if listed.returncode == 0:
            container_ids.update(
                line.strip() for line in listed.stdout.splitlines() if line.strip()
            )
    except (OSError, subprocess.TimeoutExpired):
        pass

    for container_id in container_ids:
        try:
            subprocess.run(
                [DOCKER, "rm", "--force", container_id],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=4,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass


def run_container(language: str, source: bytes) -> dict[str, Any]:
    runtime = RUNTIMES[language]
    execution_id = uuid.uuid4().hex
    cid_fd, cid_file = tempfile.mkstemp(prefix="runner-", dir="/run/clicoding-sandbox")
    os.close(cid_fd)
    os.unlink(cid_file)

    command = [
        DOCKER, "run", "--rm", "-i", "--pull=never",
        "--cidfile", cid_file,
        "--label", f"clicoding.execution={execution_id}",
        "--network=none",
        "--read-only",
        "--user", USER_ID,
        "--cap-drop=ALL",
        "--security-opt", "no-new-privileges:true",
        "--pids-limit", PID_LIMIT,
        "--memory", MEMORY_LIMIT,
        "--memory-swap", MEMORY_LIMIT,
        "--cpus", CPU_LIMIT,
        "--ulimit", "nofile=64:64",
        "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=16m,uid=65532,gid=65532",
        "--workdir", "/tmp",
        "--env", "HOME=/tmp",
        runtime["image"],
        *runtime["argv"],
    ]

    started = time.monotonic()
    deadline = started + TIME_LIMIT_SECONDS
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
        close_fds=True,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None

    selector = selectors.DefaultSelector()
    os.set_blocking(process.stdin.fileno(), False)
    os.set_blocking(process.stdout.fileno(), False)
    os.set_blocking(process.stderr.fileno(), False)
    selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")

    offset = 0
    captured = {"stdout": bytearray(), "stderr": bytearray()}
    timed_out = False
    output_limited = False
    input_closed = False

    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            events = selector.select(min(0.1, remaining))
            for key, _ in events:
                stream_name = key.data
                stream = key.fileobj
                if stream_name == "stdin":
                    try:
                        written = os.write(stream.fileno(), source[offset:offset + 8192])
                        offset += written
                    except BrokenPipeError:
                        offset = len(source)
                        selector.unregister(stream)
                        stream.close()
                        input_closed = True
                        continue
                    if offset >= len(source):
                        selector.unregister(stream)
                        stream.close()
                        input_closed = True
                else:
                    chunk = os.read(stream.fileno(), 8192)
                    if not chunk:
                        selector.unregister(stream)
                        stream.close()
                        continue
                    total = len(captured["stdout"]) + len(captured["stderr"])
                    available = MAX_OUTPUT_BYTES - total
                    if available > 0:
                        captured[stream_name].extend(chunk[:available])
                    if len(chunk) > available:
                        output_limited = True
                        break
            if output_limited:
                break
            if process.poll() is not None and input_closed and not selector.get_map():
                break
    finally:
        selector.close()

    if timed_out or output_limited:
        process.kill()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
        cleanup_container(cid_file, execution_id)
    else:
        try:
            process.wait(timeout=max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            process.wait(timeout=3)
            cleanup_container(cid_file, execution_id)

    try:
        duration_ms = int((time.monotonic() - started) * 1000)
        stdout = bytes(captured["stdout"]).decode("utf-8", errors="replace")
        stderr = bytes(captured["stderr"]).decode("utf-8", errors="replace")
        exit_code = process.returncode
        if timed_out:
            status = "timeout"
        elif output_limited:
            status = "output_limit"
        elif exit_code == 0:
            status = "succeeded"
        elif exit_code in (125, 126, 127):
            status = "runtime_error"
        else:
            status = "failed"
        return {
            "status": status,
            "language": language,
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "duration_ms": duration_ms,
            "limits": {
                "wall_seconds": TIME_LIMIT_SECONDS,
                "memory": MEMORY_LIMIT,
                "cpus": CPU_LIMIT,
                "pids": int(PID_LIMIT),
                "output_bytes": MAX_OUTPUT_BYTES,
            },
        }
    finally:
        try:
            os.unlink(cid_file)
        except FileNotFoundError:
            pass


def main() -> int:
    if os.geteuid() != 0:
        return emit({"status": "rejected", "error": "Run this tool with sudo"}, 2)
    if not Path(DOCKER).is_file():
        return emit({"status": "runner_error", "error": "Docker CLI not found"}, 2)
    try:
        language, source = read_request()
        lock_path = "/run/lock/clicoding-sandbox.lock"
        with open(lock_path, "w", encoding="ascii") as lock_file:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return emit({"status": "busy", "error": "Another sandbox execution is active"}, 1)
            result = run_container(language, source)
            return emit(result, 0 if result["status"] == "succeeded" else 1)
    except RequestError as exc:
        return emit({"status": "rejected", "error": str(exc)}, 2)
    except Exception as exc:
        return emit(
            {"status": "runner_error", "error": f"{type(exc).__name__}: {exc}"},
            3,
        )


if __name__ == "__main__":
    raise SystemExit(main())
