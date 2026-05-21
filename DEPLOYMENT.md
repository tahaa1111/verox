# Deployment Guide

## Prerequisites

- GCP project with billing enabled
- `gcloud` CLI authenticated (`gcloud auth login`)
- Docker installed locally
- Python 3.11+
- Permissions: `roles/owner` or equivalent on the project

---

## Step 0: Set environment variables

```bash
export GCP_PROJECT_ID=your-project-id
export GCP_REGION=us-central1
export NOTIFICATION_EMAIL=oncall@yourpharmacy.tn
```

---

## Step 1: Enable APIs and provision infrastructure

```bash
bash scripts/01_setup_project.sh
```

This script is **idempotent** — safe to re-run. It provisions:
- APIs (Cloud Run, Vertex AI, BigQuery, KMS, etc.)
- Service accounts with least-privilege roles (see DD-010)
- Serverless VPC Connector
- Cloud KMS keyring + key (DD-008)
- GCS buckets with lifecycle rules (crops expire after 90 days)
- BigQuery dataset + tables with partition expiry
- Memorystore Redis + AUTH string in Secret Manager
- Cloud SQL Postgres + password in Secret Manager
- Secret Manager placeholders for Firebase and JWT secrets

---

## Step 2: Configure secrets

After Step 1, fill in the Secret Manager placeholders:

```bash
# Firebase admin SDK JSON (download from Firebase Console)
gcloud secrets versions add medibox-firebase-admin-json \
  --data-file=path/to/serviceAccountKey.json

# JWT signing key (generate a secure random key)
openssl rand -base64 64 | gcloud secrets versions add medibox-jwt-secret --data-file=-
```

---

## Step 3: Run database migrations

```bash
bash scripts/02_run_migrations.sh
```

Downloads Cloud SQL Auth Proxy, connects, and runs `alembic upgrade head`.

---

## Step 4: Deploy Cloud Run services (CI/CD)

```bash
gcloud builds submit --config cloudbuild.yaml .
```

This runs: lint → unit tests → build API/Worker/Frontend → push → deploy → smoke tests.

Or manually trigger on push to `main` via the Cloud Build trigger.

---

## Step 5: Deploy Vertex AI vLLM endpoint

```bash
# Takes 20-40 minutes — separate from the main pipeline
gcloud builds submit --config cloudbuild-vllm.yaml \
  --substitutions="_MODEL_GCS=gs://${GCP_PROJECT_ID}-models/vllm-weights/"
```

This builds the vLLM container, pushes it to Artifact Registry, and calls `scripts/03_deploy_vllm_endpoint.sh` to create/update the Vertex AI endpoint.

---

## Step 6: Configure Workload Identity Federation (edge Pi auth)

```bash
export EDGE_SA_EMAIL="medibox-edge@${GCP_PROJECT_ID}.iam.gserviceaccount.com"
bash scripts/setup_workload_identity_federation.sh
```

Generates `edge_credentials.json` — copy to the Pi:

```bash
scp edge_credentials.json pi@raspberrypi.local:/home/pi/.config/medibox/
```

---

## Step 7: Deploy monitoring

```bash
export NOTIFICATION_EMAIL=oncall@yourpharmacy.tn
bash monitoring/deploy_monitoring.sh
```

Creates log-based metrics, dashboards, and alert policies.

Budget alerts must be configured manually via Cloud Console → Billing → Budgets.
Thresholds: **$250** (alert), **$350** (alert), **$500** (alert + pause endpoint).

---

## Step 8: Verify deployment

```bash
# Get API URL
API_URL=$(gcloud run services describe medibox-api \
  --region=$GCP_REGION --format='value(status.url)')

# Health check
curl "$API_URL/v1/healthz"

# Readiness check (includes Redis + Postgres)
curl "$API_URL/v1/readyz"
```

---

## Cost Management

**Idle baseline: ~$317/month** (Vertex T4 is the dominant cost at $252/month — cannot scale to zero, DD-001).

To pause the Vertex endpoint when not in use:
```bash
bash scripts/pause_endpoint.sh    # saves ~$252/month
bash scripts/resume_endpoint.sh   # restores in ~5 minutes
```

---

## CI/CD Triggers

| Trigger | Config | When |
|---------|--------|------|
| Main CI/CD | `cloudbuild.yaml` | Push to `main` |
| vLLM rebuild | `cloudbuild-vllm.yaml` | Manual or push to `services/vllm/**` |
| Monthly retrain | KFP pipeline | Cloud Scheduler → every 28 days |

---

## Rollback

Via API (admin auth required):
```bash
curl -X POST "$API_URL/v1/admin/model/rollback" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"deployed_model_id": "previous-deployed-model-id"}'
```

Via Cloud Build: redeploy the previous `$SHORT_SHA` image.
