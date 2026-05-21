"""
Settings loaded from environment variables (Cloud Run injects Secret Manager values).
Never hard-code secrets. Local dev uses .env file.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # GCP
    gcp_project_id: str = ""
    gcp_region: str = "us-central1"

    # Cloud SQL (Auth Proxy injects the Unix socket path at /cloudsql/...)
    db_user: str = "medibox"
    db_password: str = ""
    db_host: str = "127.0.0.1"
    db_port: int = 5432
    db_name: str = "medibox"

    @property
    def database_url(self) -> str:
        if self.db_host.startswith("/"):
            # Cloud SQL Auth Proxy Unix socket
            return (
                f"postgresql+asyncpg://{self.db_user}:{self.db_password}"
                f"@/{self.db_name}?host={self.db_host}"
            )
        return (
            f"postgresql+asyncpg://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @property
    def database_url_sync(self) -> str:
        if self.db_host.startswith("/"):
            return (
                f"postgresql+psycopg2://{self.db_user}:{self.db_password}"
                f"@/{self.db_name}?host={self.db_host}"
            )
        return (
            f"postgresql+psycopg2://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    # Memorystore Redis
    redis_url: str = "redis://localhost:6379/0"
    redis_auth_string: str = ""
    maintenance_redis_key: str = "medibox:maintenance"

    # Firebase
    firebase_project_id: str = ""
    firebase_credentials_path: str = ""  # path to JSON key (local dev only)

    # Vertex AI
    vertex_endpoint_id: str = ""
    vertex_endpoint_region: str = "us-central1"
    vertex_deployed_model_id: str = ""
    local_vllm_url: str = "http://localhost:8000"  # fallback for local dev

    # GCS
    gcs_raw_bucket: str = ""
    gcs_crops_bucket: str = ""
    gcs_models_bucket: str = ""

    # KMS (envelope encryption)
    kms_key_name: str = ""
    pii_dev_key: str = ""  # local dev only fallback

    # App
    environment: str = "production"
    log_level: str = "info"
    domain: str = ""

    # Celery
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    # Rate limiting
    rate_limit_per_device: int = 60     # requests per minute
    rate_limit_burst: int = 10

    # Connection pool (see RISKS.md R-15)
    db_pool_size: int = 2
    db_max_overflow: int = 1


@lru_cache
def get_settings() -> Settings:
    return Settings()
