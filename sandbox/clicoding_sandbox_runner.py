#!/usr/bin/env python3
"""Root-owned, allowlisted multi-language CLICoding sandbox launcher."""
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
TIME_LIMIT_SECONDS = 30
MEMORY_LIMIT = "512m"
CPU_LIMIT = "1"
PID_LIMIT = "32"
USER_ID = "65532:65532"

# Commands are fixed by this table. User input is supplied only on stdin and is
# never interpolated into a shell command, image name, flag, or filesystem path.
RUNTIMES = {
    "python": {
        "image": "python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df",
        "argv": ["python", "-I", "-B", "-"],
    },
    "javascript": {
        "image": "node:24-bookworm-slim@sha256:01d566e8d94d8c619591d78652babfccd3c6992a3dffa9ed5ab39e11cf6c70e9",
        "argv": ["node", "--input-type=commonjs", "-"],
    },
    "typescript": {
        "image": "clicoding-typescript:7.0.2",
        "argv": ["sh", "-lc", "cat > /tmp/main.ts && tsc --target ES2022 --module commonjs --strict --skipLibCheck --outDir /tmp/out /tmp/main.ts && node /tmp/out/main.js"],
    },
    "go": {
        "image": "golang:1.27.3-bookworm",
        "argv": ["sh", "-lc", "cat > /tmp/main.go && GOCACHE=/tmp/go-cache GOMODCACHE=/tmp/go-mod-cache GOPROXY=off GOTOOLCHAIN=local go run /tmp/main.go"],
    },
    "rust": {
        "image": "rust:1.99.1-bookworm",
        "argv": ["sh", "-lc", "cat > /tmp/main.rs && rustc /tmp/main.rs -o /tmp/main && /tmp/main"],
    },
    "java": {
        "image": "eclipse-temurin:25.0.5_8-jdk-ubi10-minimal",
        "argv": ["sh", "-lc", "cat > /tmp/Main.java && javac -d /tmp /tmp/Main.java && java -cp /tmp Main"],
    },
    "c": {
        "image": "gcc:16.2.0-trixie",
        "argv": ["sh", "-lc", "cat > /tmp/main.c && gcc -std=c23 -O0 /tmp/main.c -o /tmp/main && /tmp/main"],
    },
    "cpp": {
        "image": "gcc:16.2.0-trixie",
        "argv": ["sh", "-lc", "cat > /tmp/main.cpp && g++ -std=c++23 -O0 /tmp/main.cpp -o /tmp/main && /tmp/main"],
    },
    "csharp": {
        "image": "mcr.microsoft.com/dotnet/sdk:10.0.402",
        "argv": ["sh", "-lc", "mkdir -p /tmp/app && cat > /tmp/app/Program.cs && printf '%s\\n' '<Project Sdk=\"Microsoft.NET.Sdk\"><PropertyGroup><OutputType>Exe</OutputType><TargetFramework>net10.0</TargetFramework><ImplicitUsings>enable</ImplicitUsings><Nullable>enable</Nullable></PropertyGroup></Project>' > /tmp/app/app.csproj && printf '%s\\n' '<configuration><packageSources><clear /></packageSources></configuration>' > /tmp/app/NuGet.Config && dotnet restore /tmp/app/app.csproj --configfile /tmp/app/NuGet.Config --ignore-failed-sources && dotnet build /tmp/app/app.csproj --no-restore -o /tmp/app/out && dotnet /tmp/app/out/app.dll"],
    },
    "php": {
        "image": "php:8.5.12-cli-trixie",
        "argv": ["php", "-d", "opcache.enable_cli=0", "-"],
    },
    "ruby": {
        "image": "ruby:4.0.9-slim-trixie",
        "argv": ["ruby", "-"],
    },
}


class RequestError(Exception):
    pass


