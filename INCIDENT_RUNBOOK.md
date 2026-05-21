# Incident Runbook — Medibox

This runbook covers the 12 most likely production incidents. Each entry follows:
**Symptom → Likely cause → Diagnosis → Fix → Prevention**

---

## Quick Reference

| # | Incident | Severity | Est. MTTR |
|---|----------|----------|-----------|
| IR-01 | Vertex Endpoint OOM / model load failure | P1 | 15–30 min |
| IR-02 | Queue backup — Redis growing unbounded | P2 | 20–40 min |
| IR-03 | Worker stuck on a job | P2 | 5–15 min |
| IR-04 | Cloud SQL CPU saturation | P2 | 10–20 min |
| IR-05 | Cloud SQL storage full | P1 | 10–20 min |
| IR-06 | Failed canary deployment (Vertex traffic split) | P2 | 10–20 min |
| IR-07 | Model regression after retraining | P1 | 15–30 min |
| IR-08 | Cloud Run cold start latency spike | P3 | 5–15 min |
| IR-09 | Memorystore connection failures (VPC connector) | P1 | 15–30 min |
| IR-10 | Budget alert triggered | P3 | 30–60 min |
| IR-11 | Firebase JWT validation failures spike | P1 | 10–20 min |
| IR-12 | IAM permission denied — service-to-service | P2 | 10–20 min |

**Severity:** P1 = service down / data risk. P2 = degraded. P3 = warning / cost.

---

## IR-01 — Vertex Endpoint OOM or Model Load Failure

### Symptom
- All prescription jobs fail at the inference step with 500 or 503
- Worker logs: `Vertex prediction error` or `503 Service Unavailable`
- Cloud Monitoring: Vertex endpoint error rate alert fires
- Jobs pile up in the queue; nothing completes

### Likely Cause
1. Model artifacts are corrupted or missing from GCS
2. The deployed model version ran out of GPU memory (OOM kill)
3. Vertex replica crashed during a rolling update and is stuck in `DEPLOYING` state
4. The endpoint node `n1-standard-4+T4` was preempted (Vertex uses spot resources for shared GPU quotas)

### Diagnosis

```bash
# 1. Check endpoint status
gcloud ai endpoints list \
  --region=$REGION --project=$PROJECT_ID \
  --filter="displayName:medibox"

gcloud ai endpoints describe $VERTEX_ENDPOINT_ID \
  --region=$REGION --project=$PROJECT_ID \
  --format="table(deployedModels[].id,deployedModels[].dedicatedResources,deployedModels[].createTime)"

# 2. Check recent Vertex prediction logs
gcloud logging read \
  'resource.type="aiplatform.googleapis.com/Endpoint"
   severity>=ERROR' \
  --project=$PROJECT_ID \
  --freshness=30m \
  --limit=20

# 3. Check if model artifacts exist
MODEL_GCS=$(gcloud ai models describe $MODEL_ID \
  --region=$REGION --project=$PROJECT_ID \
  --format="value(artifactUri)")
gcloud storage ls "$MODEL_GCS"

# 4. Check OOM — look for SIGKILL / memory in logs
gcloud logging read \
  'resource.type="aiplatform.googleapis.com/Endpoint"
   textPayload:"OOM"
   OR textPayload:"Killed"
   OR textPayload:"out of memory"' \
  --project=$PROJECT_ID \
  --freshness=1h
```

### Fix

**If replica is stuck deploying / crashed:**
```bash
# Undeploy the current model and redeploy
DEPLOYED_MODEL_ID=$(gcloud ai endpoints describe $VERTEX_ENDPOINT_ID \
  --region=$REGION --project=$PROJECT_ID \
  --format="value(deployedModels[0].id)")

gcloud ai endpoints undeploy-model $VERTEX_ENDPOINT_ID \
  --region=$REGION --project=$PROJECT_ID \
  --deployed-model-id=$DEPLOYED_MODEL_ID

# Wait 60s, then redeploy
sleep 60
gcloud ai endpoints deploy-model $VERTEX_ENDPOINT_ID \
  --region=$REGION --project=$PROJECT_ID \
  --model=$MODEL_ID \
  --display-name="medibox-vllm" \
  --machine-type=n1-standard-4 \
  --accelerator=type=NVIDIA_TESLA_T4,count=1 \
  --traffic-split=0=100
```

**If model artifacts are missing (GCS object deleted):**
```bash
# Restore from a versioned previous object
gcloud storage ls -a "gs://${PROJECT_ID}-models/lora-adapters/"
# Find a good previous version, copy it back to current/
gcloud storage cp \
  "gs://${PROJECT_ID}-models/lora-adapters/<GOOD_RUN>/" \
  "gs://${PROJECT_ID}-models/lora-adapters/current/" \
  --recursive
# Then redeploy as above
```

**Immediate mitigation — enable local fallback:**
The API has a `LOCAL_FALLBACK_ENABLED` env var. When `true`, the worker
uses the base Qwen model served via the `medibox-vllm` Cloud Run service (if
deployed) instead of the Vertex endpoint.
```bash
gcloud run services update medibox-worker \
  --region=$REGION --project=$PROJECT_ID \
  --set-env-vars="LOCAL_FALLBACK_ENABLED=true"
```

### Prevention
- Alert fires at Vertex error rate > 0.5/s (alert_policies.json IR-01 equivalent)
- Schedule monthly `verify_backups.sh` which checks model artifact existence
- Pin model artifact GCS path in Vertex model registry — never delete the `current/` folder without creating the next one first

---

## IR-02 — Queue Backup (Redis Growing Unbounded)

