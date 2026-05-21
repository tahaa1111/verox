# Cost Breakdown — Medibox on GCP

**Honest estimate. Not optimistic.**

These figures use GCP's `us-central1` list prices as of early 2026. Actual bills will vary based on traffic, training frequency, and sustained-use discounts (which apply automatically). Where a service cannot scale to zero, the floor cost is noted.

---

## Summary by Scenario

| Scenario | Description | Est. Monthly Cost |
|----------|-------------|:-----------------:|
| **Idle** | Infrastructure running, no traffic | $310–335 |
| **Low** | 1 pharmacy, ~100 jobs/day | $340–390 |
| **Medium** | 10 pharmacies, ~1,000 jobs/day | $420–530 |

The dominant cost in every scenario is the **Vertex AI endpoint** (~$252/month). It cannot scale to zero. Everything else is secondary.

---

## Itemized Cost Table

### 1. Vertex AI Endpoint (Qwen2.5-VL-7B on T4)

The single largest line item. The model is deployed on `n1-standard-4 + 1× NVIDIA T4`. Vertex AI shared GPU endpoints do not support scale-to-zero.

| Item | Unit Price | Idle | Low (100/day) | Medium (1000/day) |
|------|-----------|------|--------------|-------------------|
| n1-standard-4 node-hours (24/7) | $0.19/hr | $138 | $138 | $138 |
| T4 GPU node-hours (24/7) | $0.35/hr | $252 | $252 | $252 |
| Prediction requests | ~$0.00003/req | — | ~$0.09 | ~$0.90 |
| **Subtotal** | | **~$252** | **~$252** | **~$252** |

> T4 price dominates. The CPU portion ($138) adds to it — it's a combined node cost.
> Actual Vertex Prediction pricing is per-node-hour for dedicated endpoints. At $0.35/hr × 720hr = $252/month for the GPU alone.

**Optimization:** Undeploy the model when no pharmacies are active (e.g., overnight 22:00–07:00 Africa/Tunis time). That is 9h/day × 30 days = 270h idle. Removing those hours saves ~$94/month but requires an automated scheduler and increases cold-start risk.

---

### 2. Cloud SQL — PostgreSQL (db-f1-micro)

| Item | Unit Price | All Scenarios |
|------|-----------|:-------------:|
| db-f1-micro instance (730 hr/month) | $0.0150/hr | ~$11 |
| Storage (10 GB SSD, expandable) | $0.170/GB/month | ~$1.70 |
| 7-day backup storage (~2× data) | $0.08/GB/month | ~$1.60 |
| **Subtotal** | | **~$14–16** |

> db-f1-micro has 1 shared vCPU and 614 MB RAM. It is the minimum viable size for this workload (max 25 connections). If you hit CPU saturation regularly, upgrading to db-g1-small (~$25/month) is the next step.

---

### 3. Memorystore — Redis (Basic, 1 GB)

| Item | Unit Price | All Scenarios |
|------|-----------|:-------------:|
| Basic tier, 1 GB, us-central1 | $0.049/GB/hr | ~$35 |
| **Subtotal** | | **~$35** |

> Memorystore Basic tier does not support replicas. The minimum useful size is 1 GB. There is no free tier. This is a fixed floor cost.

---

### 4. Cloud Run — API Service

| Item | Unit Price | Idle | Low | Medium |
|------|-----------|:----:|:---:|:------:|
| CPU (always-on min-instance, 1 vCPU) | $0.000024/vCPU-s | ~$5 | ~$5 | ~$5 |
| Memory (always-on min-instance, 512 MB) | $0.0000025/GB-s | ~$0.60 | ~$0.60 | ~$0.60 |
| Requests (first 2M free/month) | $0.40/million | — | ~$1.20 | ~$12 |
| CPU per request (100ms avg) | $0.000024/vCPU-s | — | ~$0.86 | ~$8.60 |
| **Subtotal** | | **~$6** | **~$7–8** | **~$26** |

> `min-instances=1` ensures the API is always warm. That costs ~$5–6/month for the always-on instance regardless of traffic.

---

### 5. Cloud Run — Worker Service

| Item | Unit Price | Idle | Low | Medium |
|------|-----------|:----:|:---:|:------:|
| CPU (always-on min=1, 1 vCPU) | $0.000024/vCPU-s | ~$5 | ~$5 | ~$5 |
| Memory (2 GB per instance) | $0.0000025/GB-s | ~$2.40 | ~$2.40 | ~$2.40 |
| CPU during job processing (avg 30s/job) | $0.000024/vCPU-s | — | ~$2.60 | ~$26 |
| Scale-up instances (> 1 during peak) | (same rates) | — | — | ~$10 |
| **Subtotal** | | **~$7** | **~$10** | **~$43** |

