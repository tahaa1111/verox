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

The API verifies the token using Firebase Admin SDK. Token expiry, signature, and project ID are all verified on every request.

### Admin Authorization (Dual Factor)

Admin endpoints (`/v1/admin/**`) require **both**:

1. `admin: true` custom claim in the verified Firebase JWT
2. A non-revoked row in the `admin_role_grants` Postgres table (`revoked_at IS NULL`)

This ensures a compromised Firebase console alone cannot grant admin access. All admin actions are written to the append-only `audit_log` table.

### Edge Device Authentication

Pi edge devices authenticate using a **shared camera secret** (UUID) stored in:
- Pi: `/etc/medibox/edge.toml` or `CAMERA_SECRET` env var (systemd EnvironmentFile)
- API: `CAMERA_SECRET` Railway environment variable

The secret is used to authenticate WebSocket connections from the Pi to the camera relay endpoint.

---

## Encryption

### Data in Transit

- TLS enforced end-to-end (Railway + Vercel + RunPod all terminate TLS)
- `HSTS`, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff` on all API responses

### Data at Rest — PII

Patient names and doctor names are **encrypted at rest** using **MultiFernet** (Fernet symmetric encryption):

- Encryption key stored in Railway environment variable `PII_ENCRYPTION_KEY` (never in code)
- `PII_ENCRYPTION_KEY_PREV` supported for zero-downtime key rotation
- Stored format: `v2:multi::<fernet_token>`
- Decryption only at display time; plaintext never persists in logs or DB columns
- **PII is never logged** — all log events omit or redact patient/doctor names

### Secrets Management

All secrets are stored in Railway environment variables. Never:
- Hardcoded in source code
- Committed to version control
- Printed in logs

| Secret | Where |
|--------|-------|
| `DATABASE_URL` | Railway |
| `REDIS_URL` | Railway (Upstash) |
| `PII_ENCRYPTION_KEY` | Railway |
| `CAMERA_SECRET` | Railway + Pi `/etc/medibox/edge.toml` |
| `VLLM_URL` / `VLLM_API_KEY` | Railway |
| Firebase Admin JSON | Railway |

---

## Input Validation

| Check | Location |
|-------|----------|
| Device ID format | EdgePayload Pydantic schema |
| Session ID (UUID v4) | EdgePayload schema |
| Max 9 crops per request | Enforced in submit router |
| JPEG magic bytes + aspect ratio | Safety filter (worker-side) |
| Black / blank image detection | Safety filter — returns error, not processed |
| Image dimensions | PIL decompression bomb guard (`MAX_IMAGE_PIXELS`) |

---

## Audit Logging

All admin actions are written to the append-only `audit_log` Postgres table. The application role has `INSERT` only — `UPDATE` and `DELETE` are revoked.

Actions logged: admin login, maintenance mode toggle, model rollback, role grant/revoke.

---

## Network Security

- Railway API: public HTTPS, all endpoints require Firebase JWT (except `/healthz`)
- RunPod: accessible only via `VLLM_API_KEY` bearer token, not public
- Neon Postgres: connection string with credentials in Railway env only
- Upstash Redis: TLS-only (`rediss://`), password in Railway env only
- Pi → Railway: WebSocket over TLS, authenticated with `CAMERA_SECRET`

---

## Known Constraints

| Constraint | Mitigation |
|------------|------------|
| Firebase console admin claim injection | `admin_role_grants` DB second factor |
| RunPod cold start (60–120s) | Warmup endpoint called on camera page open |
| Single-process Railway (API + worker) | Restart policy + Railway health checks |
| Model hallucination | Disclaimer on every response, pharmacist review required |

---

## Responsible Disclosure

To report a security vulnerability, contact the maintainer directly.
Do not open a public GitHub issue for security vulnerabilities.
