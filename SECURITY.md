# Security Model — Medibox

## Regulatory Framework

Medibox operates under **Tunisian Loi n°2004-63** (personal data protection). HIPAA does not apply.

---

## Authentication and Authorization

### User Authentication (Firebase JWT)

All API endpoints except `/v1/healthz` and `/v1/readyz` require a valid Firebase JWT:

```
Authorization: Bearer <firebase_id_token>
```

The API verifies the token using Firebase Admin SDK via Application Default Credentials (ADC) in Cloud Run. Token expiry, signature, and project ID are all verified.

### Admin Authorization (Dual Factor — DD-013)

Admin endpoints (`/v1/admin/**`) require **both**:

1. `admin: true` claim in the verified Firebase JWT
2. A non-revoked row in the `admin_role_grants` Postgres table (`revoked_at IS NULL`)

This prevents a compromised Firebase console from granting admin access without a corresponding database grant. All admin actions are written to the append-only `audit_log` table.

### Edge Device Authentication (WIF — DD-010)

Raspberry Pi edge devices authenticate using:

1. **Primary:** Workload Identity Federation (OIDC) — no long-lived keys
2. **Fallback:** JSON service account key (if WIF unavailable on the device network)

The edge service account has `roles/run.invoker` only — it cannot call Vertex AI directly. All inference goes through the Cloud Run API → Celery worker → Vertex AI.

---

## Encryption

### Data in Transit

- TLS 1.3 enforced by default on Cloud Run
- HSTS header: `Strict-Transport-Security: max-age=63072000; includeSubDomains`
- `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff` on all responses

### Data at Rest — PII (DD-008)

Patient names and doctor names are **encrypted at rest** using Cloud KMS envelope encryption:

1. A per-job **Data Encryption Key (DEK)** is generated (AES-256-GCM via Fernet)
2. The DEK is encrypted by the **Key Encryption Key (KEK)** stored in Cloud KMS
3. Only the encrypted DEK is stored alongside the ciphertext in Postgres
4. Decryption happens only at display time (~50ms KMS API call)
5. The plaintext DEK **never persists** anywhere

Stored format: `kms::<encrypted_value_b64>::<encrypted_dek_b64>`

**PII is never logged.** Log events use `patient_name: "[REDACTED]"` or omit the field entirely.

### Secrets

All secrets are stored in **Google Secret Manager** and loaded at Cloud Run startup via the service account's ADC. They are never:
- Embedded in container images
- Stored in `.env` files in production
- Printed in logs

Secrets include: DB password, Redis AUTH string, Firebase admin JSON, JWT signing key, KMS key name.

---

## Input Validation

| Check | Location |
|-------|----------|
| Device ID format (`pi-XXXX`) | EdgePayload Pydantic schema |
| Session ID (UUID v4) | EdgePayload schema |
| Timestamp clock skew (±5 min) | EdgePayload schema |
| Max 30 crops per request | EdgePayload schema |
| Max 2MB per crop, 20MB combined | EdgePayload + multipart handler |
| JPEG/PNG magic bytes | Safety filter (worker-side) |
| Image dimensions (max 4096×4096) | Safety filter + PIL limit |
| PIL decompression bomb guard | `Image.MAX_IMAGE_PIXELS = 25_000_000` |

---

## Audit Logging

All admin actions are written to the `audit_log` Postgres table:

```sql
CREATE TABLE audit_log (
    id BIGSERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    action TEXT NOT NULL,
    resource_type TEXT,
    resource_id TEXT,
    details JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- App role has INSERT only — no UPDATE or DELETE
REVOKE UPDATE, DELETE ON audit_log FROM medibox_app;
```

Actions logged: admin login, maintenance mode toggle, model rollback, role grant/revoke.

All Cloud Run access is also logged to Cloud Audit Logs automatically.

---

## Network Security

- Cloud Run services are deployed with `--no-allow-unauthenticated` (API and Worker)
- Frontend is public (`--allow-unauthenticated`) — serves only static HTML/JS
- Cloud SQL accessible only via Cloud SQL Auth Proxy (Unix socket or TCP via VPC)
- Memorystore Redis accessible only via Serverless VPC Connector (private IP)
- Vertex AI endpoint accessible only from Cloud Run worker service account
- No public IP on Cloud SQL

---

## Responsible Disclosure

To report a security vulnerability, contact the project maintainer directly.
Do not open a public GitHub issue for security vulnerabilities.

---

## Known Security Constraints

| Constraint | Mitigation |
|------------|------------|
| Firebase console admin claim injection | `admin_role_grants` DB second factor (DD-013) |
| T4 GPU cannot scale to zero (DD-001) | Pause script + budget alerts |
| Cloud SQL connection pool ceiling (R-15) | max-instances=5, pool_size=2 |
| WIF unavailable on restricted networks | JSON key fallback (DD-010) |
| Model hallucination (clinical risk) | Disclaimer on every response, pharmacist review required |
