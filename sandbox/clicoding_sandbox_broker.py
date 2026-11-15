#!/usr/bin/env python3
"""Root-owned Unix-socket broker for approved CLICoding sandbox executions."""
from __future__ import annotations

import grp
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import socketserver
import signal
import threading
import time
import sqlite3
import stat
import subprocess
import sys
from datetime import datetime, timezone
import uuid

SOCKET_PATH = os.getenv("CLICODING_BROKER_SOCKET", "/run/clicoding-sandbox/broker.sock")
DATABASE_PATH = os.getenv("CLICODING_DATABASE_PATH", "")
RUNNER_PATH = "/usr/local/sbin/clicoding-sandbox-run"
SOCKET_GROUP = os.getenv("CLICODING_BROKER_GROUP", "clicoding-sandbox")
MAX_REQUEST_BYTES = 4096
MAX_RESPONSE_BYTES = 512 * 1024
RUNNER_TIMEOUT_SECONDS = 660
RUNNING_EXECUTIONS: dict[str, subprocess.Popen] = {}
RUNNING_EXECUTIONS_LOCK = threading.Lock()
DOCKER_PATH = "/usr/bin/docker"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def database_connection() -> sqlite3.Connection:
    if not DATABASE_PATH or not Path(DATABASE_PATH).is_absolute():
        raise RuntimeError("CLICODING_DATABASE_PATH must be an absolute path")
    connection = sqlite3.connect(DATABASE_PATH, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=15000")
    return connection


def claim_approval(approval_id: str) -> tuple[str, str, str]:
    """Atomically consume an approved immutable source snapshot."""
    if not isinstance(approval_id, str):
        raise ValueError("approval_id must be a UUID string")
    try:
        approval_id = str(uuid.UUID(approval_id))
    except (ValueError, AttributeError) as exc:
        raise ValueError("invalid approval_id") from exc

    connection = database_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sandbox_execution_claims (
                approval_id TEXT PRIMARY KEY,
                source_sha256 TEXT NOT NULL,
                status TEXT NOT NULL,
                claimed_at TEXT NOT NULL,
                finished_at TEXT
            )
            """
        )
        row = connection.execute(
            """
            SELECT language, source, source_sha256, status, expires_at
            FROM sandbox_approvals WHERE id = ?
            """,
            (approval_id,),
        ).fetchone()
        if row is None:
            raise ValueError("approval not found")
        if row["status"] != "approved":
            raise ValueError(f"approval is not approved (status={row['status']})")
        if row["expires_at"] <= now():
            raise ValueError("approval has expired")
        if row["language"] not in {"python", "javascript", "typescript", "go", "rust", "java", "c", "cpp", "csharp", "php", "ruby"}:
            raise ValueError("unsupported approved language")
        source = row["source"]
        source_bytes = source.encode("utf-8")
        digest = hashlib.sha256(source_bytes).hexdigest()
        if not hmac.compare_digest(digest, row["source_sha256"]):
            raise ValueError("approved source digest mismatch")
        connection.execute(
            """
            INSERT INTO sandbox_execution_claims
                (approval_id, source_sha256, status, claimed_at)
            VALUES (?, ?, 'running', ?)
            """,
            (approval_id, digest, now()),
        )
        connection.commit()
        return row["language"], source, digest
    except sqlite3.IntegrityError as exc:
        connection.rollback()
        raise ValueError("approval has already been consumed") from exc
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()



def claim_workspace_approval(approval_id: str) -> dict:
    """Atomically consume an approved immutable multi-file workspace snapshot."""
    if not isinstance(approval_id, str):
        raise ValueError("approval_id must be a UUID string")
    try:
        approval_id = str(uuid.UUID(approval_id))
    except (ValueError, AttributeError) as exc:
        raise ValueError("invalid approval_id") from exc

    connection = database_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sandbox_execution_claims (
                approval_id TEXT PRIMARY KEY,
                source_sha256 TEXT NOT NULL,
                status TEXT NOT NULL,
                claimed_at TEXT NOT NULL,
                finished_at TEXT
            )
            """
        )
        row = connection.execute(
            """
            SELECT action, project_path, files_json, snapshot_sha256, status, expires_at
            FROM sandbox_workspace_approvals WHERE id = ?
            """,
            (approval_id,),
        ).fetchone()
        if row is None:
            raise ValueError("workspace approval not found")
        if row["status"] != "approved":
            raise ValueError(f"workspace approval is not approved (status={row['status']})")
        if row["expires_at"] <= now():
            raise ValueError("workspace approval has expired")
        if row["action"] not in {"build", "test"}:
            raise ValueError("unsupported workspace sandbox action")

        try:
            files = json.loads(row["files_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("workspace snapshot JSON is invalid") from exc
        if not isinstance(files, list) or not 1 <= len(files) <= 200:
            raise ValueError("workspace snapshot file count is invalid")
        total_bytes = 0
        seen: set[str] = set()
        manifest = []
        target_found = False
        for item in files:
            if not isinstance(item, dict) or set(item) != {"path", "content", "sha256", "bytes"}:
                raise ValueError("workspace snapshot entry is invalid")
            path = item["path"]
            if (
                not isinstance(path, str) or not path or len(path) > 512
                or "\\" in path or "\x00" in path
                or PurePosixPath(path).is_absolute()
                or PureWindowsPath(path).is_absolute()
                or any(part in {"", ".", ".."} for part in PurePosixPath(path).parts)
            ):
                raise ValueError("workspace snapshot path is unsafe")
            relative = PurePosixPath(path)
            key = relative.as_posix().casefold()
            if key in seen:
                raise ValueError("workspace snapshot contains duplicate paths")
            seen.add(key)
            if any(part.casefold() in {
                ".git", ".ssh", ".aws", ".azure", ".gnupg", ".venv", "venv",
                "node_modules", "bin", "obj", "__pycache__", ".idea", ".vs",
                "secrets", "credentials",
            } for part in relative.parts[:-1]):
                raise ValueError("workspace snapshot contains a blocked directory")
            filename = relative.name.casefold()
            if filename in {
                ".env", ".git-credentials", "id_rsa", "id_ed25519",
                "credentials.json", "secrets.json", ".npmrc", ".pypirc", "nuget.config",
            } or (filename.startswith(".env.") and filename != ".env.example") or Path(filename).suffix in {
                ".pem", ".key", ".p12", ".pfx", ".kdb",
            }:
                raise ValueError("workspace snapshot contains a blocked credential file")
            source = item["content"]
            if not isinstance(source, str):
                raise ValueError("workspace snapshot content must be UTF-8 text")
            encoded = source.encode("utf-8")
            digest = hashlib.sha256(encoded).hexdigest()
            if len(encoded) > 1_000_000 or item["bytes"] != len(encoded):
                raise ValueError("workspace snapshot file size mismatch")
            if not isinstance(item["sha256"], str) or not hmac.compare_digest(
                digest, item["sha256"]
            ):
                raise ValueError("workspace snapshot file digest mismatch")
            total_bytes += len(encoded)
            if total_bytes > 5 * 1024 * 1024:
                raise ValueError("workspace snapshot exceeds 5 MiB")
            manifest.append({"path": relative.as_posix(), "sha256": digest, "bytes": len(encoded)})
            if relative.as_posix() == row["project_path"]:
                target_found = True

        project_path = row["project_path"]
        if (
            not isinstance(project_path, str)
            or PurePosixPath(project_path).suffix.lower() not in {".sln", ".slnx", ".csproj"}
            or not target_found
        ):
            raise ValueError("workspace project target is missing or unsupported")
        manifest.sort(key=lambda entry: entry["path"])
        canonical = json.dumps(
            manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        snapshot_digest = hashlib.sha256(canonical).hexdigest()
        if not hmac.compare_digest(snapshot_digest, row["snapshot_sha256"]):
            raise ValueError("workspace snapshot digest mismatch")

        connection.execute(
            """
            INSERT INTO sandbox_execution_claims
                (approval_id, source_sha256, status, claimed_at)
            VALUES (?, ?, 'running', ?)
            """,
            (approval_id, snapshot_digest, now()),
        )
        connection.commit()
        return {
            "approval_id": approval_id,
            "action": row["action"],
            "project_path": project_path,
            "files": files,
            "snapshot_sha256": snapshot_digest,
        }
    except sqlite3.IntegrityError as exc:
        connection.rollback()
        raise ValueError("approval has already been consumed") from exc
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def persist_result(approval_id: str, digest: str, result: dict) -> None:
    connection = database_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        claim = connection.execute(
            "SELECT status FROM sandbox_execution_claims WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()
        if claim is not None and claim["status"] == "cancel_requested":
            result = {**result, "status": "cancelled", "cancel_requested": True}
        workspace = connection.execute(
            "SELECT 1 FROM sandbox_workspace_approvals WHERE id = ?",
            (approval_id,),
        ).fetchone()
        if workspace is not None:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS sandbox_workspace_execution_results (
                    approval_id TEXT PRIMARY KEY,
                    source_sha256 TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (approval_id) REFERENCES sandbox_workspace_approvals(id)
                )
                """
            )
            table = "sandbox_workspace_execution_results"
        else:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS sandbox_execution_results (
                    approval_id TEXT PRIMARY KEY,
                    source_sha256 TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (approval_id) REFERENCES sandbox_approvals(id)
                )
                """
            )
            table = "sandbox_execution_results"
        connection.execute(
            f"""
            INSERT INTO {table}
                (approval_id, source_sha256, status, result_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (approval_id, digest, str(result.get("status", "runtime_error")),
             json.dumps(result, ensure_ascii=False), now()),
        )
        connection.execute(
            """
            UPDATE sandbox_execution_claims
            SET status = ?, finished_at = ?
            WHERE approval_id = ?
            """,
            (str(result.get("status", "runtime_error")), now(), approval_id),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def is_cancel_requested(approval_id: str) -> bool:
    connection = database_connection()
    try:
        row = connection.execute(
            "SELECT status FROM sandbox_execution_claims WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()
        return row is not None and row["status"] == "cancel_requested"
    finally:
        connection.close()


def execute_approval(approval_id: str) -> dict:
    try:
        language, source, digest = claim_approval(approval_id)
        payload_object = {"language": language, "code": source}
    except ValueError as exc:
        if str(exc) != "approval not found":
            raise
        workspace = claim_workspace_approval(approval_id)
        digest = workspace["snapshot_sha256"]
        payload_object = {"mode": "workspace", **workspace}
    payload = json.dumps(payload_object, ensure_ascii=False).encode("utf-8")
    process = None
    try:
        if is_cancel_requested(approval_id):
            result = {"status": "cancelled", "error": "cancelled before runner start"}
            result["approval_id"] = approval_id
            result["source_sha256"] = digest
            persist_result(approval_id, digest, result)
            return result
        process = subprocess.Popen(
            [RUNNER_PATH],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            start_new_session=True,
        )
        with RUNNING_EXECUTIONS_LOCK:
            RUNNING_EXECUTIONS[approval_id] = process
        if is_cancel_requested(approval_id):
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        stdout, stderr = process.communicate(
            input=payload, timeout=RUNNER_TIMEOUT_SECONDS
        )
        if len(stdout) > MAX_RESPONSE_BYTES:
            raise RuntimeError("runner response exceeded broker limit")
        try:
            result = json.loads(stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("runner returned invalid JSON") from exc
        if not isinstance(result, dict) or "status" not in result:
            raise RuntimeError("runner returned an invalid result object")
        if stderr:
            result["broker_stderr"] = stderr.decode("utf-8", errors="replace")[:4096]
    except subprocess.TimeoutExpired:
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                pass
        result = {"status": "runtime_error", "error": "runner process exceeded broker timeout"}
    except Exception as exc:
        result = {"status": "runtime_error", "error": f"{type(exc).__name__}: {exc}"}
    finally:
        with RUNNING_EXECUTIONS_LOCK:
            RUNNING_EXECUTIONS.pop(approval_id, None)
    result["approval_id"] = approval_id
    result["source_sha256"] = digest
    persist_result(approval_id, digest, result)
    return result


def cancel_workspace_approval(approval_id: str) -> dict:
    if not isinstance(approval_id, str):
        raise ValueError("cancel_approval_id must be a UUID string")
    try:
        approval_id = str(uuid.UUID(approval_id))
    except (ValueError, AttributeError) as exc:
        raise ValueError("invalid cancel_approval_id") from exc

    connection = database_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sandbox_execution_claims (
                approval_id TEXT PRIMARY KEY,
                source_sha256 TEXT NOT NULL,
                status TEXT NOT NULL,
                claimed_at TEXT NOT NULL,
                finished_at TEXT
            )
            """
        )
        approval = connection.execute(
            "SELECT status FROM sandbox_workspace_approvals WHERE id = ?",
            (approval_id,),
        ).fetchone()
        if approval is None:
            raise ValueError("workspace approval not found")
        if approval["status"] == "pending":
            connection.execute(
                "UPDATE sandbox_workspace_approvals SET status = 'rejected', decided_at = ? "
                "WHERE id = ? AND status = 'pending'",
                (now(), approval_id),
            )
            connection.commit()
            return {"status": "cancelled", "approval_id": approval_id}
        if approval["status"] != "approved":
            connection.commit()
            return {"status": "not_cancellable", "approval_id": approval_id}
        claim = connection.execute(
            "SELECT status FROM sandbox_execution_claims WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()
        if claim is None:
            connection.execute(
                "UPDATE sandbox_workspace_approvals SET status = 'rejected', decided_at = ? "
                "WHERE id = ? AND status = 'approved'",
                (now(), approval_id),
            )
            connection.commit()
            return {"status": "cancelled", "approval_id": approval_id}
        if claim["status"] not in {"running", "cancel_requested"}:
            connection.commit()
            return {"status": "already_finished", "approval_id": approval_id}
        connection.execute(
            "UPDATE sandbox_execution_claims SET status = 'cancel_requested' "
            "WHERE approval_id = ? AND status = 'running'",
            (approval_id,),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    with RUNNING_EXECUTIONS_LOCK:
        process = RUNNING_EXECUTIONS.get(approval_id)
    if process is not None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass

    stopped = False
    docker_query_succeeded = False
    for _ in range(5):
        try:
            listed = subprocess.run(
                [DOCKER_PATH, "ps", "-aq", "--filter", f"label=clicoding.approval={approval_id}"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3, check=False,
            )
            if listed.returncode == 0:
                docker_query_succeeded = True
            ids = [
                item for item in listed.stdout.decode("ascii", errors="ignore").splitlines()
                if item and all(ch in "0123456789abcdef" for ch in item.lower())
            ]
            if ids:
                removed = subprocess.run(
                    [DOCKER_PATH, "rm", "--force", *ids],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5, check=False,
                )
                stopped = removed.returncode == 0
                if stopped:
                    break
        except (OSError, subprocess.TimeoutExpired):
            pass
        time.sleep(0.2)
    if not stopped and docker_query_succeeded:
        stopped = True
    if process is not None and process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    return {
        "status": "cancel_requested" if stopped else "cancel_pending",
        "approval_id": approval_id,
        "container_stopped": stopped,
    }


class Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        self.connection.settimeout(20)
        raw = self.rfile.readline(MAX_REQUEST_BYTES + 1)
        if not raw or len(raw) > MAX_REQUEST_BYTES or not raw.endswith(b"\n"):
            self.wfile.write(b'{"status":"rejected","error":"invalid request"}\n')
            return
        try:
            request = json.loads(raw)
            if not isinstance(request, dict):
                raise ValueError("request must be a JSON object")
            if set(request) == {"approval_id"}:
                result = execute_approval(request["approval_id"])
            elif set(request) == {"cancel_approval_id"}:
                result = cancel_workspace_approval(request["cancel_approval_id"])
            else:
                raise ValueError("request must contain approval_id or cancel_approval_id")
        except ValueError as exc:
            result = {"status": "rejected", "error": str(exc)}
        except Exception as exc:
            result = {"status": "broker_error", "error": f"{type(exc).__name__}: {exc}"}
        encoded = json.dumps(result, ensure_ascii=False).encode("utf-8")
        if len(encoded) > MAX_RESPONSE_BYTES:
            encoded = b'{"status":"broker_error","error":"response too large"}'
        self.wfile.write(encoded + b"\n")


class UnixServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = False
    allow_reuse_address = False


def main() -> int:
    if os.geteuid() != 0:
        print("broker must run as root", file=sys.stderr)
        return 2
    if not Path(RUNNER_PATH).is_file():
        print("sandbox runner is not installed", file=sys.stderr)
        return 2
    socket_path = Path(SOCKET_PATH)
    socket_path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    if socket_path.exists():
        mode = socket_path.lstat().st_mode
        if not stat.S_ISSOCK(mode):
            print("refusing to replace non-socket path", file=sys.stderr)
            return 2
        socket_path.unlink()
    try:
        group_id = grp.getgrnam(SOCKET_GROUP).gr_gid
    except KeyError:
        print(f"required socket group does not exist: {SOCKET_GROUP}", file=sys.stderr)
        return 2
    os.chown(socket_path.parent, 0, group_id)
    os.chmod(socket_path.parent, 0o750)
    server = UnixServer(str(socket_path), Handler)
    os.chown(socket_path, 0, group_id)
    os.chmod(socket_path, 0o660)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        try:
            socket_path.unlink()
        except FileNotFoundError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