### Symptom
- Celery queue depth alert fires (> 100 tasks)
- Jobs are submitted successfully (`202 Accepted`) but results never arrive
- Worker logs are silent or show repeated connection errors
- `redis-cli INFO memory` shows `used_memory` climbing toward `maxmemory`

### Likely Cause
1. Worker is down (scaled to 0 or crashed) — tasks accumulate
2. Worker is consuming but inference is too slow (Vertex latency spike)
3. Dead-letter tasks filling the queue (repeatedly failing and re-queuing)
4. Redis `maxmemory-policy` is `noeviction` — queue items block new writes

### Diagnosis

```bash
# 1. Check worker instance count
gcloud run services describe medibox-worker \
  --region=$REGION --project=$PROJECT_ID \
  --format="value(status.observedGeneration,status.conditions)"

# How many instances are actually running?
gcloud monitoring read \
  'metric.type="run.googleapis.com/container/instance_count"
   resource.label.service_name="medibox-worker"' \
  --project=$PROJECT_ID \
  --freshness=10m

# 2. Check Celery queue depth via Redis
# Connect via Cloud SQL Auth Proxy pattern (for Redis, use stunnel or VPC):
# From a GCE VM in the same VPC (or Cloud Shell with VPC access):
REDIS_HOST=$(grep REDIS_HOST gcp_resources.txt | cut -d= -f2)
redis-cli -h "$REDIS_HOST" -p 6379 LLEN celery

# 3. Check for dead-letter / failed tasks
redis-cli -h "$REDIS_HOST" -p 6379 LLEN celery.dead
redis-cli -h "$REDIS_HOST" -p 6379 KEYS "celery-task-meta-*" | wc -l

# 4. Check worker error logs
gcloud logging read \
  'resource.type="cloud_run_revision"
   resource.label.service_name="medibox-worker"
   severity>=ERROR' \
  --project=$PROJECT_ID \
  --freshness=30m \
  --limit=30
```

### Fix

**If worker is down:**
```bash
# Check min-instances is set (worker should never scale to 0)
gcloud run services update medibox-worker \
  --region=$REGION --project=$PROJECT_ID \
  --min-instances=1

# Force a new revision to restart stuck workers
gcloud run services update medibox-worker \
  --region=$REGION --project=$PROJECT_ID \
  --set-env-vars="RESTART_TS=$(date +%s)"
```

**If queue is backed up but worker is running — scale up:**
```bash
# Temporarily scale worker concurrency (each worker handles 1 job — not concurrent)
# Instead, increase max-instances to drain faster
gcloud run services update medibox-worker \
  --region=$REGION --project=$PROJECT_ID \
  --max-instances=10   # temporarily above normal max of 5
```

**If dead-letter tasks are filling the queue:**
```bash
# Inspect what's in the dead-letter queue
redis-cli -h "$REDIS_HOST" -p 6379 LRANGE celery.dead 0 9

# If they're safe to discard:
redis-cli -h "$REDIS_HOST" -p 6379 DEL celery.dead

# Mark corresponding DB jobs as FAILED so users see results
# (Run via Cloud SQL Auth Proxy + psql)
UPDATE jobs SET status='failed', error='Worker queue overflow — task discarded'
WHERE status='processing'
  AND updated_at < NOW() - INTERVAL '30 minutes';
```

### Prevention
- Set `min-instances=1` on medibox-worker (already in `03_first_deploy.sh`)
- Celery task max retries = 3 with exponential backoff, then dead-letter
- Alert on queue depth > 100 (already in `alert_policies.json`)
- Redis `maxmemory-policy` should be `allkeys-lru` to prevent write stalls

---

## IR-03 — Worker Stuck on a Job

### Symptom
- A specific `job_id` stays in `processing` state indefinitely
- The pharmacy UI shows the spinner but no result
- Worker logs for that job_id stop mid-processing
- Other jobs complete normally (only one job is stuck)

### Likely Cause
1. Vertex prediction request hung — TCP connection open but no response
2. PII decryption/re-encryption loop exceeded timeout
3. Worker container ran out of memory on a large image (9 crops × 2MB = 18MB)
4. A bug introduced by a recent deploy causes an infinite retry

### Diagnosis

```bash
# 1. Find the stuck job
psql -h 127.0.0.1 -U medibox medibox <<'EOF'
SELECT job_id, device_id, status, created_at, updated_at,
       NOW() - updated_at AS stuck_for
FROM jobs
WHERE status = 'processing'
ORDER BY updated_at ASC
LIMIT 10;
EOF

# 2. Find the worker instance handling it
gcloud logging read \
  "resource.type=\"cloud_run_revision\"
   resource.label.service_name=\"medibox-worker\"
   jsonPayload.job_id=\"<JOB_ID>\"" \
  --project=$PROJECT_ID \
  --freshness=2h \
  --limit=50

# 3. Check if Vertex has a pending request for this job
gcloud logging read \
  "resource.type=\"aiplatform.googleapis.com/Endpoint\"
   jsonPayload.job_id=\"<JOB_ID>\"" \
  --project=$PROJECT_ID \
  --freshness=2h
```

### Fix

```bash
# Mark the job failed — unblocks the pharmacy UI
psql -h 127.0.0.1 -U medibox medibox <<EOF
UPDATE jobs
SET status = 'failed',
    error  = 'Worker timeout — please resubmit',
    updated_at = NOW()
WHERE job_id = '<JOB_ID>'
  AND status = 'processing';
EOF

# Force worker to release the Celery task lock
# (If using Celery task_id stored in jobs.celery_task_id)
redis-cli -h "$REDIS_HOST" -p 6379 DEL "celery-task-meta-<CELERY_TASK_ID>"

# Restart the worker to clear any stuck TCP connections
gcloud run services update medibox-worker \
  --region=$REGION --project=$PROJECT_ID \
  --set-env-vars="RESTART_TS=$(date +%s)"
```

