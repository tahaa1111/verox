# GCP Era Archive — Medibox

This folder preserves the original Google Cloud Platform deployment for report documentation and screenshots.

## What this represents

The original production architecture before migrating to the free-tier stack.

## GCP Services that were running

| Service | GCP Product | Purpose |
|---------|------------|---------|
| medibox-api | Cloud Run | FastAPI REST API |
| medibox-worker | Cloud Run (min=1, no CPU throttle) | Celery OCR worker |
| medibox-frontend | Cloud Run | React frontend |
| medibox-vllm-gpu | Cloud Run (L4 GPU) | vLLM / Qwen2.5-VL-7B inference |
| medibox-postgres | Cloud SQL (db-f1-micro) | PostgreSQL database |
| medibox-redis | Memorystore Redis (1 GB) | Celery broker + cache |
| verox-4dc3f-crops | Cloud Storage | Prescription crop images |
| verox-4dc3f-models | Cloud Storage | Model artifacts |
| medibox KMS key | Cloud KMS | PII envelope encryption |
| medibox-* (14 secrets) | Secret Manager | All runtime credentials |
| Cloud Build | Cloud Build | CI/CD pipeline |
| medibox-repo | Artifact Registry | Docker images |
| Firebase / Vercel | Firebase Auth + Vercel | Authentication + Frontend |

## Architecture diagram (GCP era)

```
Pi 5 (YOLO11 ONNX)
  │  Tailscale VPN + HTTPS
  ▼
Cloud Run — medibox-api (FastAPI)
  │  Internal VPC
  ├──→ Cloud Run — medibox-worker (Celery, min=1, no-CPU-throttle)
  │         │
  │         ├──→ Cloud Run — medibox-vllm-gpu (L4 GPU, Qwen2.5-VL-7B)
  │         ├──→ Cloud SQL — medibox-postgres
  │         └──→ Cloud Storage — crops bucket
  │
  ├──→ Memorystore Redis (Celery broker + WebSocket pub/sub)
  ├──→ Cloud KMS (PII key encryption)
  └──→ Secret Manager (14 secrets)

CI/CD: GitHub → Cloud Build → Artifact Registry → Cloud Run deploy
```

## Monthly cost (why we migrated)

| Resource | Cost/month |
|----------|-----------|
| Cloud SQL db-f1-micro | ~$7 |
| Memorystore Redis 1GB | ~$35 |
| Worker (min=1, no throttle) | ~$130 |
| Cloud Run GPU (L4) idle | ~$60 |
| API + frontend (min=0) | ~$5 |
| **Total** | **~$237** |

After migration: ~$0/month idle, ~$0.40/scan (RunPod GPU only).

## Files in this archive

- `cloudbuild/` — All Cloud Build CI/CD pipeline configs
- `scripts/` — GCP provisioning scripts (project setup, secrets, first deploy, Vertex AI)
- `services/worker/utils/vertex_client.py` — Vertex AI batch prediction client
- `.env.deploy` — Production environment template (GCP project IDs, regions, billing)
- `run_vllm_deploy.sh` — Script to deploy vLLM container to Cloud Run GPU

## For your report

Screenshots to capture from the GCP Console (verox-4dc3f project):
1. Cloud Run services list (shows all 4 services)
2. Cloud SQL instance details
3. Memorystore Redis instance
4. Secret Manager secrets list
5. Artifact Registry repository
6. Cloud Build history (successful builds)
7. Cloud Monitoring dashboards
8. Firebase Authentication users
9. Cloud Storage buckets

These demonstrate production-grade GCP deployment in your report's "Implementation" chapter.
