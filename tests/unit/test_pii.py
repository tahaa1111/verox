"""Unit tests for PII encryption/decryption — v2:multi:: MultiFernet format."""

import pytest
from cryptography.fernet import Fernet


@pytest.fixture(autouse=True)
def pii_env(monkeypatch):
    """Set PII_ENCRYPTION_KEY env var and clear the LRU cache before/after."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("PII_ENCRYPTION_KEY", key)
    monkeypatch.delenv("PII_ENCRYPTION_KEY_PREV", raising=False)

    import services.api.core.pii as pii_module
    pii_module._multi_fernet.cache_clear()
    yield
    pii_module._multi_fernet.cache_clear()


class TestPiiEncryptDecrypt:
    def test_encrypt_returns_versioned_prefix(self):
        from services.api.core.pii import encrypt_pii
        enc = encrypt_pii("Ahmed Ben Ali")
        assert enc.startswith("v2:multi::")

    def test_decrypt_roundtrip(self):
        from services.api.core.pii import encrypt_pii, decrypt_pii
        plaintext = "Dr. Fatma Chaabane"
        enc = encrypt_pii(plaintext)
        dec = decrypt_pii(enc)
        assert dec == plaintext

    def test_decrypt_invalid_token_returns_placeholder(self):
        from services.api.core.pii import decrypt_pii
        result = decrypt_pii("v2:multi::invalid_token_base64")
        assert result == "[ENCRYPTED]"

    def test_decrypt_none_returns_none(self):
        from services.api.core.pii import decrypt_pii
        result = decrypt_pii(None)
        assert result is None

    def test_encrypt_empty_string_returns_empty(self):
        from services.api.core.pii import encrypt_pii
        assert encrypt_pii("") == ""

    def test_plaintext_not_in_encrypted_value(self):
        from services.api.core.pii import encrypt_pii
        name = "Ahmed Ben Ali"
        enc = encrypt_pii(name)
        assert name not in enc

    def test_nondeterministic_encryption(self):
        """Same plaintext must produce different ciphertext each call (random IV)."""
        from services.api.core.pii import encrypt_pii
        enc1 = encrypt_pii("Ahmed Ben Ali")
        enc2 = encrypt_pii("Ahmed Ben Ali")
        assert enc1 != enc2

    def test_decrypt_legacy_kms_format_returns_legacy_marker(self):
        from services.api.core.pii import decrypt_pii
        fake_kms = "kms::AQICAHgfake_encrypted_value==::AQICAHgfake_dek=="
        result = decrypt_pii(fake_kms)
        assert result == "[LEGACY_GCP_ENCRYPTED]"

    def test_decrypt_legacy_fernet_prefix(self, monkeypatch):
        """Legacy 'fernet::<token>' format (pre-versioning) still decrypts."""
        import services.api.core.pii as pii_module
        key = Fernet.generate_key()
        monkeypatch.setenv("PII_ENCRYPTION_KEY", key.decode())
        pii_module._multi_fernet.cache_clear()

        # Build ciphertext manually using raw Fernet, wrap with old prefix
        token = Fernet(key).encrypt(b"Fatma Chaabane").decode()
        legacy = f"fernet::{token}"

        from services.api.core.pii import decrypt_pii
        assert decrypt_pii(legacy) == "Fatma Chaabane"

    def test_key_rotation_prev_key_decrypts_old_ciphertext(self, monkeypatch):
        """After rotation, data encrypted with old key still decrypts via PREV."""
        import services.api.core.pii as pii_module

        old_key = Fernet.generate_key().decode()
        new_key = Fernet.generate_key().decode()

        # Encrypt with OLD key
        monkeypatch.setenv("PII_ENCRYPTION_KEY", old_key)
        monkeypatch.delenv("PII_ENCRYPTION_KEY_PREV", raising=False)
        pii_module._multi_fernet.cache_clear()
        from services.api.core.pii import encrypt_pii
        old_ciphertext = encrypt_pii("Patient Name")

        # Rotate: old → PREV, new → current
        monkeypatch.setenv("PII_ENCRYPTION_KEY", new_key)
        monkeypatch.setenv("PII_ENCRYPTION_KEY_PREV", old_key)
        pii_module._multi_fernet.cache_clear()

        from services.api.core.pii import decrypt_pii
        assert decrypt_pii(old_ciphertext) == "Patient Name"
