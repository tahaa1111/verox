"""Unit tests for PII encryption/decryption (dev path only)."""

import pytest
from unittest.mock import MagicMock, patch
from cryptography.fernet import Fernet


@pytest.fixture(autouse=True)
def dev_settings(monkeypatch):
    """
    Patch services.api.core.pii.settings so that:
    - kms_key_name is empty → dev path is taken
    - pii_dev_key holds a fresh Fernet key
    Also clear the _dev_fernet LRU cache so the new key is picked up.
    """
    key = Fernet.generate_key().decode()
    mock_settings = MagicMock()
    mock_settings.kms_key_name = ""
    mock_settings.pii_dev_key = key

    import services.api.core.pii as pii_module

    with patch.object(pii_module, "settings", mock_settings):
        pii_module._dev_fernet.cache_clear()
        yield
        pii_module._dev_fernet.cache_clear()


class TestPiiEncryptDecrypt:
    def test_encrypt_returns_dev_prefix(self):
        from services.api.core.pii import encrypt_pii
        enc = encrypt_pii("Ahmed Ben Ali")
        assert enc.startswith("dev::")

    def test_decrypt_roundtrip(self):
        from services.api.core.pii import encrypt_pii, decrypt_pii
        plaintext = "Dr. Fatma Chaabane"
        enc = encrypt_pii(plaintext)
        dec = decrypt_pii(enc)
        assert dec == plaintext

    def test_decrypt_invalid_returns_placeholder(self):
        from services.api.core.pii import decrypt_pii
        result = decrypt_pii("dev::invalid_token")
        assert result == "[ENCRYPTED]"

    def test_decrypt_none_returns_none(self):
        from services.api.core.pii import decrypt_pii
        result = decrypt_pii(None)
        assert result is None

    def test_encrypt_empty_string(self):
        from services.api.core.pii import encrypt_pii
        enc = encrypt_pii("")
        # encrypt_pii returns the value unchanged when empty
        assert enc == ""

    def test_plaintext_not_in_encrypted_value(self):
        from services.api.core.pii import encrypt_pii
        name = "Ahmed Ben Ali"
        enc = encrypt_pii(name)
        assert name not in enc

    def test_decrypt_kms_format_returns_placeholder_when_no_kms(self):
        from services.api.core.pii import decrypt_pii
        # KMS format without real KMS configured should return [ENCRYPTED] safely
        fake_kms = "kms::AQICAHgfake_encrypted_value==::AQICAHgfake_dek=="
        result = decrypt_pii(fake_kms)
        assert result == "[ENCRYPTED]"
