from dataclasses import dataclass, field
import json
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
    max_parallel_tasks: int = 3
    api_key: str | None = None
    user_api_keys: dict[str, str] = field(default_factory=dict)
    trust_user_header: bool = False
    attachment_storage_dir: str = "./data/attachments"
    max_attachment_bytes: int = 10 * 1024 * 1024
    rate_limit_requests_per_minute: int = 120
    rate_limit_window_seconds: int = 60

    def __post_init__(self) -> None:
        if not self.database_path.strip():
            raise ValueError("database_path cannot be empty")
        if not math.isfinite(self.request_timeout_seconds) or self.request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be a finite positive number")
        if self.max_parallel_tasks < 1 or self.max_parallel_tasks > 3:
            raise ValueError("max_parallel_tasks must be between 1 and 3 for the three-runtime topology")
        if not self.attachment_storage_dir.strip():
            raise ValueError("attachment_storage_dir cannot be empty")
        if self.max_attachment_bytes < 1024 or self.max_attachment_bytes > 25 * 1024 * 1024:
            raise ValueError("max_attachment_bytes must be between 1 KiB and 25 MiB")
        if self.rate_limit_requests_per_minute < 1 or self.rate_limit_requests_per_minute > 10000:
            raise ValueError("rate_limit_requests_per_minute must be between 1 and 10000")
        if self.rate_limit_window_seconds < 1 or self.rate_limit_window_seconds > 3600:
            raise ValueError("rate_limit_window_seconds must be between 1 and 3600")
        if self.api_key is not None and (
            len(self.api_key) < 32 or not self.api_key.isascii() or not self.api_key.strip()
            or any(character.isspace() for character in self.api_key)
        ):
            raise ValueError("api_key must be at least 32 ASCII characters")
        if not isinstance(self.user_api_keys, dict):
            raise ValueError("user_api_keys must be a mapping of user IDs to tokens")
        if any(
            not isinstance(user_id, str)
            or not user_id.strip()
            or len(user_id) > 128
            or not isinstance(token, str)
            or len(token) < 32
            or not token.isascii()
            or any(character.isspace() for character in token)
            for user_id, token in self.user_api_keys.items()
        ):
            raise ValueError("user_api_keys require non-empty IDs and tokens of at least 32 ASCII characters")
        configured_tokens = list(self.user_api_keys.values())
        if len(set(configured_tokens)) != len(configured_tokens):
            raise ValueError("user_api_keys must not reuse the same token for multiple users")
        if self.api_key is not None and self.api_key in configured_tokens:
            raise ValueError("api_key must be different from every user API token")

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        raw_user_keys = os.getenv("CLICODING_USER_API_KEYS", "").strip()
        try:
            user_api_keys = json.loads(raw_user_keys) if raw_user_keys else {}
        except json.JSONDecodeError as exc:
            raise ValueError("CLICODING_USER_API_KEYS must be a JSON object") from exc
        if not isinstance(user_api_keys, dict):
            raise ValueError("CLICODING_USER_API_KEYS must be a JSON object")
        trust_user_header = os.getenv("CLICODING_TRUST_USER_HEADER", "").strip().lower() in {
            "1", "true", "yes", "on"
        }
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
            max_parallel_tasks=int(os.getenv("CLICODING_MAX_PARALLEL_TASKS", "3")),
            api_key=os.getenv("CLICODING_API_KEY") or None,
            user_api_keys=user_api_keys,
            trust_user_header=trust_user_header,
            attachment_storage_dir=os.getenv(
                "CLICODING_ATTACHMENT_STORAGE_DIR", "./data/attachments"
            ),
            max_attachment_bytes=int(
                os.getenv("CLICODING_MAX_ATTACHMENT_BYTES", str(10 * 1024 * 1024))
            ),
            rate_limit_requests_per_minute=int(
                os.getenv("CLICODING_RATE_LIMIT_PER_WINDOW", "120")
            ),
            rate_limit_window_seconds=int(
                os.getenv("CLICODING_RATE_LIMIT_WINDOW_SECONDS", "60")
            ),
        )
