"""
Fail-fast startup validation for the Celery worker.
Called before the worker starts accepting tasks.
"""

from __future__ import annotations

import os
import sys

_CRITICAL = [
    ("CELERY_BROKER_URL",    "Redis broker URL — worker cannot connect without it"),
    ("REDIS_URL",            "Redis URL — used for job status updates and cancellation"),
    ("PII_ENCRYPTION_KEY",   "Fernet key — worker encrypts patient/doctor names before DB write"),
]

_WARNING = [
    ("DATABASE_URL_SYNC",    "Postgres sync URL — job status cannot be written to DB"),
    ("VLLM_URL",             "vLLM/RunPod endpoint — inference will fail"),
    ("VLLM_API_KEY",         "RunPod API key — inference will fail"),
    ("R2_ACCESS_KEY_ID",     "R2 key — crop uploads will be skipped"),
]


def validate_worker_secrets() -> None:
    import structlog
    logger = structlog.get_logger("medibox.worker.startup")

    failures: list[str] = []

    for env_var, description in _CRITICAL:
        value = os.getenv(env_var, "").strip()
        if not value:
            failures.append(f"  MISSING: {env_var} — {description}")
            continue

        if env_var == "PII_ENCRYPTION_KEY":
            try:
                from cryptography.fernet import Fernet
                Fernet(value.encode())
            except Exception:
                failures.append(
                    f"  INVALID: {env_var} — must be a valid Fernet key. "
                    "Generate: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
                )

        if env_var in ("CELERY_BROKER_URL", "REDIS_URL"):
            if not (value.startswith("redis://") or value.startswith("rediss://")):
                failures.append(f"  INVALID: {env_var} — must start with redis:// or rediss://")

    if failures:
        msg = "\n".join(["FATAL: Worker cannot start — critical secrets missing:"] + failures)
        logger.critical("worker_startup_validation_failed", failures=failures)
        sys.exit(msg)

    for env_var, description in _WARNING:
        if not os.getenv(env_var, "").strip():
            logger.warning("worker_optional_secret_missing", secret=env_var, impact=description)

    logger.info("worker_startup_validation_passed")


if __name__ == "__main__":
    validate_worker_secrets()
