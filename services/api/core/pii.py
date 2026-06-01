"""
PII encryption using Fernet symmetric encryption.
Key is loaded from PII_ENCRYPTION_KEY env var (base64-url-safe 32-byte key).
Generate a key: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""

from __future__ import annotations

from functools import lru_cache

from services.api.core.config import get_settings

settings = get_settings()


@lru_cache(maxsize=1)
def _fernet():
    from cryptography.fernet import Fernet
    key = settings.pii_encryption_key
    if not key:
        raise RuntimeError("PII_ENCRYPTION_KEY env var is not set")
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_pii(value: str) -> str:
    """Encrypt a PII string. Returns 'fernet::<token>'."""
    if not value:
        return value
    token = _fernet().encrypt(value.encode()).decode()
    return f"fernet::{token}"


def decrypt_pii(encrypted: str) -> str:
    """Decrypt a value produced by encrypt_pii. Returns '[ENCRYPTED]' on failure."""
    if not encrypted:
        return encrypted
    try:
        if encrypted.startswith("fernet::"):
            return _fernet().decrypt(encrypted[8:].encode()).decode()
        # Legacy format from old KMS/dev encryption — return as-is, cannot decrypt
        if encrypted.startswith(("kms::", "dev::")):
            return "[LEGACY_ENCRYPTED]"
    except Exception:
        pass
    return "[ENCRYPTED]"
