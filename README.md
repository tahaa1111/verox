# Medibox Cloud — AI-Assisted Prescription Reading for Tunisian Pharmacies

> **Pharmacist verification required. Medibox assists, it does not dispense.**

Medibox is a clinical decision-support system that extracts structured data from handwritten Tunisian prescriptions using a fine-tuned multimodal LLM (Qwen2.5-VL-7B AWQ INT4) deployed on Google Cloud Platform.

---

## Architecture

```
Pi Edge Device  ──HTTPS──►  Cloud Run API  ──Celery──►  Cloud Run Worker
                                                              │
                                              Vertex AI Endpoint (vLLM + Qwen2.5-VL)
                                                              │
                                              Cloud SQL Postgres  +  Memorystore Redis
                                                              │
                                              Cloud Storage (GCS)  +  BigQuery
                                                              │
                                              Vertex AI Pipelines (monthly QLoRA retrain)
```

**Key design decisions:** see [DESIGN_DECISIONS.md](DESIGN_DECISIONS.md)  
**Risk register:** see [RISKS.md](RISKS.md)  
**Cost model:** see [COSTS.md](COSTS.md)

---

## Quick Start (local development)

```bash
# 1. Copy environment template
cp .env.example .env
# Edit .env — set real values for local dev only

# 2. Start local services
docker compose up -d postgres redis

# 3. Run migrations
bash scripts/02_run_migrations.sh

# 4. Start API
cd services/api && uvicorn main:app --reload --port 8080

# 5. Start worker
cd services/worker && celery -A celery_app worker -Q inference,default -c 1 --loglevel=info
```

---

## GCP Deployment

See [DEPLOYMENT.md](DEPLOYMENT.md) for the complete step-by-step deployment guide.

```bash
# One-time setup (idempotent)
export GCP_PROJECT_ID=your-project-id
bash scripts/01_setup_project.sh

# Deploy all Cloud Run services via Cloud Build
gcloud builds submit --config cloudbuild.yaml .

# Deploy Vertex AI vLLM endpoint (takes 20-40 min — separate pipeline)
gcloud builds submit --config cloudbuild-vllm.yaml .
```

---

## Services

| Service | Runtime | Purpose |
|---------|---------|---------|
| `medibox-api` | Cloud Run | FastAPI — HTTP/WS gateway, auth, job management |
| `medibox-worker` | Cloud Run | Celery — image processing, Vertex AI inference |
| `medibox-frontend` | Cloud Run | React SPA — pharmacist UI |
| `medibox-vllm` | Vertex AI Endpoint | vLLM serving Qwen2.5-VL-7B AWQ INT4 on T4 GPU |

---

## Repository Structure

```
medibox-cloud/
├── services/
│   ├── api/          # FastAPI application
│   ├── worker/       # Celery worker + utilities
│   ├── vllm/         # vLLM server + Vertex AI container
│   └── frontend/     # React + TypeScript SPA
├── migrations/       # Alembic database migrations
├── pipelines/        # Vertex AI KFP v2 pipeline definitions
├── src/training/     # QLoRA training + evaluation scripts
├── scripts/          # Setup, deployment, and utility scripts
├── monitoring/       # Cloud Monitoring dashboards + alert policies
├── tests/            # Unit, integration, and load tests
│   ├── unit/
│   ├── integration/
│   └── load/         # Locust load test
├── referances/       # Tunisian drug reference data (real files)
│   ├── drug_dict.json
│   └── drug_registry.json
├── cloudbuild.yaml          # CI/CD: API + Worker + Frontend
├── cloudbuild-vllm.yaml     # CI/CD: vLLM container (separate — slow)
├── DESIGN_DECISIONS.md
├── RISKS.md
└── COSTS.md
```

---

## Running Tests

```bash
# Unit tests (no GCP required)
pip install pytest pytest-asyncio pillow rapidfuzz json-repair pydantic cryptography
pytest tests/unit/ -v

# Integration tests (requires mocked or real services)
pytest tests/integration/ -v

# Load tests (requires running API)
pip install locust
export MEDIBOX_TOKEN="your-firebase-jwt"
locust -f tests/load/locustfile.py --host=https://your-api-url.run.app
```

---

## Compliance

- Tunisian **Loi n°2004-63** (personal data protection) — not HIPAA
- Patient and doctor names encrypted at rest via Cloud KMS (DD-008)
- PII never logged in plaintext
- All admin actions audited to append-only `audit_log` table
- Admin access requires Firebase JWT `admin` claim AND `admin_role_grants` DB row (DD-013)
- TLS 1.3 enforced (Cloud Run default), HSTS via response headers
- Secrets managed via Google Secret Manager — never in container images or `.env` files in production

---

## Legal

See [TERMS_OF_SERVICE.md](TERMS_OF_SERVICE.md) and [LIABILITY.md](LIABILITY.md).

All AI-generated prescription extractions require pharmacist review before any clinical action.