> Workers process one job at a time (`concurrency=1`). At 100 jobs/day × 30s/job ÷ 3600 = 0.83 CPU-hours of work billed as CPU-seconds on Cloud Run.

---

### 6. Cloud Storage

| Item | Unit Price | Idle | Low | Medium |
|------|-----------|:----:|:---:|:------:|
| Models bucket (LoRA adapter ~2 GB) | $0.020/GB/month | ~$0.04 | ~$0.04 | ~$0.04 |
| Models bucket — versioned (5 versions) | $0.020/GB/month | ~$0.20 | ~$0.20 | ~$0.20 |
| Crops bucket (90-day retention, JPEG crops) | $0.020/GB/month | — | ~$1.80 | ~$18 |
| Raw/DR exports bucket | $0.020/GB/month | ~$0.50 | ~$0.50 | ~$0.50 |
| Operations (Class A: uploads, Class B: reads) | $0.05/10K ops | — | ~$0.05 | ~$0.50 |
| **Subtotal** | | **~$0.75** | **~$2.60** | **~$19** |

> Crops: 9 crops/job × ~50 KB each × 100 jobs/day × 30 days = ~1.35 GB/month at low volume. At medium volume: ~13.5 GB/month. All crops are auto-deleted after 90 days (lifecycle rule).

---

### 7. BigQuery

| Item | Unit Price | Idle | Low | Medium |
|------|-----------|:----:|:---:|:------:|
| Storage (first 10 GB free) | $0.02/GB/month | Free | Free | ~$0.20 |
| Queries (first 1 TB free/month) | $5/TB | Free | Free | Free |
| Streaming inserts | $0.01/200 MB | — | ~$0.15 | ~$1.50 |
| **Subtotal** | | **$0** | **~$0.15** | **~$1.70** |

> At medium volume: 1,000 jobs/day × 2 rows × ~1 KB/row × 30 days = ~60 MB/month streaming. Well within the free tier for storage.

---

### 8. Cloud Build

| Item | Unit Price | Idle | Low | Medium |
|------|-----------|:----:|:---:|:------:|
| First 120 min/day free | — | Free | Free | Free |
| Regular build (e2-medium, ~15 min) | $0.003/min | — | ~$0.04 | ~$0.08 |
| vLLM Docker build (e2-highcpu-8, ~45 min) | $0.016/min | — | ~$0.72/run | ~$0.72/run |
| **Subtotal** (1 deploy/month) | | **$0** | **~$0.72** | **~$0.72** |

> Regular API/worker builds (< 120 min/day total) are free. The vLLM build is the expensive one because it uses a larger machine. Budget ~$1/deploy.

---

### 9. Vertex AI Training (QLoRA on A100)

| Item | Unit Price | Idle | Low | Medium |
|------|-----------|:----:|:---:|:------:|
| A100 GPU node-hours (~3h/run) | $3.67/hr | — | — | ~$11 |
| n1-highmem-8 CPU (training node) | $0.47/hr | — | — | ~$1.40 |
| GCS data read/write during training | ~$0.01 | — | — | ~$0.01 |
| **Subtotal** (1 run/month) | | **$0** | **$0** | **~$12–20** |

> Training only triggers when `MIN_TRAINING_CORRECTIONS` (default 500) corrections have accumulated. At 100 jobs/day, this may not trigger monthly. At 1,000 jobs/day it likely triggers monthly.

---

### 10. KMS (Cloud Key Management Service)

| Item | Unit Price | All Scenarios |
|------|-----------|:-------------:|
| Key versions (1 KEK active + 1 rotation pending) | $0.06/version/month | ~$0.12 |
| Cryptographic operations (encrypt/decrypt per job) | $0.03/10,000 ops | ~$0.09 (low) / ~$0.90 (medium) |
| **Subtotal** | | **~$0.21–$1.02** |

> Each job encrypts patient_name + doctor_name = 2 KMS ops. At 100 jobs/day × 2 ops × 30 days = 6,000 ops/month = $0.018. Negligible.

---

### 11. Secret Manager

| Item | Unit Price | All Scenarios |
|------|-----------|:-------------:|
| Active secret versions (7 secrets × 1 version) | $0.06/version/month | ~$0.42 |
| Access operations | $0.03/10,000 | ~$0.03 |
| **Subtotal** | | **~$0.45** |

> Cloud Run reads secrets at container startup, not per-request. Very low operation count.

---

### 12. Networking

