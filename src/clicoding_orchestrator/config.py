from dataclasses import dataclass
import math
import os

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    database_path: str = "./data/clicoding.db"
    upstream_base_url: str = "http://127.0.0.1:8000/v1"
    upstream_api_key: str | None = None
    upstream_model: str = "default"
    request_timeout_seconds: float = 180.0
    workers_file: str | None = None
    enable_planning: bool = False
    api_key: str | None = None
    api_key: str | None = None

    def __post_init__(self) -> None:
        if not self.database_path.strip():
            raise ValueError("database_path cannot be empty")
        if not math.isfinite(self.request_timeout_seconds) or self.request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be a finite positive number")
        if self.api_key is not None and (
            len(self.api_key) < 32 or not self.api_key.isascii() or not self.api_key.strip()
        ):
            raise ValueError("api_key must be at least 32 ASCII characters")

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        return cls(
            database_path=os.getenv("CLICODING_DATABASE_PATH", "./data/clicoding.db"),
            upstream_base_url=os.getenv(
                "CLICODING_UPSTREAM_BASE_URL", "http://127.0.0.1:8000/v1"
            ),
            upstream_api_key=os.getenv("CLICODING_UPSTREAM_API_KEY") or None,
            upstream_model=os.getenv("CLICODING_UPSTREAM_MODEL", "default"),
            request_timeout_seconds=float(
                os.getenv("CLICODING_REQUEST_TIMEOUT_SECONDS", "180")
            ),
            workers_file=os.getenv("CLICODING_WORKERS_FILE") or None,
            enable_planning=os.getenv("CLICODING_ENABLE_PLANNING", "false").lower() in ("1", "true", "yes"),
            api_key=os.getenv("CLICODING_API_KEY") or None,
            api_key=os.getenv("CLICODING_API_KEY") or None,
        )
