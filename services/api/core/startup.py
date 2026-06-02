"""
Fail-fast startup validation.

Called once during lifespan startup before serving any traffic.
If any CRITICAL secret is missing or malformed, the process exits immediately.
This ensures a misconfigured deployment fails loudly at boot rather than
silently serving broken responses or exposing unencrypted PII.

CRITICAL  — missing = immediate exit (no traffic served)
WARNING   — missing = log warning, continue in degraded mode
"""

from __future__ import annotations

import os
import sys

import structlog

logger = structlog.get_logger(__name__)

_CRITICAL = [
    ("FIREBASE_ADMIN_JSON",  "Firebase Admin SDK credentials (JSON string)"),
    ("PII_ENCRYPTION_KEY",   "MultiFernet key for PII encryption"),
    ("REDIS_URL",            "Upstash Redis URL (rediss://...)"),
]

_WARNING = [
    ("CAMERA_SECRET",        "Camera relay HMAC secret — Pi cannot push frames"),
    ("VLLM_API_KEY",         "RunPod/vLLM API key — inference will fail"),
    ("VLLM_URL",             "RunPod endpoint URL — inference will fail"),
    ("R2_ACCESS_KEY_ID",     "Cloudflare R2 access key — crop uploads skipped"),
    ("R2_SECRET_KEY",        "Cloudflare R2 secret key — crop uploads skipped"),
    ("METRICS_SECRET",       "Prometheus /metrics token — endpoint disabled"),
    ("SENTRY_DSN",           "Sentry DSN — error tracking disabled"),
]


def validate_secrets() -> None:
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
                    "Generate: python -c \"from cryptography.fernet import Fernet; "
                    "print(Fernet.generate_key().decode())\""
                )

        if env_var == "REDIS_URL":
            if not (value.startswith("redis://") or value.startswith("rediss://")):
                failures.append(
                    f"  INVALID: {env_var} — must start with redis:// or rediss://"
                )

    if failures:
        msg = "\n".join(
            ["FATAL: Critical secrets missing or invalid — refusing to start:"]
            + failures
        )
        logger.critical("startup_secrets_validation_failed", failures=failures)
        sys.exit(msg)

    for env_var, description in _WARNING:
        if not os.getenv(env_var, "").strip():
            logger.warning(
                "startup_optional_secret_missing",
                secret=env_var,
                impact=description,
            )

    logger.info("startup_secrets_validation_passed", critical_count=len(_CRITICAL))