| Item | Unit Price | Idle | Low | Medium |
|------|-----------|:----:|:---:|:------:|
| Cloud Run ingress | Free | $0 | $0 | $0 |
| Cloud Run to Cloud SQL (same region) | Free | $0 | $0 | $0 |
| Cloud Run to Vertex (same region) | Free | $0 | $0 | $0 |
| Egress to internet (API responses) | $0.12/GB | — | ~$0.12 | ~$1.20 |
| VPC Connector (e2-micro) | ~$0.008/hr | ~$5.76 | ~$5.76 | ~$5.76 |
| **Subtotal** | | **~$5.76** | **~$5.88** | **~$6.96** |

> VPC connector (required for Cloud Run → Memorystore Redis) costs ~$5.76/month for an e2-micro machine running 24/7. This is a fixed floor cost.

---

## Full Scenario Summary

### Scenario 1: Idle (Infrastructure up, no traffic)

| Service | Cost |
|---------|-----:|
| Vertex AI endpoint (T4, 24/7) | $252 |
| Memorystore Redis | $35 |
| Cloud SQL | $15 |
| VPC Connector | $6 |
| Cloud Run API (min=1) | $6 |
| Cloud Run Worker (min=1) | $7 |
| Cloud Storage (model storage) | $1 |
| Networking | $0 |
| KMS + Secrets | $1 |
| **Total** | **~$323/month** |

> Even with zero pharmacies using the system, you're spending ~$323/month. The Vertex endpoint alone is 78% of this.

---

### Scenario 2: Low — 1 Pharmacy, 100 Jobs/Day

| Service | Cost |
|---------|-----:|
| Vertex AI endpoint | $252 |
| Memorystore Redis | $35 |
| Cloud SQL | $15 |
| VPC Connector | $6 |
| Cloud Run API | $8 |
| Cloud Run Worker | $10 |
| Cloud Storage (crops + models) | $3 |
| BigQuery | $0 |
| Cloud Build (1 deploy/month) | $1 |
| Networking (egress) | $6 |
| KMS + Secrets | $1 |
| **Total** | **~$337/month** |

> At 1 pharmacy, the Vertex endpoint is still 75% of the bill. The incremental cost of 100 jobs/day is only ~$14/month above idle.

---

### Scenario 3: Medium — 10 Pharmacies, 1,000 Jobs/Day

| Service | Cost |
|---------|-----:|
| Vertex AI endpoint | $252 |
| Memorystore Redis | $35 |
| Cloud SQL | $16 |
| VPC Connector | $6 |
| Cloud Run API (scales up) | $26 |
| Cloud Run Worker (scales up) | $43 |
| Cloud Storage (crops accumulate) | $19 |
| BigQuery (streaming + storage) | $2 |
| Cloud Build (2 deploys/month) | $2 |
| Vertex AI Training (1 run) | $15 |
| Networking (egress) | $7 |
| KMS + Secrets | $1 |
| **Total** | **~$424/month** |

> At 10 pharmacies with 1,000 jobs/day, the total is still within a $500 budget. The Vertex endpoint is 60% of cost; Cloud Run is the second-largest item.

---

## Cost Optimization Levers (Ranked by Impact)

### 1. Schedule Vertex Endpoint Downtime — saves ~$84–100/month

Pharmacies in Tunisia operate roughly 08:00–20:00. If you undeploy the Vertex model during 20:00–07:00 (11h/day), you save ~38% of the GPU bill.

```bash
# Undeploy at 20:00 Africa/Tunis
gcloud scheduler jobs create http medibox-vertex-stop \
  --location=$REGION \
  --schedule="0 20 * * *" \
  --time-zone="Africa/Tunis" \
  --uri="https://us-central1-aiplatform.googleapis.com/v1/projects/$PROJECT_ID/locations/$REGION/endpoints/$VERTEX_ENDPOINT_ID:undeployModel" \
  --message-body="{\"deployedModelId\":\"$DEPLOYED_MODEL_ID\"}" \
  --oauth-service-account-email="medibox-runner@$PROJECT_ID.iam.gserviceaccount.com"

# Redeploy at 07:30 Africa/Tunis (30 min lead time before opening)
gcloud scheduler jobs create http medibox-vertex-start \
  --location=$REGION \
  --schedule="30 7 * * *" \
  --time-zone="Africa/Tunis" \
  --uri="https://us-central1-aiplatform.googleapis.com/v1/projects/$PROJECT_ID/locations/$REGION/endpoints/$VERTEX_ENDPOINT_ID:deployModel" \
  --message-body="{\"deployedModelId\":\"$DEPLOYED_MODEL_ID\",\"trafficSplit\":{\"0\":100}}" \
  --oauth-service-account-email="medibox-runner@$PROJECT_ID.iam.gserviceaccount.com"
```

