# Medibox — Operational Risk Register

| ID | Risk | Probability | Impact | Mitigation | Owner |
|----|------|-------------|--------|------------|-------|
| R-01 | Vertex T4 endpoint OOM — model + logprobs exceed 16GB VRAM | Low | High | AWQ INT4 uses ~5.5GB; `--gpu-memory-utilization 0.85` leaves 2.4GB headroom. Alert at 95% VRAM. Runbook §4. | Infra |
| R-02 | Vertex endpoint cold start (min-replicas temporarily 0 after manual pause) | Medium | High | `resume_endpoint.sh` documented as 5-min operation. Runbook §5. Schedule resumes before pharmacy open hours. | Ops |
| R-03 | Memorystore Basic tier single-AZ failure | Low | Medium | Celery retries (3×, 5s backoff). Jobs fail-safe: Pi polls `GET /result` and gets a 5xx, retries. Upgrade to Standard HA at >50 deployments. | Infra |
| R-04 | Cloud SQL db-f1-micro CPU saturation | Medium | Medium | Alert at 80% CPU. Upgrade to db-g1-small ($25/month) with one flag change. Query plan: jobs table is indexed on `status`, `device_id`, `created_at`. | Infra |
| R-05 | KFP pipeline fails mid-run; partial training state left in GCS | Low | Medium | Each component is idempotent (writes to timestamped GCS prefixes). Re-triggering the pipeline from the `finetune` step is safe. Failed runs do NOT update the model registry. | MLOps |
| R-06 | Bad retrained model deployed to production; regression not caught by canary | Low | Critical | Shadow eval (1h mirrored evaluation) + canary deploy (10% traffic, 2h) with auto-rollback. `POST /admin/model/rollback` flips traffic split in <1s. 7-day retention of previous deployedModel. | MLOps |
| R-07 | Drug formulary reference data out of date | Medium | High | `referances/` is version-controlled. Any update requires a new container build and CI pipeline run. Monthly review scheduled. | Data |
| R-08 | Firebase JWT leakage (compromised Pi device) | Low | High | JWTs expire in 1h (Firebase default). Pi refreshes at 50-min mark. Compromised device: revoke the Firebase UID via Firebase console. Rate limit by `device_id` (Redis-backed). | Security |
| R-09 | Secret Manager secret rotation without coordinated Cloud Run redeployment | Low | High | Cloud Run picks up new secret versions only on new instance spin-up. Use Secret Manager version pinning + Cloud Run revision redeployment as part of secret rotation runbook. | Security |
| R-10 | BigQuery costs spike due to un-partitioned query | Low | Medium | All production tables partitioned by `ts`. Dashboard queries use `DATE(_PARTITIONTIME)` filter. Cost alert at $50/month on BigQuery. | Data |
| R-11 | Cloud Run API scales to 10 instances under DDoS, costs spike | Low | Medium | Rate limiting per `device_id` in FastAPI middleware (Redis-backed). Cloud Armor (optional, adds cost) for WAF. Max 10 Cloud Run instances caps cost at ~$0.50/h. | Security |
| R-12 | Vertex Pipelines quota exhaustion (concurrent pipeline runs or T4 quota) | Low | Medium | Pipeline is 4-weekly, single run at a time. Cloud Monitoring alert if a run exceeds 8 hours (signals a stuck component). Request A100 quota as fallback for finetune step. | MLOps |
| R-13 | Data retention compliance failure (raw crops retained > 90 days) | Low | Critical | GCS lifecycle policy auto-deletes crops bucket objects after 90 days. Weekly Cloud Scheduler verification job checks for stale objects. | Compliance |
| R-14 | Cloud KMS key deletion (accidental or malicious) | Very Low | Critical | KMS key destruction has a mandatory 24-hour scheduled destruction window — cannot be instant. Enable key version deletion prevention policy. Multiple key admins required. | Security |
| R-15 | Postgres Auth Proxy connection pool exhaustion on Cloud Run burst | Low | Medium | Cloud Run concurrency=80 per instance; SQLAlchemy pool=5 per process. At 10 instances = 50 max connections. Cloud SQL db-f1-micro supports 25 connections max. **This is a real risk at max scale.** Mitigation: use PgBouncer sidecar or reduce Cloud Run max-instances to 5. | Infra |

## Risk R-15 Deep Dive (Connection Pool Ceiling)

Cloud SQL db-f1-micro has a **hard limit of 25 PostgreSQL connections**. At Cloud Run
max-instances=10 with concurrency=80 and SQLAlchemy pool_size=3:
- 10 instances × 3 connections = 30 connections — **exceeds the limit by 5**.

**Immediate mitigations applied in this implementation:**
1. `max-instances=5` for Cloud Run API (15 connections max, safe headroom).
2. SQLAlchemy `pool_size=2, max_overflow=1` — 3 connections per instance, 15 max.
3. Cloud Monitoring alert: "Cloud SQL connection count > 20".

**Upgrade path:** `db-g1-small` supports 400 connections. One `gcloud sql instances patch`
command. No code changes required.