### Prevention
- Celery task `time_limit=300` (5 min hard kill) and `soft_time_limit=240`
- Vertex prediction client timeout set to 120s
- A sweeper cron (runs every 10 min via Cloud Scheduler) marks jobs `failed` if `updated_at < NOW() - INTERVAL '10 minutes'` and `status='processing'`

---

## IR-04 — Cloud SQL CPU Saturation

### Symptom
- Cloud SQL CPU alert fires (> 80% for 5 min)
- API requests that hit the DB are slow (> 500ms for simple queries)
- `pg_stat_activity` shows many long-running queries
- API p95 latency alert may also fire

### Likely Cause
1. Missing index — a new query introduced in a recent deploy does a seq scan
2. `pg_stat_activity` lock contention — an UPDATE is blocking SELECTs
3. Connection storm — Cloud Run scaled up suddenly and all connections hit DB simultaneously
4. A retraining pipeline is running and reading large amounts of job/feedback data

### Diagnosis

```bash
# Connect via Cloud SQL Auth Proxy
cloud-sql-proxy --port=5432 "$PROJECT_ID:$REGION:medibox-postgres" &
psql -h 127.0.0.1 -U medibox medibox

-- In psql: find expensive queries
SELECT pid, now() - pg_stat_activity.query_start AS duration,
       query, state
FROM pg_stat_activity
WHERE (now() - pg_stat_activity.query_start) > interval '5 seconds'
ORDER BY duration DESC;

-- Check for lock waits
SELECT blocked.pid, blocked.query,
       blocking.pid AS blocking_pid, blocking.query AS blocking_query
FROM pg_stat_activity AS blocked
JOIN pg_stat_activity AS blocking
  ON blocking.pid = ANY(pg_blocking_pids(blocked.pid))
WHERE NOT blocked.granted;

-- Check for missing indexes (seq scans > 1000 rows)
SELECT schemaname, tablename, seq_scan, seq_tup_read,
       idx_scan, n_live_tup
FROM pg_stat_user_tables
ORDER BY seq_tup_read DESC
LIMIT 10;
```

### Fix

**Kill blocking long-running queries:**
```sql
-- In psql: terminate the blocking pid
SELECT pg_terminate_backend(<BLOCKING_PID>);
```

**Add a missing index (non-blocking):**
```sql
-- Example: if jobs table is missing status index
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_jobs_status
  ON jobs(status)
  WHERE status IN ('pending','processing');
```

**Reduce connection pressure temporarily:**
```bash
# Scale down Cloud Run API to reduce connections
gcloud run services update medibox-api \
  --region=$REGION --project=$PROJECT_ID \
  --max-instances=2   # reduce from 5 to 2 temporarily

# After CPU recovers, restore
gcloud run services update medibox-api \
  --region=$REGION --project=$PROJECT_ID \
  --max-instances=5
```

**If a training pipeline is the cause:**
```bash
# Check if a Vertex Pipeline run is active
gcloud ai pipeline-jobs list \
  --region=$REGION --project=$PROJECT_ID \
  --filter="state=PIPELINE_STATE_RUNNING"

# Cancel it if CPU impact is too high
gcloud ai pipeline-jobs cancel <PIPELINE_JOB_ID> \
  --region=$REGION --project=$PROJECT_ID
```

### Prevention
- db-f1-micro max connections = 25; use connection pool (asyncpg pool_size=5 per Cloud Run instance)
- Add `CONCURRENTLY` to index creation in all migrations
- Run `EXPLAIN ANALYZE` on every new query before deploy
- Alert on connections > 20 (already in `alert_policies.json` IR-04 equivalent)

---

## IR-05 — Cloud SQL Storage Full

### Symptom
- Cloud SQL instance goes read-only (`ERROR: cannot execute INSERT in a read-only transaction`)
- All API writes fail with 500; reads may still work
- Cloud Monitoring: Cloud SQL disk bytes usage near provisioned size

### Likely Cause
1. Job result JSON blobs are larger than expected (base64 images stored in DB — bug)
2. WAL/transaction logs not being cleaned up (PITR retention consuming space)
3. Dead tuples not vacuumed — table bloat

### Diagnosis

```bash
# Check current disk usage
gcloud sql instances describe medibox-postgres \
  --project=$PROJECT_ID \
  --format="table(settings.dataDiskSizeGb,diskEncryptionConfiguration)"

# In psql: find largest tables
SELECT schemaname, tablename,
       pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size
FROM information_schema.tables
WHERE table_schema NOT IN ('pg_catalog','information_schema')
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC
LIMIT 10;

-- Check dead tuple bloat
SELECT relname, n_dead_tup, n_live_tup,
       round(100.0 * n_dead_tup / NULLIF(n_live_tup + n_dead_tup, 0), 1) AS dead_pct
FROM pg_stat_user_tables
ORDER BY n_dead_tup DESC;
```

### Fix

**Immediate — increase disk (auto-storage-increase is enabled but check):**
```bash
gcloud sql instances patch medibox-postgres \
  --storage-size=20GB \
  --project=$PROJECT_ID
# Cloud SQL can resize online; no restart needed for storage expansion
```

**Remove bloat — run VACUUM:**
```sql
-- In psql (not in a transaction)
VACUUM FULL ANALYZE jobs;
VACUUM FULL ANALYZE job_results;
```

