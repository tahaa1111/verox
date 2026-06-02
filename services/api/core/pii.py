"""
PII encryption — MultiFernet with key versioning and rotation support.

Key strategy:
  PII_ENCRYPTION_KEY        — current (active) key; all NEW writes use this
  PII_ENCRYPTION_KEY_PREV   — previous key retained for decryption during rotation

Encryption format: "v2:multi::<base64-fernet-token>"
Legacy read support: "v1:fernet::", "fernet::", "dev::" (same key path)
GCP-era: "kms::" — cannot decrypt without GCP KMS; returns [LEGACY_GCP_ENCRYPTED]

MultiFernet behaviour:
  encrypt() always uses the FIRST (newest) key.
  decrypt() tries all keys in order — old ciphertexts remain readable after rotation.

Key rotation procedure:
  1. Set PII_ENCRYPTION_KEY_PREV = current PII_ENCRYPTION_KEY
  2. Generate new key:
       python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  3. Set PII_ENCRYPTION_KEY = new key
  4. Deploy — new writes use new key; old ciphertexts still decrypt via PREV key
  5. (Optional) run re-encryption task to migrate all v1 ciphertexts to v2

No deterministic leakage: Fernet uses AES-128-CBC + HMAC-SHA256 with a random
IV per message. Identical plaintexts always produce distinct ciphertexts.

PII values are NEVER logged — encrypt_pii() and decrypt_pii() must never
emit the plaintext in any log, trace, or exception message.
"""

from __future__ import annotations

import os
from functools import lru_cache

import structlog

logger = structlog.get_logger(__name__)


@lru_cache(maxsize=1)
def _multi_fernet():
    from cryptography.fernet import Fernet, MultiFernet

    keys = []
    current = os.getenv("PII_ENCRYPTION_KEY", "").strip()
    if current:
        try:
            keys.append(Fernet(current.encode()))
        except Exception as exc:
            raise RuntimeError(
                f"PII_ENCRYPTION_KEY is not a valid Fernet key: {exc}"
            ) from exc

    prev = os.getenv("PII_ENCRYPTION_KEY_PREV", "").strip()
    if prev:
        try:
            keys.append(Fernet(prev.encode()))
        except Exception:
            logger.warning("pii_prev_key_invalid")

    if not keys:
        raise RuntimeError(
            "PII_ENCRYPTION_KEY is not set. "
            "Generate: python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\""
        )

    return MultiFernet(keys)


def encrypt_pii(value: str) -> str:
    """
    Encrypt a PII string with MultiFernet (always uses the newest active key).
    Returns versioned ciphertext: 'v2:multi::<base64-token>'
    Raises RuntimeError if no key is configured (startup.py prevents this at boot).
    The plaintext value is NEVER logged.
    """
    if not value:
        return value
    token = _multi_fernet().encrypt(value.encode()).decode()
    return f"v2:multi::{token}"


def decrypt_pii(encrypted: str) -> str:
    """
    Decrypt a PII value. Tries all configured keys (current + prev).
    Returns '[ENCRYPTED]' on any failure — never raises, never logs the value.

    Handles all historical formats:
      v2:multi::<token>   — current MultiFernet format
      v1:fernet::<token>  — previous single-key Fernet format
      fernet::<token>     — pre-versioning Fernet
      dev::<token>        — old local-dev Fernet
      kms::<enc>::<dek>   — GCP KMS era (unrecoverable without GCP)
    """
    if not encrypted:
        return encrypted

    try:
        mf = _multi_fernet()

        if encrypted.startswith("v2:multi::"):
            return mf.decrypt(encrypted[len("v2:multi::"):].encode()).decode()

        if encrypted.startswith("v1:fernet::"):
            return mf.decrypt(encrypted[len("v1:fernet::"):].encode()).decode()

        if encrypted.startswith("fernet::"):
            return mf.decrypt(encrypted[len("fernet::"):].encode()).decode()

        if encrypted.startswith("dev::"):
            return mf.decrypt(encrypted[5:].encode()).decode()

        if encrypted.startswith("kms::"):
            return "[LEGACY_GCP_ENCRYPTED]"

    except Exception:
        pass

    return "[ENCRYPTED]"
