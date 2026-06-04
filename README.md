# MediBox — AI Prescription OCR for Tunisian Pharmacies

Clinical decision-support system that reads handwritten Arabic/French Tunisian prescriptions using a multimodal LLM, assists pharmacists with structured medication data, and requires human verification before any dispensing action.

> **MediBox assists. The pharmacist decides.**

---

## Architecture

```
Raspberry Pi 5 (edge)
  imx708 camera → YOLO11 ONNX detection
  3 horizontal strips → CLAHE color preprocessing
  WebSocket stream → Railway relay
         │
         ▼
Railway (FastAPI + arq worker)
  /v1/submit       ← crops from Pi
  /v1/camera/ws/*  ← live feed relay to browser
  arq job queue    → RunPod inference
  Neon Postgres    ← results, audit log
  Upstash Redis    ← job queue + WS pub/sub
         │
         ▼
RunPod Serverless (24GB GPU)
  vLLM serving Qwen2.5-VL-7B-Instruct-AWQ
  guided JSON decoding (schema-enforced output)
  temp=0, seed=42 — deterministic
         │
         ▼
CPU Postprocessing (Railway)
  SymSpell OCR typo correction
  Tunisian AMM formulary fuzzy match
  Doctor registry (TAHA.xlsx) normalization
  spaCy NER validation
  Specialty coherence check
  Fernet PII encryption
         │
         ▼
Vercel (React frontend)
  Camera page  — live Pi feed, stability ring, auto-submit
  Results page — verbatim OCR text + structured patient/doctor/medications
  Admin panel  — job dashboard, model registry, audit log
```

---

## Stack

| Layer | Technology |
|-------|-----------|
| Edge | Raspberry Pi 5, imx708, YOLO11 ONNX, Picamera2 |
| API & worker | FastAPI, arq, Python 3.11, Railway |
| Inference | Qwen2.5-VL-7B-Instruct-AWQ, vLLM, RunPod Serverless |
| Database | Neon (serverless Postgres), Upstash (serverless Redis) |
| Storage | Cloudflare R2 (crop images) |
| Auth | Firebase Authentication (JWT + custom claims) |
| Frontend | React, TypeScript, Tailwind CSS, Vite, Vercel |
| Infra as code | Terraform (Neon, RunPod, Upstash, Cloudflare) |

---

## Repository Structure

```
medibox-cloud/
├── services/
│   ├── api/            FastAPI — HTTP/WS gateway, auth, job management
│   ├── worker/         CPU postprocessing pipeline, AMM matching, NER
│   ├── vllm/           vLLM server config + system prompt
│   └── frontend/       React SPA (camera + results + admin)
├── pi_edge/            Canonical Pi source (camera, YOLO, preprocessing)
├── migrations/         Alembic database schema
├── referances/         Tunisian drug registry (AMM) + doctor registry (TAHA)
├── terraform/          Infrastructure as code
├── monitoring/         Grafana dashboards + Prometheus config
├── scripts/            Utility scripts (admin, migrations, RunPod control)
├── src/training/       QLoRA fine-tuning pipeline
├── pipelines/          Scheduled monthly retrain
├── tests/              Unit + integration tests
└── BENCHMARK.md        CER / latency tracking across pipeline changes
```

---

## Local Development

```bash
# 1. Copy env template
cp .env.example .env
# Fill in VLLM_URL, VLLM_API_KEY, REDIS_URL, DATABASE_URL, FIREBASE_*

# 2. Run DB migrations
bash scripts/02_run_migrations.sh

# 3. Start API (Railway equivalent locally)
cd services/api && uvicorn main:app --reload --port 8080

# 4. Frontend
cd services/frontend && npm install && npm run dev
```

---

## Deployment

| Service | Platform | Trigger |
|---------|----------|---------|
| API + worker | Railway | push to `main` |
| Frontend | Vercel | push to `main` |
| vLLM inference | RunPod Serverless | always-on endpoint |
| Pi edge daemon | systemd on Pi 5 | manual deploy via `scripts/` |

Pi deployment: SSH into Pi → SCP updated files from `pi_edge/` → `sudo systemctl restart medibox-camera`.

---

## Compliance

- Tunisian **Loi n°2004-63** (personal data protection)
- Patient/doctor names encrypted at rest (Fernet, key in Railway env)
- PII never logged in plaintext
- All admin actions written to append-only `audit_log` table
- Admin access requires Firebase JWT `admin` claim AND `admin_role_grants` DB row
- TLS enforced end-to-end; secrets only in Railway/RunPod env vars

---

## Known Limitations

- **Single-process Railway**: API and arq worker run in the same process. If the worker crashes, the API is affected. Migration path: split into two Railway services.
- **RunPod cold starts**: Serverless workers scale to zero. First scan after idle costs 60–120s. Warmup endpoint (`POST /v1/warmup`) is called on camera page open to front-load this.
- **No offline fallback**: Pi requires Railway connectivity to submit jobs.

---

All AI-generated extraction results require pharmacist verification before any clinical action.
