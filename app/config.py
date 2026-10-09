"""Application configuration.

All settings can be overridden with environment variables or a ``.env`` file.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Bulk Certificate Generator"
    app_version: str = "2.0.0"
    log_level: str = "INFO"

    # SQLAlchemy database URL. SQLite by default.
    database_url: str = "sqlite:///./data/certificates.db"

    # Directory where generated certificate PDFs are stored.
    storage_dir: str = "./storage"

    # Number of threads used to process generation jobs.
    max_workers: int = 4

    # Hard cap on recipients accepted in a single request.
    max_recipients_per_job: int = 5000

    # "thread" runs generation in a background thread pool; "inline" performs
    # generation during the request (used by the test-suite / small setups).
    generation_mode: str = "thread"

    # Automatic retries for *transient/unexpected* generation errors. Validation
    # failures (certificate.py) are never retried.
    max_attempts: int = 3
    retry_backoff_seconds: float = 0.2

    # Artificial per-recipient delay. Useful for demos and for exercising
    # cancellation deterministically; keep at 0 in production.
    generation_delay_ms: int = 0

    # On startup, re-dispatch jobs left in PENDING / PROCESSING (crash recovery).
    recover_incomplete_jobs: bool = True

    # Base URL used to build the public verification link embedded in PDFs.
    public_base_url: str = "http://localhost:8000"

    @property
    def storage_path(self) -> Path:
        return Path(self.storage_dir).resolve()

    @property
    def verification_base_url(self) -> str:
        return self.public_base_url.rstrip("/") + "/api/v1/verify"


@lru_cache
def get_settings() -> Settings:
    return Settings()
