"""
PII encryption — Fernet symmetric encryption with key versioning.

Key strategy:
  PII_ENCRYPTION_KEY       — current (active) key for all NEW encryptions
  PII_ENCRYPTION_KEY_PREV  — previous key (optional) for decrypting rotated data

Encrypted format: "v1:fernet::<base64-token>"
Legacy formats:   "fernet::<token>" (pre-versioning), "dev::<token>", "kms::..." (GCP era)

Key rotation procedure:
  1. Set PII_ENCRYPTION_KEY_PREV = old PII_ENCRYPTION_KEY
  2. Generate new key: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  3. Set PII_ENCRYPTION_KEY = new key
  4. Deploy — new writes use new key, old ciphertext still decryptable via PREV key
  5. Run re-encryption job (optional — all results are re-encrypted on next read via decrypt→encrypt)

Safe startup guarantee:
  If PII_ENCRYPTION_KEY is missing, encrypt_pii() raises RuntimeError logged as CRITICAL.
  The system does NOT silently skip encryption — that would be a data exposure.
  startup.py catches this at boot and exits before serving traffic.

No deterministic leakage:
  Fernet uses AES-128-CBC + HMAC-SHA256 with a random IV per message.
  Same plaintext always produces a different ciphertext — no pattern leakage.
"""

from __future__ import annotations

import os
from functools import lru_cache

import structlog

logger = structlog.get_logger(__name__)

_CURRENT_VERSION = "v1"


@lru_cache(maxsize=1)
def _current_fernet():
    from cryptography.fernet import Fernet
    key = os.getenv("PII_ENCRYPTION_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "PII_ENCRYPTION_KEY is not set. "
            "Generate: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    return Fernet(key.encode())


@lru_cache(maxsize=1)
def _prev_fernet():
    from cryptography.fernet import Fernet
    key = os.getenv("PII_ENCRYPTION_KEY_PREV", "").strip()
    if not key:
        return None
    try:
        return Fernet(key.encode())
    except Exception:
        logger.warning("pii_prev_key_invalid")
        return None


def encrypt_pii(value: str) -> str:
    """
    Encrypt a PII string using the current active Fernet key.
    Returns versioned ciphertext: 'v1:fernet::<token>'
    Raises RuntimeError if no key is configured (startup.py prevents this at boot).
    PII values are NEVER logged.
    """
    if not value:
        return value
    f = _current_fernet()
    token = f.encrypt(value.encode()).decode()
    return f"{_CURRENT_VERSION}:fernet::{token}"


def decrypt_pii(encrypted: str) -> str:
    """
    Decrypt a PII value. Tries current key, then previous key (rotation window).
    Returns '[ENCRYPTED]' on any failure — never raises, never logs the value.

    Handles all historical formats:
      v1:fernet::<token>    — current versioned format
      fernet::<token>       — pre-versioning Fernet (same key, different prefix)
      dev::<token>          — old local-dev format (same Fernet key)
      kms::<enc>::<dek>     — GCP KMS era (cannot decrypt without GCP — returns legacy marker)
    """
    if not encrypted:
        return encrypted

    try:
        if encrypted.startswith("v1:fernet::"):
            token = encrypted[len("v1:fernet::"):]
            return _try_decrypt(token)

        if encrypted.startswith("fernet::"):
            token = encrypted[len("fernet::"):]
            return _try_decrypt(token)

        if encrypted.startswith("dev::"):
            token = encrypted[5:]
            return _try_decrypt(token)

        if encrypted.startswith("kms::"):
            return "[LEGACY_GCP_ENCRYPTED]"

    except Exception:
        pass

    return "[ENCRYPTED]"


def _try_decrypt(token: str) -> str:
    """Try current key, then previous key. Never logs token value."""
    errors = []
    for f, label in [(_current_fernet(), "current"), (_prev_fernet(), "prev")]:
        if f is None:
            continue
        try:
            return f.decrypt(token.encode()).decode()
        except Exception as exc:
            errors.append(f"{label}: {type(exc).__name__}")

    logger.warning("pii_decrypt_failed", error_count=len(errors))
    return "[ENCRYPTED]"
