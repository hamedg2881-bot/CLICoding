from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


class WorkerRegistry:
    """Configuration-backed registry. Loading it never probes or starts runtimes."""

    def __init__(self, workers: list[Worker]):
        ids = [worker.id for worker in workers]
        if len(ids) != len(set(ids)):
            raise RegistryError("Worker IDs must be unique")
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
                capabilities = frozenset(str(value).strip() for value in item["capabilities"])
                enabled = item.get("enabled", False)
                priority = int(item.get("priority", 100))
                max_concurrency = int(item.get("max_concurrency", 1))
                timeout = float(item.get("timeout_seconds", 180))
                api_key = item.get("api_key")
            except (KeyError, TypeError, ValueError) as exc:
                raise RegistryError(f"Invalid workers[{index}] entry: {exc}") from exc

            if not worker_id or not base_url or not model or not capabilities:
                raise RegistryError(f"workers[{index}] requires id, base_url, model and capabilities")
            if not isinstance(enabled, bool):
                raise RegistryError(f"workers[{index}].enabled must be boolean")
            if priority < 0 or max_concurrency < 1 or timeout <= 0:
                raise RegistryError(f"workers[{index}] has invalid limits")
            workers.append(
                Worker(
                    id=worker_id,
                    base_url=base_url,
                    model=model,
                    capabilities=capabilities,
                    enabled=enabled,
                    priority=priority,
                    api_key=str(api_key) if api_key else None,
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
