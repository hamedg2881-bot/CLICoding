from __future__ import annotations

from dataclasses import dataclass
import math
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml


@dataclass(frozen=True)
class Worker:
    id: str
    base_url: str
    model: str
    capabilities: frozenset[str]
    enabled: bool = True
    priority: int = 100
    api_key: str | None = None
    timeout_seconds: float = 180.0
    max_concurrency: int = 1


class RegistryError(ValueError):
    pass


def runtime_key(worker: Worker) -> tuple[str, str]:
    """Identify a served runtime independently of logical worker/capability IDs."""
    return worker.base_url.rstrip("/"), worker.model


class WorkerRegistry:
    """Configuration-backed registry. Loading it never probes or starts runtimes."""

    def __init__(self, workers: list[Worker]):
        ids = [worker.id for worker in workers]
        if len(ids) != len(set(ids)):
            raise RegistryError("Worker IDs must be unique")
        for index, worker in enumerate(workers):
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", worker.id):
                raise RegistryError(f"workers[{index}].id has an invalid format")
            parsed = urlsplit(worker.base_url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
            ):
                raise RegistryError(f"workers[{index}].base_url must be a valid HTTP(S) URL")
            if not worker.model.strip() or not worker.capabilities:
                raise RegistryError(f"workers[{index}] requires a model and capabilities")
            if any(
                not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", capability)
                for capability in worker.capabilities
            ):
                raise RegistryError(f"workers[{index}] has an invalid capability name")
            if (
                not isinstance(worker.enabled, bool)
                or worker.priority < 0
                or worker.max_concurrency < 1
                or not math.isfinite(worker.timeout_seconds)
                or worker.timeout_seconds <= 0
            ):
                raise RegistryError(f"workers[{index}] has invalid limits or enabled state")
        self._workers = tuple(workers)

    @classmethod
    def from_yaml(cls, path: str) -> "WorkerRegistry":
        source = Path(path)
        if not source.is_file():
            raise RegistryError(f"Worker configuration file not found: {path}")
        try:
            raw: Any = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise RegistryError(f"Cannot read worker configuration: {exc}") from exc
        if not isinstance(raw, dict) or not isinstance(raw.get("workers", []), list):
            raise RegistryError("Worker configuration must contain a workers list")

        workers: list[Worker] = []
        for index, item in enumerate(raw.get("workers", [])):
            if not isinstance(item, dict):
                raise RegistryError(f"workers[{index}] must be a mapping")
            try:
                worker_id = str(item["id"]).strip()
                base_url = str(item["base_url"]).strip().rstrip("/")
                model = str(item["model"]).strip()
                raw_capabilities = item["capabilities"]
                if not isinstance(raw_capabilities, list):
                    raise RegistryError(f"workers[{index}].capabilities must be a list")
                if not all(isinstance(value, str) for value in raw_capabilities):
                    raise RegistryError(f"workers[{index}].capabilities must contain strings")
                capabilities = frozenset(value.strip() for value in raw_capabilities)
                enabled = item.get("enabled", False)
                priority = int(item.get("priority", 100))
                max_concurrency = int(item.get("max_concurrency", 1))
                timeout = float(item.get("timeout_seconds", 180))
                api_key_env = item.get("api_key_env")
            except (KeyError, TypeError, ValueError) as exc:
                raise RegistryError(f"Invalid workers[{index}] entry: {exc}") from exc

            if not worker_id or not base_url or not model or not capabilities or "" in capabilities:
                raise RegistryError(f"workers[{index}] requires id, base_url, model and capabilities")
            if not isinstance(enabled, bool):
                raise RegistryError(f"workers[{index}].enabled must be boolean")
            if priority < 0 or max_concurrency < 1 or timeout <= 0:
                raise RegistryError(f"workers[{index}] has invalid limits")
            if api_key_env is not None and not isinstance(api_key_env, str):
                raise RegistryError(f"workers[{index}].api_key_env must be a string")
            if api_key_env and not os.getenv(api_key_env):
                raise RegistryError(
                    f"workers[{index}] references unset or empty secret environment variable "
                    f"{api_key_env!r}"
                )
            workers.append(
                Worker(
                    id=worker_id,
                    base_url=base_url,
                    model=model,
                    capabilities=capabilities,
                    enabled=enabled,
                    priority=priority,
                    api_key=(
                        os.getenv(api_key_env) or None
                        if api_key_env else None
                    ),
                    timeout_seconds=timeout,
                    max_concurrency=max_concurrency,
                )
            )
        return cls(workers)

    def enabled_workers(self) -> tuple[Worker, ...]:
        return tuple(worker for worker in self._workers if worker.enabled)

    def describe(self) -> list[dict[str, object]]:
        return [
            {
                "id": worker.id,
                "model": worker.model,
                "capabilities": sorted(worker.capabilities),
                "enabled": worker.enabled,
                "priority": worker.priority,
                "max_concurrency": worker.max_concurrency,
            }
            for worker in self._workers
        ]
