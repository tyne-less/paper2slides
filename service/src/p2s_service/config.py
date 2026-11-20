from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="P2S_")

    database_url: str = "postgresql+psycopg://p2s:p2s@localhost:5432/p2s"

    # local: filesystem (tests); s3: S3-compatible store (local compose); postgres: the
    # stored_objects table (free-tier cloud deployment without an object store).
    storage_backend: Literal["local", "s3", "postgres"] = "local"
    storage_local_root: Path = Path("./.p2s-storage")
    # S3-compatible storage (MinIO locally). Credentials come from the standard
    # AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY environment variables read by boto3.
    s3_endpoint_url: str | None = None
    s3_bucket: str = "p2s"
    s3_region: str = "us-east-1"
    s3_create_bucket: bool = False

    max_upload_bytes: int = 20 * 1024 * 1024
    max_pages: int = 100
    max_attempts: int = 3

    # A worker renews its lease every lease_seconds / 3 while a job runs.
    lease_seconds: float = 60
    poll_interval_seconds: float = 1.0
    reap_interval_seconds: float = 10
    job_timeout_seconds: float = 300
    # Time a job may keep running after SIGTERM before it is released back to the queue.
    # Cloud Run sends SIGKILL 10 s after SIGTERM.
    shutdown_grace_seconds: float = 8
    retry_base_seconds: float = 2
    retry_max_seconds: float = 300

    # Port for the worker's own Prometheus endpoint; 0 disables it.
    worker_metrics_port: int = 9100

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