**Warning:** Vertex model deployment takes 10–15 minutes. If you schedule start at 07:30, the endpoint may not be ready until 07:45. Configure the worker's `LOCAL_FALLBACK_ENABLED=true` as a safety net during the startup window.

---

### 2. Compress Crops Before Upload — saves ~30% on GCS storage

Client-side JPEG compression at quality=72 instead of 85 reduces crop sizes from ~150 KB to ~60 KB without meaningful model accuracy loss. Change `save(quality=85)` to `save(quality=72)` in `submit_prescription.py`'s `slice_into_crops()` function.

---

### 3. Use Cloud SQL Connection Pooler — avoids db-g1-small upgrade

db-f1-micro supports 25 connections. At scale (5 API instances × 5 connections/instance = 25), you hit the limit. Before upgrading to db-g1-small ($25/month), deploy PgBouncer in transaction pooling mode to multiplex many Cloud Run connections over a small number of DB connections.

---

### 4. Archive Old BigQuery Data — saves cents now, material at scale

BigQuery long-term storage (tables not modified for 90 days) is billed at $0.01/GB/month vs $0.02/GB active. Partition `medibox.requests` and `medibox.feedback` by date so rows older than 90 days auto-migrate to long-term pricing.

```sql
CREATE OR REPLACE TABLE `PROJECT_ID.medibox.requests`
PARTITION BY DATE(ts)
AS SELECT * FROM `PROJECT_ID.medibox.requests`;
```

---

### 5. Reduce Cloud Build Frequency — saves $1–3/month

Add `includedFiles` to the Cloud Build trigger so builds only run when `services/api/**` or `services/worker/**` change. Documentation and config changes won't trigger a build.

---

### 6. Upgrade Vertex Accelerator Type Strategically

| Machine | GPU | $/hr | Best for |
|---------|-----|-----:|----------|
| n1-standard-4 + T4 | 16 GB VRAM | $0.35 | < 500 jobs/day |
| n1-standard-4 + 2× T4 | 32 GB VRAM | $0.70 | Only if OOM on single T4 |
| n1-standard-8 + V100 | 16 GB VRAM | $2.48 | Not recommended — too expensive |

At low volume, a single T4 is the most cost-effective. Do not upgrade unless you're hitting GPU saturation or OOM errors.

---

## Free Tier Credits (What Applies Here)

| Credit | Amount | Notes |
|--------|--------|-------|
| Cloud Run requests | 2M/month | After free tier: $0.40/million |
| Cloud Run CPU | 180,000 vCPU-s/month | After free: $0.000024/vCPU-s |
| Cloud Run memory | 360,000 GB-s/month | After free: $0.0000025/GB-s |
| BigQuery queries | 1 TB/month | More than enough for this workload |
| BigQuery storage | 10 GB/month | First 10 GB free |
| Cloud Storage | 5 GB (US only) | Minimal credit, negligible |
| Secret Manager versions | First 6 free | We have 7 → first month $0.06 |
| KMS key versions | No free tier | $0.06/version/month |
| Networking egress | 1 GB/month to internet | After: $0.12/GB |

**New GCP accounts receive $300 in free credits** (valid 90 days). At ~$323/month, this covers roughly the first 28 days of idle operation.

---

## Budget Alert Thresholds (Recommended)

| Threshold | Action |
|-----------|--------|
| $200 (40% of $500) | Informational — track spend trend |
| $400 (80% of $500) | Alert fires — investigate before end of month |
| $500 (100%) | Review: is usage growing, or is something misconfigured? |

**If you consistently exceed $500/month** with medium usage, the main levers are:
1. Schedule Vertex downtime (saves ~$100)
2. Stop scheduled retraining until necessary (saves ~$15/run)
3. Consider moving to a preemptible GCE A100 for batch-only inference (more complex but ~40% cheaper at high volume)

---

## What These Numbers Don't Include

- **GCP support plan**: Basic (free). Enhanced ($150/month minimum) or Premium ($1,500/month minimum) not needed for a solo-engineer pilot.
- **Custom domain TLS certificate**: Free (Google-managed for Cloud Run custom domains).
- **Firebase Authentication**: Free for < 10,000 monthly active users on the Blaze plan. Auth cost is $0 at this scale.
- **Human time**: Initial setup 2–4 hours. Monthly maintenance < 1 hour.
- **Latency vs cost tradeoff for region**: Serving Tunisian pharmacies from `us-central1` adds ~150ms latency vs `europe-west1`. Moving to Europe adds ~$10–15/month (higher compute pricing tier) but reduces round-trip time. For async prescription reading this is not a material concern.