**If large blobs were accidentally stored in DB:**
```sql
-- Find rows with oversized result_json (> 100KB is suspicious)
SELECT job_id, pg_column_size(result_json) AS result_size_bytes
FROM job_results
WHERE pg_column_size(result_json) > 102400
ORDER BY result_size_bytes DESC
LIMIT 20;

-- If images were stored: redact them (move reference to GCS, clear blob)
UPDATE job_results
SET result_json = jsonb_set(result_json, '{crops}', '[]')
WHERE job_id IN (<LIST_OF_JOB_IDS>);
```

### Prevention
- Cloud SQL `--storage-auto-increase` is set in `01_setup_project.sh`
- Scheduled `pg_repack` or `VACUUM` in maintenance window (Cloud SQL handles autovacuum but check `autovacuum_vacuum_scale_factor`)
- Never store binary data in DB — all images go to GCS (`{PROJECT_ID}-crops` bucket)
- Alert when disk usage > 80% (add to `alert_policies.json`)

---

## IR-06 — Failed Canary Deployment (Vertex Traffic Split)

### Symptom
- A new model version was deployed with a partial traffic split (e.g., 20% to new model)
- Some jobs return worse results or structured JSON parse failures increase
- `model_quality` dashboard shows a spike in `json_parse_failures` or drop in `drug_f1`
- Pharmacists are reporting more corrections than usual

### Likely Cause
1. The new LoRA adapter was fine-tuned on too few samples or overfit to training data
2. The new adapter prompt template changed and the inference container was not updated
3. The new model container image has a bug in the post-processing pipeline

### Diagnosis

```bash
# 1. Check traffic split
gcloud ai endpoints describe $VERTEX_ENDPOINT_ID \
  --region=$REGION --project=$PROJECT_ID \
  --format="table(deployedModels[].id,deployedModels[].trafficSplit)"

# 2. Compare error rates by deployed model version
# In BigQuery:
bq query --project_id="$PROJECT_ID" --use_legacy_sql=false "
SELECT
  JSON_EXTRACT_SCALAR(result_metadata, '$.model_version') AS model_version,
  COUNT(*) AS total_jobs,
  COUNTIF(status='failed') AS failures,
  AVG(CAST(JSON_EXTRACT_SCALAR(result_metadata, '$.confidence') AS FLOAT64)) AS avg_confidence
FROM \`$PROJECT_ID.medibox.requests\`
WHERE DATE(ts) = CURRENT_DATE()
GROUP BY 1
ORDER BY 1
"

# 3. Check correction rate for new vs old model
bq query --project_id="$PROJECT_ID" --use_legacy_sql=false "
SELECT
  JSON_EXTRACT_SCALAR(r.result_metadata, '$.model_version') AS model_version,
  COUNT(f.job_id) AS corrections
FROM \`$PROJECT_ID.medibox.requests\` r
LEFT JOIN \`$PROJECT_ID.medibox.feedback\` f USING (job_id)
WHERE DATE(r.ts) = CURRENT_DATE()
GROUP BY 1
"
```

### Fix — Roll Back Traffic to Old Model

```bash
# 1. Find old deployed model ID
OLD_MODEL_ID=$(gcloud ai endpoints describe $VERTEX_ENDPOINT_ID \
  --region=$REGION --project=$PROJECT_ID \
  --format="value(deployedModels[0].id)")
NEW_MODEL_ID=$(gcloud ai endpoints describe $VERTEX_ENDPOINT_ID \
  --region=$REGION --project=$PROJECT_ID \
  --format="value(deployedModels[1].id)")

# 2. Route all traffic back to old model
gcloud ai endpoints update $VERTEX_ENDPOINT_ID \
  --region=$REGION --project=$PROJECT_ID \
  --traffic-split="$OLD_MODEL_ID=100,$NEW_MODEL_ID=0"

# 3. Confirm
gcloud ai endpoints describe $VERTEX_ENDPOINT_ID \
  --region=$REGION --project=$PROJECT_ID \
  --format="table(deployedModels[].id,deployedModels[].trafficSplit)"

# 4. Remove the bad model from the endpoint
gcloud ai endpoints undeploy-model $VERTEX_ENDPOINT_ID \
  --region=$REGION --project=$PROJECT_ID \
  --deployed-model-id=$NEW_MODEL_ID
```

### Prevention
- Never deploy a new model with > 20% traffic until 24h of shadow evaluation
- Gate deployments on `drug_f1 >= 0.87 AND json_validity >= 0.95`
- The KFP pipeline's eval step enforces these thresholds before calling `deploy_model`
- Keep the previous deployed model on the endpoint (0% traffic) for 48h before undeploying

---

## IR-07 — Model Regression After Retraining (Rollback Procedure)

### Symptom
- After a scheduled retraining run deployed a new model, quality metrics drop
- `drug_f1` drops below 0.85 threshold in the `mlops` dashboard
- Pharmacists report more hallucinations or wrong drug names
- The retraining pipeline may have passed eval (eval set was too small or unrepresentative)

### Likely Cause
1. Training data had mislabeled corrections (pharmacist errors propagated as ground truth)
2. Min-corrections threshold was too low — trained on insufficient data
3. Overfitting — training ran too many epochs on a small dataset
4. Drug dictionary `referances/drug_dict.json` changed and the model was not re-evaluated

### Diagnosis

