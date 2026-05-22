# Post-Deployment Checklist

Run through this checklist after every production deployment.

---

## Infrastructure

- [ ] `bash scripts/01_setup_project.sh` completed without errors
- [ ] All 6 Secret Manager secrets have non-placeholder values
- [ ] Cloud SQL Auth Proxy connectivity verified (`bash scripts/02_run_migrations.sh`)
- [ ] Alembic migrations at `head` (`alembic current` shows latest revision)
- [ ] Memorystore Redis reachable from Cloud Run (check worker logs for Redis connection)
- [ ] Serverless VPC Connector in `READY` state
- [ ] Cloud KMS key active and accessible by `medibox-runner@` SA

---

## Services

- [ ] `medibox-api` Cloud Run service deployed and healthy
  - [ ] `GET /v1/healthz` returns `{"status": "ok"}`
  - [ ] `GET /v1/readyz` returns 200 (Redis + Postgres reachable)
- [ ] `medibox-worker` Cloud Run service deployed (min-instances=1)
- [ ] `medibox-frontend` Cloud Run service deployed and serving HTML
- [ ] Vertex AI endpoint has ≥1 deployed model with health check passing

---

## Security

- [ ] No `.env` files deployed to Cloud Run (secrets via Secret Manager only)
- [ ] No sensitive values in Cloud Build logs (redacted by Secret Manager)
- [ ] Firebase Admin SDK initialized successfully (check API startup logs)
- [ ] Admin endpoint returns 403 for non-admin token
- [ ] Admin endpoint returns 403 for admin Firebase token WITHOUT `admin_role_grants` row (DD-013)
- [ ] PII encryption/decryption working (submit a test job, check patient_name in result)
- [ ] Audit log receiving writes (check `audit_log` table after an admin action)
- [ ] HSTS header present on API responses
- [ ] `X-Frame-Options: DENY` present on API responses

---

## Monitoring

- [ ] Cloud Monitoring dashboards deployed (System Overview, Model Quality, MLOps, Cost)
- [ ] Alert policies created and enabled
- [ ] Notification channel configured (email or PagerDuty)
- [ ] Budget alerts configured: $250/$350/$500
- [ ] Log-based metrics created (medibox_confidence_score, medibox_hallucination_count, etc.)

---

## Functional

- [ ] Submit a test prescription (1 crop JPEG)
- [ ] Poll `/v1/result/{job_id}` until `completed`
- [ ] Verify response contains `disclaimer` field
- [ ] Verify `medications` array populated
- [ ] Verify `overall_confidence` in [0, 1]
- [ ] WebSocket connection to `/v1/ws/jobs/{job_id}` receives at least one event
- [ ] Submit a correction for the test job
- [ ] Correction appears in BigQuery `medibox.feedback` table

---

## Model

- [ ] Vertex AI endpoint returns prediction for a test input (no timeout)
- [ ] Warmup completed (check vLLM server logs for "Warmup complete")
- [ ] Drug normalization working (check worker logs for normalization hits)

---

## CI/CD

- [ ] Cloud Build trigger active on `main` branch
- [ ] Most recent Cloud Build run: all steps green
- [ ] Smoke test in Cloud Build passed

---

## Training Pipeline

- [ ] KFP pipeline YAML compiled (`python pipelines/monthly_qlora_retrain.py`)
- [ ] Cloud Scheduler job configured for 28-day interval
- [ ] BigQuery `medibox.requests` and `medibox.feedback` tables receiving streaming inserts

---

## Sign-off

| Date | Deployer | Notes |
|------|----------|-------|
| | | |

