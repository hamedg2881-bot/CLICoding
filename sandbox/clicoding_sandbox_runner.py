#!/usr/bin/env python3
"""Root-owned, allowlisted multi-language CLICoding sandbox launcher."""
from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
import selectors
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

DOCKER = "/usr/bin/docker"
MAX_REQUEST_BYTES = 128 * 1024
MAX_WORKSPACE_REQUEST_BYTES = 8 * 1024 * 1024
MAX_WORKSPACE_FILES = 200
MAX_WORKSPACE_FILE_BYTES = 1 * 1024 * 1024
MAX_WORKSPACE_BYTES = 5 * 1024 * 1024
MAX_OUTPUT_BYTES = 64 * 1024
MAX_WORKSPACE_OUTPUT_BYTES = 256 * 1024
TIME_LIMIT_SECONDS = 60
WORKSPACE_TIME_LIMIT_SECONDS = 600
WORKSPACE_MEMORY_LIMIT = "2g"
WORKSPACE_CPU_LIMIT = "2"
WORKSPACE_PID_LIMIT = "128"
NUGET_FEED_PATH = "/var/cache/clicoding-sandbox/nuget-feed"
DOTNET_WORKSPACE_IMAGE = "mcr.microsoft.com/dotnet/sdk:10.0.402@sha256:747cbade5bfd918a175ac32f8f1d931edec2c7d0d5397a3de72eaef1ac1f66d2"
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
        "image": "golang:1.27.3-bookworm@sha256:dc0963f32053e4bc451d24d87e3892d42d01021b61f093e98eb95c0352bb84b0",
        "argv": ["sh", "-lc", "cat > /tmp/main.go && GOCACHE=/tmp/go-cache GOMODCACHE=/tmp/go-mod-cache GOPROXY=off GOTOOLCHAIN=local go run /tmp/main.go"],
    },
    "rust": {
        "image": "rust:1.99.1-bookworm@sha256:e6f2cff35d0f309acf8433b4f05878ed77092602c5065cc81801177da0bb3a9c",
        "argv": ["sh", "-lc", "cat > /tmp/main.rs && rustc /tmp/main.rs -o /tmp/main && /tmp/main"],
    },
    "java": {
        "image": "eclipse-temurin:25.0.5_8-jdk-ubi10-minimal@sha256:6d870f0c9a54ea090e1ed8396a4926f183b1d379934248b0d335567282417517",
        "argv": ["sh", "-lc", "cat > /tmp/Main.java && javac -d /tmp /tmp/Main.java && java -cp /tmp Main"],
    },
    "c": {
        "image": "gcc:16.2.0-trixie@sha256:b8a42d6804296fbe7a54e17e9d60c3100d9e8dbe7ef88c748cae2ddb00d63ba2",
        "argv": ["sh", "-lc", "cat > /tmp/main.c && gcc -std=c23 -O0 /tmp/main.c -o /tmp/main && /tmp/main"],
    },
    "cpp": {
        "image": "gcc:16.2.0-trixie@sha256:b8a42d6804296fbe7a54e17e9d60c3100d9e8dbe7ef88c748cae2ddb00d63ba2",
        "argv": ["sh", "-lc", "cat > /tmp/main.cpp && g++ -std=c++23 -O0 /tmp/main.cpp -o /tmp/main && /tmp/main"],
    },
    "csharp": {
        "image": "mcr.microsoft.com/dotnet/sdk:10.0.402@sha256:747cbade5bfd918a175ac32f8f1d931edec2c7d0d5397a3de72eaef1ac1f66d2",
        "argv": ["sh", "-lc", "mkdir -p /tmp/app && cat > /tmp/app/Program.cs && printf '%s\\n' '<Project Sdk=\"Microsoft.NET.Sdk\"><PropertyGroup><OutputType>Exe</OutputType><TargetFramework>net10.0</TargetFramework><ImplicitUsings>enable</ImplicitUsings><Nullable>enable</Nullable></PropertyGroup></Project>' > /tmp/app/app.csproj && printf '%s\\n' '<configuration><packageSources><clear /></packageSources></configuration>' > /tmp/app/NuGet.Config && dotnet restore /tmp/app/app.csproj --configfile /tmp/app/NuGet.Config --ignore-failed-sources && dotnet build /tmp/app/app.csproj --no-restore -o /tmp/app/out && dotnet /tmp/app/out/app.dll"],
    },
    "php": {
        "image": "php:8.5.12-cli-trixie@sha256:50956f094e90a716d43bea28087d447555ad80510e386ab8c9c10d8c70d781a9",
        "argv": ["php", "-d", "opcache.enable_cli=0", "-"],
    },
    "ruby": {
        "image": "ruby:4.0.9-slim-trixie@sha256:d8cb2c0714bdaba0f318af897a01d2dbde3cfa8e62d47e99cea55feb13c0e90d",
        "argv": ["ruby", "-"],
    },
}