def emit(value: dict[str, Any], status: int = 0) -> int:
    sys.stdout.write(json.dumps(value, ensure_ascii=False) + "\\n")
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
        raise RequestError("Unsupported language; allowed: " + ", ".join(RUNTIMES))
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
            [DOCKER, "ps", "-aq", "--filter", f"label=clicoding.execution={execution_id}"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, timeout=4, check=False,
        )
        if listed.returncode == 0:
            container_ids.update(x.strip() for x in listed.stdout.splitlines() if x.strip())
    except (OSError, subprocess.TimeoutExpired):
        pass
    for container_id in container_ids:
        try:
            subprocess.run([DOCKER, "rm", "--force", container_id], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=4, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pass


def run_container(language: str, source: bytes) -> dict[str, Any]:
    runtime = RUNTIMES[language]
    execution_id = uuid.uuid4().hex
    cid_fd, cid_file = tempfile.mkstemp(prefix="runner-", dir="/run/clicoding-sandbox")
    os.close(cid_fd)
    os.unlink(cid_file)
    command = [
        DOCKER, "run", "--rm", "-i", "--pull=never", "--cidfile", cid_file,
        "--label", f"clicoding.execution={execution_id}", "--network=none",
        "--read-only", "--user", USER_ID, "--cap-drop=ALL",
        "--security-opt", "no-new-privileges:true", "--pids-limit", PID_LIMIT,
        "--memory", MEMORY_LIMIT, "--memory-swap", MEMORY_LIMIT, "--cpus", CPU_LIMIT,
        "--ulimit", "nofile=64:64",
        "--tmpfs", "/tmp:rw,exec,nosuid,nodev,size=128m,uid=65532,gid=65532",
        "--workdir", "/tmp", "--env", "HOME=/tmp", "--env", "TMPDIR=/tmp",
        "--env", "DOTNET_CLI_HOME=/tmp", "--env", "DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1",
        "--env", "DOTNET_CLI_TELEMETRY_OPTOUT=1", "--env", "NUGET_PACKAGES=/tmp/nuget",
        "--env", "DOTNET_GCHeapHardLimit=0x10000000",
        "--env", "JAVA_TOOL_OPTIONS=-XX:MaxRAMPercentage=50 -XX:InitialRAMPercentage=10",
        runtime["image"], *runtime["argv"],
    ]
    started = time.monotonic()
    deadline = started + TIME_LIMIT_SECONDS
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, bufsize=0, close_fds=True)
    assert process.stdin is not None and process.stdout is not None and process.stderr is not None
    selector = selectors.DefaultSelector()
    for stream, event, name in (
        (process.stdin, selectors.EVENT_WRITE, "stdin"),
        (process.stdout, selectors.EVENT_READ, "stdout"),
        (process.stderr, selectors.EVENT_READ, "stderr"),
    ):
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, event, name)
    offset = 0
    captured = {"stdout": bytearray(), "stderr": bytearray()}
    timed_out = output_limited = input_closed = False
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            for key, _ in selector.select(min(0.1, remaining)):
                stream = key.fileobj
                if key.data == "stdin":
                    try:
                        written = os.write(stream.fileno(), source[offset:offset + 8192])
                        offset += written
                    except BrokenPipeError:
                        offset = len(source)
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
                        captured[key.data].extend(chunk[:available])
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
        exit_code = process.returncode
        status = ("timeout" if timed_out else "output_limit" if output_limited else
                  "succeeded" if exit_code == 0 else
                  "runtime_error" if exit_code in (125, 126, 127) else "failed")
        return {
            "status": status, "language": language, "exit_code": exit_code,
            "stdout": bytes(captured["stdout"]).decode("utf-8", errors="replace"),
            "stderr": bytes(captured["stderr"]).decode("utf-8", errors="replace"),
            "duration_ms": int((time.monotonic() - started) * 1000),
            "limits": {"wall_seconds": TIME_LIMIT_SECONDS, "memory": MEMORY_LIMIT,
                       "cpus": CPU_LIMIT, "pids": int(PID_LIMIT),
                       "output_bytes": MAX_OUTPUT_BYTES},
        }
    finally:
        try:
            os.unlink(cid_file)
        except FileNotFoundError:
            pass


def main() -> int:
    if os.geteuid() != 0:
        return emit({"status": "rejected", "error": "Run this tool with sudo"}, 2)
    Path("/run/clicoding-sandbox").mkdir(mode=0o750, parents=True, exist_ok=True)
    if not Path(DOCKER).is_file():
        return emit({"status": "runner_error", "error": "Docker CLI not found"}, 2)
    try:
        language, source = read_request()
        with open("/run/lock/clicoding-sandbox.lock", "w", encoding="ascii") as lock_file:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return emit({"status": "busy", "error": "Another sandbox execution is active"}, 1)
            result = run_container(language, source)
            return emit(result, 0 if result["status"] == "succeeded" else 1)
    except RequestError as exc:
        return emit({"status": "rejected", "error": str(exc)}, 2)
    except Exception as exc:
        return emit({"status": "runner_error", "error": f"{type(exc).__name__}: {exc}"}, 3)


if __name__ == "__main__":
    raise SystemExit(main())