```bash
# 1. Identify the regression — compare metrics timeline
bq query --project_id="$PROJECT_ID" --use_legacy_sql=false "
SELECT
  DATE(ts) AS date,
  AVG(CAST(JSON_EXTRACT_SCALAR(result_metadata, '$.confidence') AS FLOAT64)) AS avg_confidence,
  COUNTIF(CAST(JSON_EXTRACT_SCALAR(result_metadata, '$.json_valid') AS BOOL) = FALSE) AS json_failures,
  COUNT(*) AS total
FROM \`$PROJECT_ID.medibox.requests\`
WHERE DATE(ts) >= DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY)
GROUP BY 1
ORDER BY 1
"

# 2. Identify the offending model version
gcloud ai models list \
  --region=$REGION --project=$PROJECT_ID \
  --filter="displayName:medibox" \
  --format="table(name,displayName,createTime)"

# 3. Check Vertex Model Registry for eval metrics stored at upload time
gcloud ai models describe $MODEL_ID \
  --region=$REGION --project=$PROJECT_ID \
  --format="json" | python3 -c "
import sys, json
m = json.load(sys.stdin)
for label in m.get('labels', {}).items():
    print(label)
"
```

### Fix — Roll Back to Previous Model Version

```bash
# 1. List model versions in the registry
gcloud ai models list \
  --region=$REGION --project=$PROJECT_ID \
  --filter="displayName:medibox" \
  --format="table(name,displayName,createTime)" \
  --sort-by="~createTime"

# 2. Get the previous (known-good) model ID
GOOD_MODEL_ID="<PREVIOUS_MODEL_ID>"

# 3. Deploy the previous model to the endpoint
gcloud ai endpoints deploy-model $VERTEX_ENDPOINT_ID \
  --region=$REGION --project=$PROJECT_ID \
  --model=$GOOD_MODEL_ID \
  --display-name="medibox-vllm-rollback" \
  --machine-type=n1-standard-4 \
  --accelerator=type=NVIDIA_TESLA_T4,count=1 \
  --traffic-split=0=100

# 4. Undeploy the bad model
gcloud ai endpoints undeploy-model $VERTEX_ENDPOINT_ID \
  --region=$REGION --project=$PROJECT_ID \
  --deployed-model-id=$BAD_DEPLOYED_MODEL_ID

# 5. Also roll back the GCS "current" artifacts
gcloud storage cp \
  "gs://${PROJECT_ID}-models/lora-adapters/<GOOD_RUN>/" \
  "gs://${PROJECT_ID}-models/lora-adapters/current/" \
  --recursive

# 6. Update Vertex AI Model Registry label to reflect rollback
gcloud ai models update $GOOD_MODEL_ID \
  --region=$REGION --project=$PROJECT_ID \
  --update-labels="deployment_status=active,rollback=true"
```

### Prevention
- Eval set must be held-out (never included in any training run)
- KFP pipeline enforces: `drug_f1 >= 0.87`, `json_validity >= 0.95`, `rare_drug_accuracy >= 0.80`
- Retain previous model on endpoint at 0% for 48h (see IR-06)
- Audit `admin_role_grants` — only the pharmacist-in-charge can approve a retraining push
- Increase `MIN_TRAINING_CORRECTIONS=500` if regressions recur

---

## IR-08 — Cloud Run Cold Start Latency Spike

### Symptom
- API p95 latency alert fires (> 1000ms)
- The spike correlates with a period of zero traffic followed by a burst
- Requests that complete successfully take 3–8 seconds
- Warm requests (same instance, subsequent) return to < 200ms

### Likely Cause
1. `min-instances=0` on medibox-api (should be min 1 for latency-sensitive paths)
2. A recent deploy increased image size, extending cold start duration
3. Python import chain at startup is slow (loading heavy libraries at module level)

### Diagnosis

```bash
# 1. Check current min-instances setting
gcloud run services describe medibox-api \
  --region=$REGION --project=$PROJECT_ID \
  --format="value(spec.template.metadata.annotations)"

# 2. Measure cold vs warm latency from logs
gcloud logging read \
  'resource.type="cloud_run_revision"
   resource.label.service_name="medibox-api"
   labels."run.googleapis.com/startup_type"="cold"' \
  --project=$PROJECT_ID \
  --freshness=1h \
  --format="table(timestamp,labels.run.googleapis.com/startup_type,httpRequest.latency)"

# 3. Check current image size
gcloud artifacts docker images list \
  "$REGION-docker.pkg.dev/$PROJECT_ID/$REPO/medibox-api" \
  --include-tags \
  --format="table(image,tags,update_time,metadata.imageSizeBytes)" \
  --project=$PROJECT_ID \
  --limit=5
```

### Fix

**Immediate — set min-instances:**
```bash
gcloud run services update medibox-api \
  --region=$REGION --project=$PROJECT_ID \
  --min-instances=1
```

**If image size grew — investigate and reduce:**
```bash
# Check what's large in the image
docker pull "$REGION-docker.pkg.dev/$PROJECT_ID/$REPO/medibox-api:latest"
docker history "$REGION-docker.pkg.dev/$PROJECT_ID/$REPO/medibox-api:latest" \
  --no-trunc --format "{{.Size}}\t{{.CreatedBy}}"
```

**Reduce startup time — defer heavy imports:**
```python
# Instead of importing at module level:
# import torch  # 2-3 second import

# Use lazy import in the function body:
def process():
    import torch  # imported only when needed
```

### Prevention
- `min-instances=1` on medibox-api is the default in `03_first_deploy.sh`
- Use CPU startup boost: `--cpu-boost` flag (Cloud Run CPU always allocated during startup)
- Keep API image < 1GB; move ML dependencies to the worker image only

---

## IR-09 — Memorystore Connection Failures (VPC Connector Issue)