class RequestError(Exception):
    pass


def emit(value: dict[str, Any], status: int = 0) -> int:
    sys.stdout.write(json.dumps(value, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return status


def read_request() -> dict[str, Any]:
    raw = sys.stdin.buffer.read(MAX_WORKSPACE_REQUEST_BYTES + 1)
    if len(raw) > MAX_WORKSPACE_REQUEST_BYTES:
        raise RequestError(f"Request exceeds {MAX_WORKSPACE_REQUEST_BYTES} bytes")
    try:
        request = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RequestError("Input must be one valid UTF-8 JSON object") from exc
    if not isinstance(request, dict):
        raise RequestError("Request must be a JSON object")
    return request


def parse_code_request(request: dict[str, Any]) -> tuple[str, bytes]:
    if set(request) != {"language", "code"}:
        raise RequestError("Single-file requests must contain only language and code")
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


def validate_workspace_request(request: dict[str, Any]) -> dict[str, Any]:
    if set(request) != {"mode", "action", "project_path", "files", "snapshot_sha256"}:
        raise RequestError("Workspace request has unexpected or missing fields")
    if request.get("mode") != "workspace" or request.get("action") not in {"build", "test"}:
        raise RequestError("Unsupported workspace sandbox action")
    project_path = request.get("project_path")
    if (
        not isinstance(project_path, str) or not project_path or len(project_path) > 512
        or "\\" in project_path or "\x00" in project_path
        or PurePosixPath(project_path).is_absolute()
        or PureWindowsPath(project_path).is_absolute()
        or PurePosixPath(project_path).suffix.lower() not in {".sln", ".slnx", ".csproj"}
    ):
        raise RequestError("Workspace project path is invalid")
    files = request.get("files")
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_WORKSPACE_FILES:
        raise RequestError("Workspace file count is outside the allowed range")
    seen: set[str] = set()
    manifest = []
    total_bytes = 0
    target_found = False
    blocked_dirs = {
        ".git", ".ssh", ".aws", ".azure", ".gnupg", ".venv", "venv",
        "node_modules", "bin", "obj", "__pycache__", ".idea", ".vs",
        "secrets", "credentials",
    }
    blocked_names = {
        ".env", ".git-credentials", "id_rsa", "id_ed25519",
        "credentials.json", "secrets.json", ".npmrc", ".pypirc", "nuget.config",
    }
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "content", "sha256", "bytes"}:
            raise RequestError("Workspace snapshot entry is invalid")
        path = item["path"]
        if (
            not isinstance(path, str) or not path or len(path) > 512
            or "\\" in path or "\x00" in path
            or PurePosixPath(path).is_absolute()
            or PureWindowsPath(path).is_absolute()
            or any(part in {"", ".", ".."} for part in PurePosixPath(path).parts)
        ):
            raise RequestError("Workspace snapshot contains an unsafe path")
        relative = PurePosixPath(path)
        parts = [part.casefold() for part in relative.parts]
        if any(part in blocked_dirs for part in parts[:-1]):
            raise RequestError("Workspace snapshot contains a blocked directory")
        filename = parts[-1]
        if (
            filename in blocked_names
            or (filename.startswith(".env.") and filename != ".env.example")
            or Path(filename).suffix.lower() in {".pem", ".key", ".p12", ".pfx", ".kdb"}
        ):
            raise RequestError("Workspace snapshot contains a blocked credential file")
        key = relative.as_posix().casefold()
        if key in seen:
            raise RequestError("Workspace snapshot contains duplicate paths")
        seen.add(key)
        source = item["content"]
        if not isinstance(source, str):
            raise RequestError("Workspace snapshot content must be UTF-8 text")
        try:
            encoded = source.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise RequestError("Workspace snapshot contains invalid UTF-8") from exc
        digest = hashlib.sha256(encoded).hexdigest()
        if len(encoded) > MAX_WORKSPACE_FILE_BYTES or item["bytes"] != len(encoded):
            raise RequestError("Workspace snapshot file size mismatch")
        if not isinstance(item["sha256"], str) or not hmac.compare_digest(digest, item["sha256"]):
            raise RequestError("Workspace snapshot file digest mismatch")
        total_bytes += len(encoded)
        if total_bytes > MAX_WORKSPACE_BYTES:
            raise RequestError("Workspace snapshot exceeds the size limit")
        manifest.append({"path": relative.as_posix(), "sha256": digest, "bytes": len(encoded)})
        if relative.as_posix() == project_path:
            target_found = True
    if not target_found:
        raise RequestError("Workspace project file is not included in the snapshot")
    manifest.sort(key=lambda item: item["path"])
    canonical = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    calculated = hashlib.sha256(canonical).hexdigest()
    supplied = request.get("snapshot_sha256")
    if not isinstance(supplied, str) or not hmac.compare_digest(calculated, supplied):
        raise RequestError("Workspace snapshot digest mismatch")
    return request


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



def run_bounded_container(
    command: list[str],
    cid_file: str,
    execution_id: str,
    timeout_seconds: int,
    output_limit: int,
) -> tuple[int | None, str, str, str]:
    started = time.monotonic()
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
        close_fds=True,
    )
    assert process.stdout is not None and process.stderr is not None
    selector = selectors.DefaultSelector()
    captured = {"stdout": bytearray(), "stderr": bytearray()}
    for stream, name in ((process.stdout, "stdout"), (process.stderr, "stderr")):
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ, name)
    timed_out = output_limited = False
    try:
        while selector.get_map():
            remaining = timeout_seconds - (time.monotonic() - started)
            if remaining <= 0:
                timed_out = True
                break
            for key, _ in selector.select(min(0.1, remaining)):
                chunk = os.read(key.fileobj.fileno(), 8192)
                if not chunk:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                total = len(captured["stdout"]) + len(captured["stderr"])
                available = output_limit - total
                if available > 0:
                    captured[key.data].extend(chunk[:available])
                if len(chunk) > available:
                    output_limited = True
                    break
            if output_limited:
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
            process.wait(timeout=max(0.1, timeout_seconds - (time.monotonic() - started)))
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            process.wait(timeout=3)
            cleanup_container(cid_file, execution_id)
    try:
        return (
            process.returncode,
            bytes(captured["stdout"]).decode("utf-8", errors="replace"),
            bytes(captured["stderr"]).decode("utf-8", errors="replace"),
            "timeout" if timed_out else "output_limit" if output_limited else "completed",
        )
    finally:
        try:
            os.unlink(cid_file)
        except FileNotFoundError:
            pass


