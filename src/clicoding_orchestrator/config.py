from dataclasses import dataclass
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
        )
