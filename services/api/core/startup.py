"""
Fail-fast startup validation.

Called once during lifespan startup. If any CRITICAL secret is missing or
malformed the process exits immediately with a clear error — preventing a
partially-configured service from serving traffic with silent failures.

Secret categories:
  CRITICAL  — missing = immediate startup failure (no traffic served)
  WARNING   — missing = log warning, continue (degraded mode documented)
"""

from __future__ import annotations

import os
import sys

import structlog

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Secret definitions
# ---------------------------------------------------------------------------

_CRITICAL = [
    ("FIREBASE_ADMIN_JSON",  "Firebase Admin SDK credentials (JSON string)"),
    ("PII_ENCRYPTION_KEY",   "Fernet key for PII encryption — generate with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""),
    ("REDIS_URL",            "Upstash Redis URL (rediss://...)"),
    ("CELERY_BROKER_URL",    "Celery broker URL (same as Redis)"),
]

_WARNING = [
    ("CAMERA_SECRET",        "Camera relay HMAC secret — Pi cannot push frames without it"),
    ("VLLM_API_KEY",         "RunPod/vLLM API key — inference will fail if missing"),
    ("R2_ACCESS_KEY_ID",     "Cloudflare R2 access key — crop uploads will be skipped"),
    ("R2_SECRET_KEY",        "Cloudflare R2 secret key — crop uploads will be skipped"),
    ("METRICS_SECRET",       "Prometheus /metrics bearer token — endpoint will be disabled"),
]


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _validate_fernet_key(key: str, name: str) -> None:
    """Verify the key is a valid Fernet key (32-byte base64url). Fails fast."""
    try:
        from cryptography.fernet import Fernet, InvalidToken
        Fernet(key.encode() if isinstance(key, str) else key)
    except Exception as exc:
        logger.critical(
            "startup_invalid_fernet_key",
            secret=name,
            error=str(exc),
        )
        sys.exit(f"FATAL: {name} is not a valid Fernet key. "
                 "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"")


def _validate_redis_url(url: str, name: str) -> None:
    if not (url.startswith("redis://") or url.startswith("rediss://")):
        logger.critical("startup_invalid_redis_url", secret=name, url_prefix=url[:20])
        sys.exit(f"FATAL: {name} must start with redis:// or rediss://")


# ---------------------------------------------------------------------------
# Main validation entry point
# ---------------------------------------------------------------------------

def validate_secrets() -> None:
    """
    Validate all critical secrets at startup.
    Logs all failures before exiting so operators see the full list at once.
    """
    failures: list[str] = []

    # Check CRITICAL secrets — all must be present
    for env_var, description in _CRITICAL:
        value = os.getenv(env_var, "").strip()
        if not value:
            failures.append(f"  MISSING: {env_var} — {description}")
            continue

        # Format-specific validation
        if env_var == "PII_ENCRYPTION_KEY":
            try:
                from cryptography.fernet import Fernet
                Fernet(value.encode())
            except Exception:
                failures.append(
                    f"  INVALID: {env_var} — must be a valid Fernet key "
                    "(32-byte base64url). Run: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
                )

        if env_var in ("REDIS_URL", "CELERY_BROKER_URL"):
            if not (value.startswith("redis://") or value.startswith("rediss://")):
                failures.append(f"  INVALID: {env_var} — must start with redis:// or rediss://")

    if failures:
        msg = "\n".join(["FATAL: Critical secrets missing or invalid at startup:"] + failures)
        logger.critical("startup_secrets_validation_failed", failures=failures)
        sys.exit(msg)

    # Check WARNING secrets — log but continue
    for env_var, description in _WARNING:
        value = os.getenv(env_var, "").strip()
        if not value:
            logger.warning(
                "startup_optional_secret_missing",
                secret=env_var,
                impact=description,
            )

    logger.info("startup_secrets_validation_passed", critical_count=len(_CRITICAL))
