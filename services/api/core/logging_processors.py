"""
Structlog processors for security and observability.

PII_SCRUB_PROCESSOR must be the first processor in the chain so that
sensitive field names are redacted before any formatter or exporter sees them.
"""

from __future__ import annotations

from typing import Any

# Fields that must never appear in logs, traces, or error reports.
_PII_FIELD_NAMES: frozenset[str] = frozenset({
    "patient_name", "doctor_name", "name", "last_name", "address",
    "phone", "email", "national_id", "profession",
    # credential fields
    "token", "authorization", "password", "secret", "key",
    "api_key", "access_key", "private_key", "pii_encryption_key",
    "firebase_admin_json", "redis_url", "database_url",
    # internal encrypted values
    "encrypted", "ciphertext",
})


def scrub_pii_processor(
    logger: Any,
    method: str,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    """
    Replace the values of sensitive keys with [REDACTED].
    Applied to every log event — must be fast (dict key iteration only).
    Does not recurse into nested dicts to keep latency predictable;
    structured PII (patient.name) is encrypted before it reaches the logger.
    """
    for key in list(event_dict.keys()):
        if key.lower() in _PII_FIELD_NAMES:
            event_dict[key] = "[REDACTED]"
    return event_dict
