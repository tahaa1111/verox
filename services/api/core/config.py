"""
Settings loaded from environment variables (Railway injects them).
Never hard-code secrets. Local dev uses .env file.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database (Neon PostgreSQL)
    db_user: str = "neondb_owner"
    db_password: str = ""
    db_host: str = "127.0.0.1"
    db_port: int = 5432
    db_name: str = "neondb"
    database_url_override: str = ""  # full Neon URL takes priority

    @property
    def database_url(self) -> str:
        if self.database_url_override:
            url = self.database_url_override
            if url.startswith("postgresql://"):
                url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
            url = url.replace("&channel_binding=require", "").replace("?channel_binding=require&", "?")
            return url
        return (
            f"postgresql+asyncpg://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @property
    def database_url_sync(self) -> str:
        if self.database_url_override:
            url = self.database_url_override
            if url.startswith("postgresql://"):
                url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
            elif url.startswith("postgresql+asyncpg://"):
                url = url.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)
            return url
        return (
            f"postgresql+psycopg2://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    # Redis (Upstash)
    redis_url: str = "redis://localhost:6379/0"
    redis_auth_string: str = ""
    maintenance_redis_key: str = "medibox:maintenance"

    # Firebase
    firebase_project_id: str = ""
    firebase_credentials_path: str = ""

    # vLLM (RunPod)
    vllm_url: str = "http://localhost:8000"
    vllm_api_key: str = ""
    vllm_model: str = "qwen2.5-vl-7b-instruct"

    # Cloudflare R2
    r2_endpoint: str = ""
    r2_access_key_id: str = ""
    r2_secret_key: str = ""
    r2_bucket: str = "medibox-crops"

    # PII encryption — Fernet (v1: current, prev: rotation window)
    pii_encryption_key: str = ""       # active key
    pii_encryption_key_prev: str = ""  # previous key (rotation window)

    # App
    environment: str = "production"
    log_level: str = "info"
    domain: str = "verox-five.vercel.app"

    # Celery
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    # Camera relay
    camera_secret: str = "medibox-camera-dev-secret"

    # Rate limiting
    rate_limit_per_device: int = 30
    rate_limit_per_user: int = 60
    rate_limit_per_ip: int = 200
    rate_limit_burst: int = 50       # max requests per 5-second burst window

    # Connection pool
    db_pool_size: int = 5
    db_max_overflow: int = 2

    # Metrics
    metrics_secret: str = ""          # Bearer token required to scrape /metrics


@lru_cache
def get_settings() -> Settings:
    return Settings()