def run_workspace(request: dict[str, Any]) -> dict[str, Any]:
    request = validate_workspace_request(request)
    feed = Path(NUGET_FEED_PATH)
    if not feed.is_dir() or feed.is_symlink():
        return {
            "status": "unavailable",
            "error": "offline NuGet feed is not staged at the root-owned configured path",
            "snapshot_sha256": request["snapshot_sha256"],
        }

    execution_id = uuid.uuid4().hex
    cid_file = f"/run/clicoding-sandbox/workspace-{execution_id}.cid"
    cid_fd = os.open(cid_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(cid_fd)
    os.unlink(cid_file)
    started = time.monotonic()

    with tempfile.TemporaryDirectory(
        prefix="workspace-snapshot-", dir="/run/clicoding-sandbox"
    ) as snapshot_dir:
        root = Path(snapshot_dir)
        for item in request["files"]:
            relative = PurePosixPath(item["path"])
            target = root.joinpath(*relative.parts)
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(target, flags, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(item["content"].encode("utf-8"))

        for directory, subdirs, filenames in os.walk(root, topdown=False):
            current = Path(directory)
            for filename in filenames:
                file_path = current / filename
                os.chown(file_path, 65532, 65532)
                os.chmod(file_path, 0o400)
            for dirname in subdirs:
                dir_path = current / dirname
                os.chown(dir_path, 65532, 65532)
                os.chmod(dir_path, 0o500)
        os.chown(root, 65532, 65532)
        os.chmod(root, 0o500)

        command = [
            DOCKER, "run", "--rm", "--pull=never",
            "--cidfile", cid_file,
            "--label", f"clicoding.execution={execution_id}",
            "--network=none", "--read-only", "--user", USER_ID,
            "--cap-drop=ALL", "--security-opt", "no-new-privileges:true",
            "--pids-limit", WORKSPACE_PID_LIMIT,
            "--memory", WORKSPACE_MEMORY_LIMIT, "--memory-swap", WORKSPACE_MEMORY_LIMIT,
            "--cpus", WORKSPACE_CPU_LIMIT, "--ulimit", "nofile=128:128",
            "--tmpfs", "/tmp:rw,exec,nosuid,nodev,size=1024m,uid=65532,gid=65532",
            "--mount", f"type=bind,src={snapshot_dir},dst=/input,readonly",
            "--mount", f"type=bind,src={NUGET_FEED_PATH},dst=/nuget-feed,readonly",
            "--workdir", "/tmp",
            "--env", "HOME=/tmp",
            "--env", "DOTNET_CLI_HOME=/tmp",
            "--env", "DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1",
            "--env", "DOTNET_CLI_TELEMETRY_OPTOUT=1",
            "--env", "NUGET_PACKAGES=/tmp/nuget",
            "--env", "DOTNET_GCHeapHardLimit=0x60000000",
            "--env", f"PROJECT_PATH={request['project_path']}",
            "--env", f"ACTION={request['action']}",
            DOTNET_WORKSPACE_IMAGE,
            "sh", "-lc",
            (
                "set -eu; mkdir -p /tmp/workspace /tmp/nuget; "
                "cp -R /input/. /tmp/workspace/; "
                "printf '%s\\n' '<configuration><packageSources><clear /><add key=\"offline\" value=\"/nuget-feed\" /></packageSources></configuration>' > /tmp/NuGet.Config; "
                "cd /tmp/workspace; "
                "dotnet restore \"$PROJECT_PATH\" --configfile /tmp/NuGet.Config --source /nuget-feed --packages /tmp/nuget --disable-parallel; "
                "dotnet \"$ACTION\" \"$PROJECT_PATH\" --no-restore --verbosity minimal"
            ),
        ]
        returncode, stdout, stderr, run_status = run_bounded_container(
            command,
            cid_file,
            execution_id,
            WORKSPACE_TIME_LIMIT_SECONDS,
            MAX_WORKSPACE_OUTPUT_BYTES,
        )

    status = (
        run_status if run_status != "completed"
        else "succeeded" if returncode == 0
        else "runtime_error" if returncode in (125, 126, 127)
        else "failed"
    )
    return {
        "status": status,
        "action": request["action"],
        "project_path": request["project_path"],
        "snapshot_sha256": request["snapshot_sha256"],
        "exit_code": returncode,
        "stdout": stdout,
        "stderr": stderr,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "limits": {
            "wall_seconds": WORKSPACE_TIME_LIMIT_SECONDS,
            "memory": WORKSPACE_MEMORY_LIMIT,
            "cpus": WORKSPACE_CPU_LIMIT,
            "pids": int(WORKSPACE_PID_LIMIT),
            "output_bytes": MAX_WORKSPACE_OUTPUT_BYTES,
            "network": "disabled",
            "workspace_mount": "read-only",
            "nuget_feed_mount": "read-only",
        },
    }


def main() -> int:
    if os.geteuid() != 0:
        return emit({"status": "rejected", "error": "Run this tool with sudo"}, 2)
    Path("/run/clicoding-sandbox").mkdir(mode=0o750, parents=True, exist_ok=True)
    if not Path(DOCKER).is_file():
        return emit({"status": "runner_error", "error": "Docker CLI not found"}, 2)
    try:
        request = read_request()
        with open("/run/lock/clicoding-sandbox.lock", "w", encoding="ascii") as lock_file:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return emit({"status": "busy", "error": "Another sandbox execution is active"}, 1)
            if request.get("mode") == "workspace":
                result = run_workspace(request)
            else:
                language, source = parse_code_request(request)
                result = run_container(language, source)
            return emit(result, 0 if result["status"] == "succeeded" else 1)
    except RequestError as exc:
        return emit({"status": "rejected", "error": str(exc)}, 2)
    except Exception as exc:
        return emit({"status": "runner_error", "error": f"{type(exc).__name__}: {exc}"}, 3)


if __name__ == "__main__":
    raise SystemExit(main())
