from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
import errno
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import secrets
import shutil
import signal
import sqlite3
import stat
import subprocess
import tempfile
import threading
import time
from typing import Any, Callable

from clicoding_orchestrator.context import redact_sensitive_data
from clicoding_orchestrator.tool_protocol import (
    ToolProtocolError,
    validate_tool_calls,
    validate_tool_definitions,
)


class ToolExecutionConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class ToolApproval:
    tool_name: str
    workspace_id: str
    summary: str
    details: dict[str, Any]


class ToolExecutionStore:
    """Durable idempotency ledger; a crash leaves a non-repeatable running record."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path == ":memory:":
            raise ValueError("tool idempotency store must use persistent local storage")
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tool_invocations (
                    idempotency_key TEXT PRIMARY KEY,
                    request_sha256 TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
                    result_json TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def begin(self, key: str, request_sha256: str) -> dict[str, Any] | None:
        now = time.time()
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT request_sha256, status, result_json FROM tool_invocations "
                "WHERE idempotency_key = ?",
                (key,),
            ).fetchone()
            if row is not None:
                if row["request_sha256"] != request_sha256:
                    raise ToolExecutionConflict(
                        "idempotency key was reused with different tool arguments"
                    )
                if row["status"] == "succeeded" and row["result_json"]:
                    return json.loads(row["result_json"])
                raise ToolExecutionConflict(
                    f"tool invocation is already {row['status']}; refusing duplicate execution"
                )
            connection.execute(
                """
                INSERT INTO tool_invocations
                    (idempotency_key, request_sha256, status, created_at, updated_at)
                VALUES (?, ?, 'running', ?, ?)
                """,
                (key, request_sha256, now, now),
            )
        return None

    def finish(self, key: str, result: dict[str, Any], status: str = "succeeded") -> None:
        if status not in {"succeeded", "failed"}:
            raise ValueError("invalid terminal tool invocation status")
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                """
                UPDATE tool_invocations
                SET status = ?, result_json = ?, updated_at = ?
                WHERE idempotency_key = ? AND status = 'running'
                """,
                (
                    status,
                    json.dumps(result, ensure_ascii=False, separators=(",", ":")),
                    time.time(),
                    key,
                ),
            )
            if cursor.rowcount != 1:
                raise ToolExecutionConflict("tool invocation state changed before completion")


class WorkspaceToolHost:
    """Allow-listed local tools bound to exactly one developer workspace."""

    IGNORED_DIRECTORIES = {
        ".git", ".venv", "venv", "node_modules", "bin", "obj",
        "__pycache__", ".idea", ".vs", ".ssh", ".aws", ".azure",
        ".gnupg", "secrets", "credentials",
    }
    BLOCKED_FILENAMES = {
        ".env", ".git-credentials", "id_rsa", "id_ed25519",
        "credentials.json", "secrets.json",
    }
    BLOCKED_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".kdb"}

    def __init__(
        self,
        workspace_root: str | Path,
        workspace_id: str,
        store: ToolExecutionStore,
        approval: Callable[[ToolApproval], bool] | None = None,
        sandbox_executor: Callable[..., dict[str, Any]] | None = None,
        max_file_bytes: int = 1_000_000,
        max_output_chars: int = 64_000,
        command_timeout_seconds: int = 300,
    ):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", workspace_id):
            raise ValueError("workspace_id has an invalid format")
        root = Path(workspace_root).resolve(strict=True)
        if not root.is_dir():
            raise ValueError("workspace_root must be a directory")
        if max_file_bytes < 1 or max_file_bytes > 10_000_000:
            raise ValueError("max_file_bytes must be between 1 and 10000000")
        if max_output_chars < 1024 or max_output_chars > 256_000:
            raise ValueError("max_output_chars must be between 1024 and 256000")
        if command_timeout_seconds < 1 or command_timeout_seconds > 1800:
            raise ValueError("command_timeout_seconds must be between 1 and 1800")
        self.root = root
        self.workspace_id = workspace_id
        self.store = store
        store_path = Path(store.path).resolve()
        if store_path == root or root in store_path.parents:
            raise ValueError("tool idempotency store must be outside the active workspace")
        self.approval = approval
        self.sandbox_executor = sandbox_executor
        self.max_file_bytes = max_file_bytes
        self.max_output_chars = max_output_chars
        self.command_timeout_seconds = command_timeout_seconds
        self._definitions = self._build_definitions()
        self._definitions_by_name = validate_tool_definitions(self._definitions)

    @property
    def definitions(self) -> list[dict[str, Any]]:
        return self._definitions

    def execute_tool_call(
        self,
        call: dict[str, Any],
        conversation_id: str | None = None,
        cancellation_event: threading.Event | None = None,
    ) -> dict[str, str]:
        normalized = validate_tool_calls([call], self._definitions_by_name)
        call = normalized[0]
        call_id = call["id"]
        name = call["function"]["name"]
        arguments = json.loads(call["function"]["arguments"])
        request_sha = hashlib.sha256(
            json.dumps(
                {"name": name, "arguments": arguments},
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if conversation_id is None:
            # Preserve the standalone host API; the CDS_AI client always supplies
            # the durable conversation ID so call IDs cannot collide across chats.
            scope = "local"
        elif (
            not isinstance(conversation_id, str)
            or not conversation_id
            or len(conversation_id) > 256
            or any(ord(character) < 32 for character in conversation_id)
        ):
            raise ValueError("conversation_id must be a bounded non-empty identifier")
        else:
            scope = hashlib.sha256(conversation_id.encode("utf-8")).hexdigest()
        key = f"{self.workspace_id}:{scope}:{call_id}"

        if cancellation_event is not None and cancellation_event.is_set():
            return self._tool_message(call_id, name, {"status": "cancelled"})
        try:
            cached = self.store.begin(key, request_sha)
        except ToolExecutionConflict as exc:
            result = {"status": "conflict", "error": str(exc)}
            return self._tool_message(call_id, name, result)
        if cached is not None:
            return self._tool_message(call_id, name, cached)

        try:
            if cancellation_event is not None and cancellation_event.is_set():
                result = {"status": "cancelled"}
            else:
                result = redact_sensitive_data(
                    self._dispatch(name, arguments, cancellation_event)
                )
            self.store.finish(key, result)
        except Exception as exc:
            result = redact_sensitive_data({
                "status": "error",
                "error": f"{type(exc).__name__}: {str(exc)[:1000]}",
            })
            try:
                self.store.finish(key, result, status="failed")
            except ToolExecutionConflict:
                pass
        return self._tool_message(call_id, name, result)

    @staticmethod
    def _tool_message(call_id: str, name: str, result: dict[str, Any]) -> dict[str, str]:
        return {
            "role": "tool",
            "tool_call_id": call_id,
            "name": name,
            "content": json.dumps(result, ensure_ascii=False, separators=(",", ":")),
        }

    def _build_definitions(self) -> list[dict[str, Any]]:
        def function(name: str, description: str, properties: dict[str, Any], required: list[str]):
            return {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                        "additionalProperties": False,
                    },
                },
            }

        string = {"type": "string"}
        return [
            function(
                "workspace_list_files",
                "List bounded, non-secret files inside the active workspace.",
                {"directory": string},
                [],
            ),
            function(
                "workspace_read_file",
                "Read a UTF-8 text file inside the active workspace; secret files are blocked.",
                {"path": string},
                ["path"],
            ),
            function(
                "workspace_search_text",
                "Search for a literal text string in bounded workspace files.",
                {"query": string, "directory": string},
                ["query"],
            ),
            function(
                "workspace_write_file",
                "Write a file only after user approval and an expected SHA-256 check for overwrites.",
                {
                    "path": string,
                    "content": {"type": "string", "maxLength": 300000},
                    "expected_sha256": {"type": "string", "maxLength": 64},
                },
                ["path", "content"],
            ),
            function(
                "workspace_git_status",
                "Read Git status for the active workspace using a fixed read-only command.",
                {},
                [],
            ),
            function(
                "workspace_git_diff",
                "Read Git diff for the active workspace, optionally for one workspace-relative path.",
                {"path": string},
                [],
            ),
            function(
                "workspace_dotnet_build",
                "Build a multi-file .NET workspace snapshot inside the existing approved Docker sandbox. Requires an offline local NuGet feed; no host project execution or network access is permitted.",
                {"project": string},
                [],
            ),
            function(
                "workspace_dotnet_test",
                "Run .NET tests for a multi-file workspace snapshot inside the existing approved Docker sandbox. Requires an offline local NuGet feed; no host project execution or network access is permitted.",
                {"project": string},
                [],
            ),
        ]

    def _safe_path(self, value: str, *, must_exist: bool = False) -> Path:
        if (
            not isinstance(value, str)
            or not value
            or len(value) > 4096
            or "\x00" in value
            or "\\" in value
            or Path(value).is_absolute()
            or PureWindowsPath(value).is_absolute()
        ):
            raise ValueError("path must be a bounded workspace-relative path")
        relative = PurePosixPath(value)
        if any(part in {"..", ""} for part in relative.parts):
            raise ValueError("path traversal is not allowed")
        current = self.root
        for part in relative.parts:
            if part == ".":
                continue
            current = current / part
            if current.is_symlink():
                raise ValueError("symbolic links are not allowed in workspace tool paths")
        resolved = current.resolve(strict=must_exist)
        if resolved != self.root and self.root not in resolved.parents:
            raise ValueError("path escapes the active workspace")
        if must_exist and not resolved.exists():
            raise FileNotFoundError("workspace path does not exist")
        if resolved != self.root and self._is_sensitive(resolved):
            raise PermissionError("access to secret or credential files is blocked")
        return resolved

    def _open_parent_fd(self, relative_path: str) -> tuple[int, str]:
        """Open a workspace parent by directory descriptors without following symlinks."""
        self._safe_path(relative_path)
        parts = PurePosixPath(relative_path).parts
        parts = tuple(part for part in parts if part != ".")
        if not parts:
            raise ValueError("path must identify a file inside the workspace")
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        parent_fd = os.open(self.root, flags)
        try:
            for part in parts[:-1]:
                next_fd = os.open(part, flags, dir_fd=parent_fd)
                os.close(parent_fd)
                parent_fd = next_fd
            return parent_fd, parts[-1]
        except Exception:
            os.close(parent_fd)
            raise

    def _read_file_posix(self, relative_path: str) -> dict[str, Any]:
        parent_fd, name = self._open_parent_fd(relative_path)
        try:
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            file_fd = os.open(name, flags, dir_fd=parent_fd)
            try:
                metadata = os.fstat(file_fd)
                if not stat.S_ISREG(metadata.st_mode):
                    raise ValueError("path must identify a regular file")
                if metadata.st_size > self.max_file_bytes:
                    raise ValueError(f"file exceeds the {self.max_file_bytes}-byte read limit")
                chunks = []
                remaining = self.max_file_bytes + 1
                while remaining:
                    chunk = os.read(file_fd, min(65536, remaining))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
                raw = b"".join(chunks)
                if len(raw) > self.max_file_bytes:
                    raise ValueError(f"file exceeds the {self.max_file_bytes}-byte read limit")
            finally:
                os.close(file_fd)
        finally:
            os.close(parent_fd)
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("only UTF-8 text files can be read") from exc
        return {
            "status": "ok",
            "path": PurePosixPath(relative_path).as_posix(),
            "content": content,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
        }

    def _write_file_posix(
        self,
        relative_path: str,
        content: str,
        expected_sha256: str | None,
        cancellation_event: threading.Event | None,
    ) -> dict[str, Any]:
        parent_fd, name = self._open_parent_fd(relative_path)
        temporary = None
        try:
            before_sha = None
            try:
                existing_fd = os.open(
                    name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent_fd
                )
            except FileNotFoundError:
                exists = False
            else:
                exists = True
                try:
                    metadata = os.fstat(existing_fd)
                    if not __import__("stat").S_ISREG(metadata.st_mode):
                        raise ValueError("target must be a regular file")
                    if metadata.st_size > 10_000_000:
                        raise ValueError("existing file is too large to replace safely")
                    if expected_sha256 is None:
                        raise ValueError("expected_sha256 is required before overwriting an existing file")
                    digest = hashlib.sha256()
                    while True:
                        chunk = os.read(existing_fd, 65536)
                        if not chunk:
                            break
                        digest.update(chunk)
                    before_sha = digest.hexdigest()
                finally:
                    os.close(existing_fd)
                if before_sha != expected_sha256:
                    return {"status": "conflict", "error": "file changed since it was read"}
            if not exists and expected_sha256 is not None:
                return {"status": "conflict", "error": "expected existing file is missing"}

            after_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
            approval = ToolApproval(
                tool_name="workspace_write_file",
                workspace_id=self.workspace_id,
                summary=f"Write {PurePosixPath(relative_path).as_posix()}",
                details={
                    "path": PurePosixPath(relative_path).as_posix(),
                    "before_sha256": before_sha,
                    "after_sha256": after_sha,
                    "bytes": len(content.encode("utf-8")),
                    "content_preview": content[:4000],
                },
            )
            if self.approval is None or not self.approval(approval):
                return {"status": "approval_denied", "path": relative_path}
            if cancellation_event is not None and cancellation_event.is_set():
                return {"status": "cancelled", "path": relative_path}

            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            for _ in range(10):
                candidate = f".cds-ai-{secrets.token_hex(12)}"
                try:
                    temp_fd = os.open(candidate, flags, 0o600, dir_fd=parent_fd)
                    temporary = candidate
                    break
                except FileExistsError:
                    continue
            else:
                raise FileExistsError("could not allocate a unique workspace temporary file")
            with os.fdopen(temp_fd, "w", encoding="utf-8", newline="") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            if cancellation_event is not None and cancellation_event.is_set():
                return {"status": "cancelled", "path": relative_path}
            # Revalidate the destination after approval and immediately before commit.
            try:
                current_fd = os.open(
                    name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent_fd
                )
            except FileNotFoundError:
                if exists:
                    return {"status": "conflict", "error": "file changed before replacement"}
            except OSError as exc:
                if exc.errno in {errno.ELOOP, errno.EMLINK}:
                    raise ValueError("symbolic links are not allowed in workspace tool paths") from exc
                raise
            else:
                try:
                    current_meta = os.fstat(current_fd)
                    if not stat.S_ISREG(current_meta.st_mode):
                        raise ValueError("target must remain a regular file")
                    if not exists:
                        return {"status": "conflict", "error": "target appeared before replacement"}
                    current_digest = hashlib.sha256()
                    while True:
                        chunk = os.read(current_fd, 65536)
                        if not chunk:
                            break
                        current_digest.update(chunk)
                    if current_digest.hexdigest() != before_sha:
                        return {"status": "conflict", "error": "file changed before replacement"}
                finally:
                    os.close(current_fd)
            os.replace(
                temporary, name,
                src_dir_fd=parent_fd, dst_dir_fd=parent_fd,
            )
            temporary = None
            return {
                "status": "ok",
                "path": PurePosixPath(relative_path).as_posix(),
                "sha256": after_sha,
                "bytes": len(content.encode("utf-8")),
            }
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary, dir_fd=parent_fd)
                except FileNotFoundError:
                    pass
            os.close(parent_fd)

    @classmethod
    def _is_sensitive(cls, path: Path) -> bool:
        name = path.name.lower()
        if name in cls.BLOCKED_FILENAMES:
            return True
        if name.startswith(".env.") and name != ".env.example":
            return True
        if path.suffix.lower() in cls.BLOCKED_SUFFIXES:
            return True
        return any(part.lower() in cls.IGNORED_DIRECTORIES for part in path.parts)

    def _list_files(self, directory: str = ".") -> dict[str, Any]:
        root = self._safe_path(directory, must_exist=True)
        if not root.is_dir():
            raise ValueError("directory must identify a workspace directory")
        found: list[str] = []
        for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
            current_path = Path(current)
            dirs[:] = [
                name for name in dirs
                if name not in self.IGNORED_DIRECTORIES
                and not (current_path / name).is_symlink()
            ]
            for name in files:
                path = current_path / name
                if path.is_symlink() or self._is_sensitive(path):
                    continue
                found.append(path.relative_to(self.root).as_posix())
                if len(found) >= 200:
                    return {"status": "ok", "files": found, "truncated": True}
        return {"status": "ok", "files": found, "truncated": False}

    def _read_file(self, relative_path: str) -> dict[str, Any]:
        if os.name != "nt" and os.open in getattr(os, "supports_dir_fd", set()):
            return self._read_file_posix(relative_path)
        path = self._safe_path(relative_path, must_exist=True)
        if not path.is_file():
            raise ValueError("path must identify a regular file")
        size = path.stat().st_size
        if size > self.max_file_bytes:
            raise ValueError(f"file exceeds the {self.max_file_bytes}-byte read limit")
        raw = path.read_bytes()
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("only UTF-8 text files can be read") from exc
        return {
            "status": "ok",
            "path": path.relative_to(self.root).as_posix(),
            "content": content,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
        }

    def _search_text(self, query: str, directory: str = ".") -> dict[str, Any]:
        if not isinstance(query, str) or not query or len(query) > 1000:
            raise ValueError("query must contain between 1 and 1000 characters")
        listing = self._list_files(directory)
        matches: list[dict[str, Any]] = []
        scanned = 0
        scanned_bytes = 0
        for relative in listing["files"]:
            scanned += 1
            if scanned > 200 or scanned_bytes >= 20_000_000:
                break
            try:
                item = self._read_file(relative)
            except (OSError, ValueError, PermissionError):
                continue
            scanned_bytes += item["bytes"]
            for line_number, line in enumerate(item["content"].splitlines(), start=1):
                if query in line:
                    matches.append({
                        "path": relative,
                        "line": line_number,
                        "text": line[:1000],
                    })
                    if len(matches) >= 100:
                        return {"status": "ok", "matches": matches, "truncated": True}
        return {
            "status": "ok",
            "matches": matches,
            "truncated": listing["truncated"] or scanned > 200 or scanned_bytes >= 20_000_000,
        }

    def _write_file(
        self,
        relative_path: str,
        content: str,
        expected_sha256: str | None,
        cancellation_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        if not isinstance(content, str) or len(content.encode("utf-8")) > 300_000:
            raise ValueError("file content exceeds the 300000-byte write limit")
        if os.name != "nt" and os.open in getattr(os, "supports_dir_fd", set()):
            return self._write_file_posix(
                relative_path, content, expected_sha256, cancellation_event
            )
        target = self._safe_path(relative_path)
        if target == self.root or not target.parent.is_dir():
            raise ValueError("target parent directory must already exist inside the workspace")
        exists = target.exists()
        before_sha = None
        if exists:
            if not target.is_file():
                raise ValueError("target must be a regular file")
            if target.stat().st_size > 10_000_000:
                raise ValueError("existing file is too large to replace safely")
            if expected_sha256 is None:
                raise ValueError("expected_sha256 is required before overwriting an existing file")
            before_sha = hashlib.sha256(target.read_bytes()).hexdigest()
            if before_sha != expected_sha256:
                return {"status": "conflict", "error": "file changed since it was read"}
        elif expected_sha256 is not None:
            return {"status": "conflict", "error": "expected existing file is missing"}

        after_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
        approval = ToolApproval(
            tool_name="workspace_write_file",
            workspace_id=self.workspace_id,
            summary=f"Write {target.relative_to(self.root).as_posix()}",
            details={
                "path": target.relative_to(self.root).as_posix(),
                "before_sha256": before_sha,
                "after_sha256": after_sha,
                "bytes": len(content.encode("utf-8")),
                "content_preview": content[:4000],
            },
        )
        if self.approval is None or not self.approval(approval):
            return {"status": "approval_denied", "path": relative_path}
        if cancellation_event is not None and cancellation_event.is_set():
            return {"status": "cancelled", "path": relative_path}

        fd, temporary = tempfile.mkstemp(prefix=".cds-ai-", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            if cancellation_event is not None and cancellation_event.is_set():
                return {"status": "cancelled", "path": relative_path}
            if target.is_symlink():
                raise ValueError("symbolic links are not allowed in workspace tool paths")
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return {
            "status": "ok",
            "path": target.relative_to(self.root).as_posix(),
            "sha256": after_sha,
            "bytes": len(content.encode("utf-8")),
        }

    def _run_fixed_command(
        self,
        command: list[str],
        tool_name: str,
        summary: str,
        require_approval: bool = True,
        cancellation_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        if cancellation_event is not None and cancellation_event.is_set():
            return {"status": "cancelled"}
        if require_approval and (self.approval is None or not self.approval(
            ToolApproval(
                tool_name=tool_name,
                workspace_id=self.workspace_id,
                summary=summary,
                details={
                    "command": command,
                    "cwd": str(self.root),
                    "execution_boundary": "workstation_host_process",
                    "sandboxed": False,
                    "risk_warning": (
                        "The project controls MSBuild targets, analyzers, generators and test code. "
                        "They may execute child processes, access files available to the current user, "
                        "and use host network access. shell=False prevents shell interpolation but "
                        "does not sandbox project-controlled execution."
                    ),
                },
            )
        )):
            return {"status": "approval_denied", "command": command}
        if cancellation_event is not None and cancellation_event.is_set():
            return {"status": "cancelled"}
        executable = shutil.which(command[0])
        if executable is None:
            return {"status": "unavailable", "error": f"{command[0]} is not installed"}
        command = [executable, *command[1:]]
        allowed_env_names = {
            "PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "WINDIR",
            "DOTNET_ROOT", "DOTNET_CLI_HOME", "NUGET_PACKAGES",
            "NUGET_HTTP_CACHE_PATH", "TMP", "TEMP", "TMPDIR", "PATHEXT",
        }
        environment = {
            key: value for key, value in os.environ.items()
            if key in allowed_env_names
        }
        environment.update({"DOTNET_CLI_TELEMETRY_OPTOUT": "1", "DOTNET_NOLOGO": "1"})
        popen_options: dict[str, Any] = {}
        if os.name == "nt":
            popen_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_options["start_new_session"] = True

        process = None

        def stop_process() -> tuple[str, str]:
            if process is None:
                return "", ""
            try:
                if os.name == "nt":
                    process.terminate()
                else:
                    os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            except OSError:
                try:
                    process.terminate()
                except OSError:
                    pass
            try:
                return process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    if os.name == "nt":
                        process.kill()
                    else:
                        os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except OSError:
                    try:
                        process.kill()
                    except OSError:
                        pass
                return process.communicate()

        try:
            process = subprocess.Popen(
                command, cwd=self.root, env=environment,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, shell=False, **popen_options,
            )
            deadline = time.monotonic() + self.command_timeout_seconds
            while True:
                if cancellation_event is not None and cancellation_event.is_set():
                    stdout, stderr = stop_process()
                    return {
                        "status": "cancelled",
                        "stdout": stdout[-self.max_output_chars:],
                        "stderr": stderr[-self.max_output_chars:],
                    }
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    stdout, stderr = stop_process()
                    return {
                        "status": "timeout",
                        "timeout_seconds": self.command_timeout_seconds,
                        "stdout": stdout[-self.max_output_chars:],
                        "stderr": stderr[-self.max_output_chars:],
                    }
                try:
                    stdout, stderr = process.communicate(timeout=min(0.1, remaining))
                    return {
                        "status": "ok" if process.returncode == 0 else "failed",
                        "exit_code": process.returncode,
                        "stdout": stdout[-self.max_output_chars:],
                        "stderr": stderr[-self.max_output_chars:],
                        "output_truncated": (
                            len(stdout) > self.max_output_chars
                            or len(stderr) > self.max_output_chars
                        ),
                    }
                except subprocess.TimeoutExpired:
                    continue
        except OSError as exc:
            return {"status": "failed", "error": f"{type(exc).__name__}: {str(exc)[:500]}"}


    def _workspace_snapshot(
        self, cancellation_event: threading.Event | None = None
    ) -> list[dict[str, str]]:
        files: list[dict[str, str]] = []
        total_bytes = 0
        for directory, dirnames, filenames in os.walk(self.root, topdown=True, followlinks=False):
            current = Path(directory)
            kept_directories = []
            for name in sorted(dirnames):
                child = current / name
                if name in self.IGNORED_DIRECTORIES:
                    continue
                if child.is_symlink():
                    raise ValueError("workspace snapshot refuses symbolic-link directories")
                kept_directories.append(name)
            dirnames[:] = kept_directories

            for name in sorted(filenames):
                if cancellation_event is not None and cancellation_event.is_set():
                    raise InterruptedError("workspace snapshot was cancelled")
                target = current / name
                if target.is_symlink():
                    raise ValueError("workspace snapshot refuses symbolic-link files")
                if name in self.BLOCKED_FILENAMES or name.casefold() in {
                    ".npmrc", ".pypirc", "nuget.config",
                } or target.suffix.lower() in self.BLOCKED_SUFFIXES:
                    continue
                relative = target.relative_to(self.root).as_posix()
                metadata = target.stat(follow_symlinks=False)
                if not stat.S_ISREG(metadata.st_mode):
                    raise ValueError("workspace snapshot accepts regular files only")
                if metadata.st_size > min(self.max_file_bytes, 1_000_000):
                    raise ValueError(f"workspace file exceeds the 1 MiB snapshot limit: {relative}")
                read = self._read_file(relative)
                if read.get("status") != "ok":
                    raise ValueError(f"workspace file could not be snapshotted: {relative}")
                total_bytes += read["bytes"]
                if total_bytes > 5 * 1024 * 1024:
                    raise ValueError("workspace snapshot exceeds the 5 MiB limit")
                files.append({"path": relative, "content": read["content"]})
                if len(files) > 200:
                    raise ValueError("workspace snapshot exceeds the 200-file limit")
        return files

    def _run_workspace_sandbox(
        self,
        action: str,
        project: str | None,
        cancellation_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        if self.sandbox_executor is None:
            return {
                "status": "unavailable",
                "error": "The existing Docker workspace sandbox is not configured; host Build/Test is disabled.",
            }
        if cancellation_event is not None and cancellation_event.is_set():
            return {"status": "cancelled"}
        files = self._workspace_snapshot(cancellation_event)
        project_files = {
            item["path"]
            for item in files
            if PurePosixPath(item["path"]).suffix.lower() in {".sln", ".slnx", ".csproj"}
        }
        if project:
            safe_project = self._safe_path(project, must_exist=True)
            if not safe_project.is_file() or safe_project.suffix.lower() not in {
                ".sln", ".slnx", ".csproj",
            }:
                raise ValueError("project must be a .sln, .slnx, or .csproj file")
            project_path = safe_project.relative_to(self.root).as_posix()
        else:
            solutions = sorted(
                item for item in project_files
                if PurePosixPath(item).suffix.lower() in {".sln", ".slnx"}
            )
            candidates = solutions or sorted(project_files)
            if len(candidates) != 1:
                raise ValueError(
                    "specify a project path when the workspace does not contain exactly one solution/project"
                )
            project_path = candidates[0]
        if project_path not in project_files:
            raise ValueError("selected project file is not included in the workspace snapshot")
        if cancellation_event is not None and cancellation_event.is_set():
            return {"status": "cancelled"}
        return self.sandbox_executor(
            self.workspace_id,
            action,
            project_path,
            files,
            self.approval,
            cancellation_event,
        )

    def _dispatch(
        self,
        name: str,
        arguments: dict[str, Any],
        cancellation_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        if cancellation_event is not None and cancellation_event.is_set():
            return {"status": "cancelled"}
        if name == "workspace_list_files":
            return self._list_files(arguments.get("directory", "."))
        if name == "workspace_read_file":
            return self._read_file(arguments["path"])
        if name == "workspace_search_text":
            return self._search_text(arguments["query"], arguments.get("directory", "."))
        if name == "workspace_write_file":
            expected = arguments.get("expected_sha256")
            if expected is not None and (
                not isinstance(expected, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", expected)
            ):
                raise ValueError("expected_sha256 must be a 64-character SHA-256 hex digest")
            return self._write_file(
                arguments["path"], arguments["content"], expected, cancellation_event
            )
        if name == "workspace_git_status":
            return self._run_fixed_command(
                ["git", "status", "--short", "--branch"],
                name,
                "Read Git status",
                require_approval=False,
                cancellation_event=cancellation_event,
            )
        if name == "workspace_git_diff":
            command = ["git", "diff", "--no-ext-diff", "--no-color", "--"]
            if arguments.get("path"):
                path = self._safe_path(arguments["path"])
                command.append(path.relative_to(self.root).as_posix())
            return self._run_fixed_command(
                command, name, "Read Git diff", require_approval=False,
                cancellation_event=cancellation_event,
            )
        if name in {"workspace_dotnet_build", "workspace_dotnet_test"}:
            return self._run_workspace_sandbox(
                "build" if name.endswith("build") else "test",
                arguments.get("project"),
                cancellation_event,
            )
        raise ToolProtocolError(f"tool is not registered: {name}")
