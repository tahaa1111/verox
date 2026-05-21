"""
PII encryption via Cloud KMS envelope encryption (DD-008).
In local dev (KMS_KEY_NAME empty), falls back to Fernet with PII_DEV_KEY.
"""

from __future__ import annotations

import base64
from functools import lru_cache

from services.api.core.config import get_settings

settings = get_settings()


def _kms_encrypt(plaintext: str, key_name: str) -> tuple[str, str]:
    """
    Envelope encrypt: generate a random DEK, encrypt plaintext with DEK,
    encrypt DEK with KMS KEK. Returns (encrypted_value_b64, encrypted_dek_b64).
    """
    from google.cloud import kms
    from cryptography.fernet import Fernet

    dek = Fernet.generate_key()
    f = Fernet(dek)
    ciphertext = f.encrypt(plaintext.encode())

    kms_client = kms.KeyManagementServiceClient()
    response = kms_client.encrypt(
        request={"name": key_name, "plaintext": dek}
    )
    encrypted_dek = base64.b64encode(response.ciphertext).decode()
    encrypted_value = base64.b64encode(ciphertext).decode()
    return encrypted_value, encrypted_dek


def _kms_decrypt(encrypted_value: str, encrypted_dek: str, key_name: str) -> str:
    from google.cloud import kms
    from cryptography.fernet import Fernet

    kms_client = kms.KeyManagementServiceClient()
    dek_ciphertext = base64.b64decode(encrypted_dek)
    response = kms_client.decrypt(
        request={"name": key_name, "ciphertext": dek_ciphertext}
    )
    dek = response.plaintext
    f = Fernet(dek)
    return f.decrypt(base64.b64decode(encrypted_value)).decode()


@lru_cache(maxsize=1)
def _dev_fernet():
    from cryptography.fernet import Fernet
    key = settings.pii_dev_key
    if not key:
        key = Fernet.generate_key().decode()
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_pii(value: str) -> str:
    """
    Returns a JSON-safe string. In production: 'kms::<enc_value>::<enc_dek>'.
    In local dev: 'dev::<fernet_token>'.
    """
    if not value:
        return value
    if settings.kms_key_name:
        enc_val, enc_dek = _kms_encrypt(value, settings.kms_key_name)
        return f"kms::{enc_val}::{enc_dek}"
    token = _dev_fernet().encrypt(value.encode()).decode()
    return f"dev::{token}"


def decrypt_pii(encrypted: str) -> str:
    """Decrypt a value produced by encrypt_pii. Returns '[ENCRYPTED]' on failure."""
    if not encrypted:
        return encrypted
    try:
        if encrypted.startswith("kms::"):
            _, enc_val, enc_dek = encrypted.split("::", 2)
            return _kms_decrypt(enc_val, enc_dek, settings.kms_key_name)
        if encrypted.startswith("dev::"):
            token = encrypted[5:]
            return _dev_fernet().decrypt(token.encode()).decode()
    except Exception:
        pass
    return "[ENCRYPTED]"
