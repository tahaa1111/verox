"""
Settings loaded from environment variables.
Railway / Render inject these as env vars. Local dev uses .env file.
Never hard-code secrets. Validated at startup by startup.py.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── Database (Neon PostgreSQL) ────────────────────────────────────────────
    db_user: str = "neondb_owner"
    db_password: str = ""
    db_host: str = "127.0.0.1"
    db_port: int = 5432
    db_name: str = "neondb"
    database_url_override: str = ""  # full Neon URL takes priority if set

    @property
    def database_url(self) -> str:
        """asyncpg URL with sslmode stripped (passed via connect_args)."""
        import re
        url = self.database_url_override or (
            f"postgresql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        url = re.sub(r"[?&]sslmode=[^&]*", "", url)
        url = re.sub(r"[?&]channel_binding=[^&]*", "", url)
        url = re.sub(r"\?&", "?", url).rstrip("?")
        return url

    @property
    def database_url_sync(self) -> str:
        """psycopg2 URL for Alembic migrations (CI only)."""
        url = self.database_url_override or (
            f"postgresql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )
        if "asyncpg" in url:
            url = url.replace("+asyncpg", "+psycopg2")
        elif url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
        return url

    # ── Redis (Upstash) ───────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"
    redis_auth_string: str = ""
    maintenance_redis_key: str = "medibox:maintenance"

    # ── Firebase ─────────────────────────────────────────────────────────────
    firebase_project_id: str = ""
    firebase_credentials_path: str = ""

    # ── vLLM / RunPod ────────────────────────────────────────────────────────
    vllm_url: str = "http://localhost:8000"
    vllm_api_key: str = ""
    vllm_model: str = "qwen2.5-vl-7b-instruct"

    # ── Cloudflare R2 ────────────────────────────────────────────────────────
    r2_endpoint: str = ""
    r2_access_key_id: str = ""
    r2_secret_key: str = ""
    r2_bucket: str = "medibox-crops"

    # ── PII encryption (MultiFernet) ─────────────────────────────────────────
    pii_encryption_key: str = ""        # current active key
    pii_encryption_key_prev: str = ""   # previous key — retained for decryption

    # ── App ──────────────────────────────────────────────────────────────────
    environment: str = "production"
    log_level: str = "info"
    domain: str = "verox-five.vercel.app"

    # ── arq worker ───────────────────────────────────────────────────────────
    arq_max_jobs: int = 1               # one GPU job at a time
    arq_job_timeout: int = 180          # seconds
    arq_health_check_interval: int = 30 # seconds — conserves Upstash free tier
    # poll_delay: how often the worker checks the queue for new jobs.
    # Default arq value is 0.5s (ZRANGEBYSCORE every 0.5s = 5.18M cmds/month on
    # Upstash free 500K tier). 2.0s = 1.3M cmds/month — still over budget but
    # far better; combine with Render spin-down so it only runs during clinic hours.
    arq_poll_delay: float = 2.0

    # ── Camera relay ─────────────────────────────────────────────────────────
    camera_secret: str = ""  # required — set CAMERA_SECRET env var

    # ── Rate limiting ────────────────────────────────────────────────────────
    rate_limit_per_device: int = 30
    rate_limit_per_user: int = 60
    rate_limit_per_ip: int = 200
    rate_limit_burst: int = 50

    # ── DB connection pool ───────────────────────────────────────────────────
    db_pool_size: int = 5
    db_max_overflow: int = 2

    # ── Observability ────────────────────────────────────────────────────────
    # metrics_secret kept for config-compat but the scrape endpoint is removed.
    metrics_secret: str = ""
    sentry_dsn: str = ""                # Sentry project DSN (kept for error tracking)

    # Grafana Cloud OTLP push (traces + metrics).
    # Endpoint format: https://otlp-gateway-prod-<region>.grafana.net/otlp
    # Token needs scopes: traces:write  metrics:write
    grafana_cloud_otlp_endpoint: str = ""
    grafana_cloud_instance_id: str = ""   # numeric stack / instance ID
    grafana_cloud_token: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