### Symptom
- Worker logs: `redis.exceptions.ConnectionError: Error connecting to Redis`
- Or Celery: `ERROR/MainProcess] consumer: Cannot connect to redis://...`
- API also fails if it uses Redis for caching
- The Memorystore instance itself is healthy (visible in GCP console)

### Likely Cause
1. Cloud Run VPC connector was deleted or misconfigured
2. VPC connector throughput limit reached (too many concurrent connections)
3. The VPC connector is in a different region from the Cloud Run service
4. Redis AUTH token rotated but Cloud Run was not updated with the new secret version

### Diagnosis

```bash
# 1. Check VPC connector exists and is ready
gcloud compute networks vpc-access connectors list \
  --region=$REGION --project=$PROJECT_ID

gcloud compute networks vpc-access connectors describe medibox-connector \
  --region=$REGION --project=$PROJECT_ID \
  --format="table(name,state,ipCidrRange,network,minThroughput,maxThroughput)"

# 2. Check Cloud Run is using the connector
gcloud run services describe medibox-worker \
  --region=$REGION --project=$PROJECT_ID \
  --format="value(spec.template.metadata.annotations)"
# Should show: run.googleapis.com/vpc-access-connector: medibox-connector
# And: run.googleapis.com/vpc-access-egress: private-ranges-only

# 3. Test connectivity from a GCE VM in the same VPC
# (If you have a bastion or Cloud Shell with VPC access)
REDIS_HOST=$(grep REDIS_HOST gcp_resources.txt | cut -d= -f2)
nc -zv "$REDIS_HOST" 6379

# 4. Check if AUTH token mismatch
gcloud secrets versions list medibox-redis-auth --project=$PROJECT_ID
# Check if Cloud Run is using the latest version
gcloud run services describe medibox-worker \
  --region=$REGION --project=$PROJECT_ID \
  --format=json | python3 -c "
import sys, json
spec = json.load(sys.stdin)
for env in spec['spec']['template']['spec']['containers'][0].get('env', []):
    if 'redis' in env.get('name','').lower():
        print(env)
"
```

### Fix

**If VPC connector is missing:**
```bash
# Recreate the VPC connector
gcloud compute networks vpc-access connectors create medibox-connector \
  --region=$REGION --project=$PROJECT_ID \
  --network=default \
  --range=10.8.0.0/28 \
  --min-throughput=200 \
  --max-throughput=1000

# Re-attach to both services
for SVC in medibox-api medibox-worker; do
  gcloud run services update $SVC \
    --region=$REGION --project=$PROJECT_ID \
    --vpc-connector=medibox-connector \
    --vpc-egress=private-ranges-only
done
```

**If throughput limit reached — scale up connector:**
```bash
gcloud compute networks vpc-access connectors update medibox-connector \
  --region=$REGION --project=$PROJECT_ID \
  --max-throughput=1000
```

**If AUTH token was rotated:**
```bash
# Update Redis AUTH in Secret Manager (new value)
echo -n "$(openssl rand -base64 32)" | \
  gcloud secrets versions add medibox-redis-auth \
    --project=$PROJECT_ID --data-file=-

# Cloud Run will pick up the new secret on next container restart
# Force a restart:
gcloud run services update medibox-worker \
  --region=$REGION --project=$PROJECT_ID \
  --set-env-vars="RESTART_TS=$(date +%s)"
```

### Prevention
- Terraform (or `01_setup_project.sh`) must create the VPC connector before deploying Cloud Run
- Cloud Run services must specify `--vpc-connector` at deploy time (already in `03_first_deploy.sh`)
- Monitor VPC connector state — add a `connector.googleapis.com/sent_bytes_count = 0` alert for sustained silence

---

## IR-10 — Budget Alert Triggered

### Symptom
- Email notification: "Google Cloud budget alert: Medibox monthly spend has reached 50% / 90% / 100% of the $500 budget"
- No service impact yet (budget alerts are advisory — Google does not auto-terminate services)

### What to Scale Down First

Ranked by monthly cost, highest to lowest impact:

| Priority | Resource | Monthly Cost | Action |
|----------|----------|-------------|--------|
| 1 | Vertex Endpoint (T4 GPU, 24/7) | ~$252 | Pause endpoint when no pharmacies active (can't scale to zero) |
| 2 | Cloud SQL db-f1-micro | ~$10–25 | No action (minimum viable) |
| 3 | Memorystore Basic 1GB | ~$25 | No action (cannot easily reduce) |
| 4 | Cloud Run — API + Worker | ~$15–40 | Scale min-instances to 0 for API; Worker already min=1 |
| 5 | Vertex AI Training (A100) | ~$15–18/run | Pause scheduled retraining |

### Diagnosis

```bash
# 1. Check current spend by service
gcloud billing accounts get-spend-information $BILLING_ACCOUNT
# Or in BigQuery if billing export is enabled:
bq query --project_id="$PROJECT_ID" --use_legacy_sql=false "
SELECT
  service.description AS service,
  SUM(cost) AS cost_usd,
  SUM(usage.amount) AS usage_amount,
  usage.unit
FROM \`$PROJECT_ID.billing_export.gcp_billing_export_v1_*\`
WHERE DATE(usage_start_time) >= DATE_TRUNC(CURRENT_DATE(), MONTH)
GROUP BY 1, 4
ORDER BY 2 DESC
LIMIT 20
"

# 2. Check Vertex usage specifically (biggest line item)
gcloud monitoring read \
  'metric.type="aiplatform.googleapis.com/prediction/online/prediction_count"' \
  --project=$PROJECT_ID \
  --freshness=720h  # 30 days
```

### Fix

**Pause Vertex endpoint (reduces bill by ~$252/month):**
```bash
# Undeploy model from endpoint (endpoint itself is free; deployed model is not)
DEPLOYED_MODEL_ID=$(gcloud ai endpoints describe $VERTEX_ENDPOINT_ID \
  --region=$REGION --project=$PROJECT_ID \
  --format="value(deployedModels[0].id)")

gcloud ai endpoints undeploy-model $VERTEX_ENDPOINT_ID \
  --region=$REGION --project=$PROJECT_ID \
  --deployed-model-id=$DEPLOYED_MODEL_ID

# Update worker to use local fallback while Vertex is paused
gcloud run services update medibox-worker \
  --region=$REGION --project=$PROJECT_ID \
  --set-env-vars="LOCAL_FALLBACK_ENABLED=true"
```

**Cancel any running training jobs:**
```bash
gcloud ai pipeline-jobs list \
  --region=$REGION --project=$PROJECT_ID \
  --filter="state=PIPELINE_STATE_RUNNING" \
  --format="value(name)" | \
  xargs -I{} gcloud ai pipeline-jobs cancel {} --region=$REGION --project=$PROJECT_ID
```

**Set lower Cloud Run max-instances:**
```bash
gcloud run services update medibox-api \
  --region=$REGION --project=$PROJECT_ID \
  --min-instances=0 --max-instances=2
```

### Prevention
- Budget alert set at $400 (80% of $500 budget) — fires before crisis
- Vertex endpoint is the single largest cost — re-evaluate if usage does not justify 24/7 allocation
- See `COSTS.md` for full cost breakdown and optimization options

---

## IR-11 — Firebase JWT Validation Failures Spike

### Symptom
- API log-based metric `auth_failures` spikes
- Users are getting `401 Unauthorized` on all requests including previously-working sessions
- The Firebase Authentication tab shows normal sign-in activity (no spike in failed logins)
- Could affect all pharmacies simultaneously (systemic — not per-user)

### Likely Cause
1. Firebase Admin SDK JSON key in Secret Manager expired or was rotated without updating the secret
2. Clock skew between Cloud Run container and NTP server (JWT `iat`/`exp` validation fails)
3. Firebase project was changed or the `FIREBASE_PROJECT_ID` env var is wrong
4. Google's JWKS (JSON Web Key Set) endpoint for Firebase was temporarily unreachable, and the SDK's in-memory cache expired

### Diagnosis

```bash
# 1. Check auth failure rate
gcloud logging read \
  'resource.type="cloud_run_revision"
   resource.label.service_name="medibox-api"
   jsonPayload.message="Firebase token verification failed"' \
  --project=$PROJECT_ID \
  --freshness=30m \
  --limit=20

# 2. Is it all users or specific users?
# All users = systemic (SDK config issue)
# Specific users = account-level issue (not this runbook)

# 3. Test Firebase Admin SDK directly
# Get the secret
gcloud secrets versions access latest \
  --secret=medibox-firebase-admin-json \
  --project=$PROJECT_ID > /tmp/firebase-test.json

python3 - <<'EOF'
import firebase_admin
from firebase_admin import credentials, auth
cred = credentials.Certificate("/tmp/firebase-test.json")
app = firebase_admin.initialize_app(cred)
print("Firebase Admin SDK initialized successfully")
# Get a test token from a real user (or skip)
EOF

rm /tmp/firebase-test.json

# 4. Check if the service account key in Secret Manager is valid
gcloud iam service-accounts keys list \
  --iam-account="firebase-adminsdk-*@$FIREBASE_PROJECT_ID.iam.gserviceaccount.com" \
  --project=$FIREBASE_PROJECT_ID
```

### Fix

**If Admin SDK JSON is expired:**
```bash
# 1. Generate a new key from Firebase Console
# Firebase Console → Project Settings → Service Accounts → Generate new private key
# Download JSON

# 2. Update Secret Manager
gcloud secrets versions add medibox-firebase-admin-json \
  --project=$PROJECT_ID \
  --data-file=/path/to/new-firebase-adminsdk.json

# 3. Force Cloud Run restart to pick up new secret
gcloud run services update medibox-api \
  --region=$REGION --project=$PROJECT_ID \
  --set-env-vars="RESTART_TS=$(date +%s)"
```

**If JWKS endpoint was unreachable (transient):**
The Firebase Admin SDK automatically retries JWKS fetches. If this was transient,
a Cloud Run instance restart should resolve it:
```bash
gcloud run services update medibox-api \
  --region=$REGION --project=$PROJECT_ID \
  --set-env-vars="RESTART_TS=$(date +%s)"
```

**Emergency bypass (maintenance mode only — NOT for production use):**
```bash
# Enable maintenance mode to show a friendly message instead of 401s
gcloud run services update medibox-api \
  --region=$REGION --project=$PROJECT_ID \
  --set-env-vars="MAINTENANCE_MODE=true,MAINTENANCE_REASON=Authentication service temporarily unavailable"
```

### Prevention
- Firebase Admin SDK JSON service account key does not expire (unlike OAuth tokens) — but can be revoked
- Keep a backup Firebase Admin SDK JSON in a second secret version
- Alert if `auth_failures` rate > 10/min for 2 min (add to `alert_policies.json`)
- Never delete all service account keys for the Firebase Admin SDK SA without first creating a replacement

---

## IR-12 — IAM Permission Denied on Service-to-Service Call

### Symptom
- Worker logs: `403 PERMISSION_DENIED` or `iam.serviceAccounts.actAs` error
- API logs: `403 The caller does not have permission`
- Typically appears after a deploy, a Secret Manager rotation, or a manual IAM change
- The error message usually names the SA (`medibox-runner@...`, `medibox-worker@...`) and the resource

### Likely Cause
1. A service account was inadvertently deleted and recreated (new SA has same name but different numeric ID — all IAM bindings were lost)
2. A `gcloud iam remove-iam-policy-binding` was run against the wrong project/resource
3. The Cloud Run SA was changed to a different SA that lacks the required roles
4. Organization policy added a constraint that removed a previously-allowed permission

### Diagnosis

```bash
# 1. Read the exact error from logs
gcloud logging read \
  'severity=ERROR
   (resource.type="cloud_run_revision" OR resource.type="aiplatform.googleapis.com/Endpoint")
   textPayload:"PERMISSION_DENIED"' \
  --project=$PROJECT_ID \
  --freshness=30m \
  --limit=10

# Example error output:
# "Permission 'secretmanager.versions.access' denied on resource
#  'projects/.../secrets/medibox-db-password/versions/latest'
#  for service account 'medibox-runner@PROJECT_ID.iam.gserviceaccount.com'"

# 2. Check what roles the SA currently has
SA="medibox-runner@$PROJECT_ID.iam.gserviceaccount.com"

gcloud projects get-iam-policy $PROJECT_ID \
  --flatten="bindings[].members" \
  --filter="bindings.members:$SA" \
  --format="table(bindings.role)"

# 3. Check SA still exists (it might have been deleted and recreated)
gcloud iam service-accounts describe $SA --project=$PROJECT_ID

# 4. Check Secret Manager permissions specifically
gcloud secrets get-iam-policy medibox-db-password --project=$PROJECT_ID

# 5. Check Vertex AI permissions (if the error is in the worker calling Vertex)
gcloud projects get-iam-policy $PROJECT_ID \
  --flatten="bindings[].members" \
  --filter="bindings.members:serviceAccount:medibox-worker@$PROJECT_ID.iam.gserviceaccount.com" \
  --format="table(bindings.role)"
```

### Fix

**Re-apply the missing role binding:**
```bash
# The required roles for each SA are documented in 01_setup_project.sh.
# Example: medibox-runner needs Secret Manager access

# Project-level Secret Manager access
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:medibox-runner@$PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"

# Per-secret access (if using fine-grained — 01_setup_project.sh uses this)
for SECRET in medibox-db-password medibox-redis-auth medibox-jwt-signing-key \
              medibox-pii-encryption-key medibox-firebase-admin-json \
              medibox-database-url medibox-database-url-sync; do
  gcloud secrets add-iam-policy-binding $SECRET \
    --project=$PROJECT_ID \
    --member="serviceAccount:medibox-runner@$PROJECT_ID.iam.gserviceaccount.com" \
    --role="roles/secretmanager.secretAccessor"
done

# Cloud SQL client role (for Cloud SQL Auth Proxy connections)
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:medibox-runner@$PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/cloudsql.client"

# Vertex AI user (worker SA only — NOT medibox-runner)
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:medibox-worker@$PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/aiplatform.user"
```

**If SA was deleted and recreated — re-bind to Cloud Run service:**
```bash
# Cloud Run must be told which SA to use (it may have fallen back to default Compute SA)
gcloud run services update medibox-api \
  --region=$REGION --project=$PROJECT_ID \
  --service-account="medibox-runner@$PROJECT_ID.iam.gserviceaccount.com"

gcloud run services update medibox-worker \
  --region=$REGION --project=$PROJECT_ID \
  --service-account="medibox-worker@$PROJECT_ID.iam.gserviceaccount.com"
```

**Verify fix:**
```bash
# Test Secret Manager access directly
gcloud secrets versions access latest \
  --secret=medibox-db-password \
  --project=$PROJECT_ID \
  --impersonate-service-account="medibox-runner@$PROJECT_ID.iam.gserviceaccount.com"
```

### Prevention
- All IAM bindings are in `01_setup_project.sh` — re-running it with `--skip-existing` will re-apply any missing bindings
- Never delete a SA without re-creating it and re-running all IAM bindings
- Audit log: all `SetIamPolicy` calls are logged; set an alert if IAM bindings change unexpectedly
- Use `gcloud projects get-iam-policy` output as a reference baseline; compare monthly

---

## Appendix: Common Diagnosis Commands

```bash
# Tail live API logs
gcloud logging tail \
  'resource.type="cloud_run_revision" resource.label.service_name="medibox-api"' \
  --project=$PROJECT_ID

# Tail live worker logs
gcloud logging tail \
  'resource.type="cloud_run_revision" resource.label.service_name="medibox-worker"' \
  --project=$PROJECT_ID

# Get all recent errors across all Cloud Run services
gcloud logging read \
  'resource.type="cloud_run_revision" severity>=ERROR' \
  --project=$PROJECT_ID --freshness=1h --limit=50

# Check all Cloud Run service statuses
for SVC in medibox-api medibox-worker medibox-frontend; do
  echo "=== $SVC ==="
  gcloud run services describe $SVC \
    --region=$REGION --project=$PROJECT_ID \
    --format="value(status.conditions[0].type,status.conditions[0].status)"
done

# Check current instance counts
gcloud monitoring read \
  'metric.type="run.googleapis.com/container/instance_count"' \
  --project=$PROJECT_ID --freshness=5m

# View active Cloud SQL connections
# (From Cloud SQL Auth Proxy session)
psql -h 127.0.0.1 -U medibox medibox -c \
  "SELECT COUNT(*), state FROM pg_stat_activity GROUP BY state;"
```
