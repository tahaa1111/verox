# Runbook — Medibox Oncall

## Contacts

| Role | Contact |
|------|---------|
| On-call | Check PagerDuty / email alert |
| GCP Project | Cloud Console → IAM |

---

## Setup Script — Idempotency Guarantee

`scripts/01_setup_project.sh` is **fully idempotent** — safe to re-run at any time against an already-set-up project with zero errors.

Every resource-creation step is implemented as an `ensure_<resource>()` function that follows this contract:

1. **Check first.** Call the appropriate `gcloud … describe` / `bq show` / `gcloud storage buckets describe` command.
2. **Skip if found.** If the resource exists, print `✔ <resource> already exists` and return immediately.
3. **Create only if missing.** The `gcloud … create` command is never reached when the resource is already present.

This means:
- A partial failure on first run leaves all already-created resources intact. Re-running picks up exactly where it left off.
- Running the script against a fully provisioned project is a no-op (only IAM binding calls are repeated, which GCP handles idempotently).
- The `--skip-existing` flag is no longer needed and is accepted only as a no-op for backward compatibility.

**When to re-run the setup script:**
- After a partial failure during first deploy
- After accidentally deleting a resource (VPC connector, bucket, etc.)
- To re-apply IAM bindings after a service account was deleted and recreated
- After adding a new service account or GCP resource to the script

```bash
# Always source .env.deploy first
set -a && source .env.deploy && set +a
bash scripts/01_setup_project.sh
```

---

## Edge Authentication — Keyfile is the default

`01_setup_project.sh` creates a service account keyfile (`medibox-edge-key.json`) for the Pi. This is the **default and recommended** path for initial deployment. Copy it to the Pi:

```bash
scp ./medibox-edge-key.json pi@<EDGE_DEVICE_IP>:/etc/medibox/medibox-edge.json
chmod 600 /etc/medibox/medibox-edge.json   # run on the Pi
```

Workload Identity Federation (no key on device) is a **v2 hardening option** available after the deployment is stable. Run `scripts/05a_setup_wif_optional.sh` when ready.

---

## Known Issue — WIF IAM Propagation Race Condition

**Symptom:**
```
ERROR: (gcloud.iam.service-accounts.add-iam-policy-binding) INVALID_ARGUMENT:
Identity Pool does not exist (projects/.../workloadIdentityPools/medibox-wif-pool)
```
This error occurs even though the pool was just created successfully.

**Cause:** GCP's IAM service has eventual consistency for Workload Identity Pool resources. The pool creation API call returns success but the IAM binding service may not see the pool for up to 60 seconds afterward.

**This only affects `05a_setup_wif_optional.sh`**, not the main `01_setup_project.sh` (which no longer touches WIF).

**Workaround:** `05a_setup_wif_optional.sh` automatically retries the binding up to 5 times with exponential backoff. If all retries fail, wait 60 seconds and re-run the script — the pool creation step is idempotent and will be skipped, going directly to the binding retry.

```bash
# Wait for propagation, then retry
sleep 60
bash scripts/05a_setup_wif_optional.sh
```

---

## Alert Response Procedures

### API p95 Latency > 1s

1. Check Cloud Run API instance count — if 0, a cold start may be in progress.
2. Check Vertex AI endpoint latency (System Overview dashboard).
3. Check Celery queue depth (Redis metrics).
4. Check Cloud SQL active connections — if near 25, reduce max-instances:
   ```bash
   gcloud run services update medibox-api --max-instances=3 --region=us-central1
   ```
5. Check for a recent Cloud Build deploy that may have introduced a regression.

---

### API Error Rate > 2%

1. Check Cloud Run logs:
   ```bash
   gcloud logging read 'resource.type="cloud_run_revision" severity>=ERROR' \
     --project=$GCP_PROJECT_ID --limit=50
   ```
2. Check Cloud SQL connectivity — if the Auth Proxy sidecar is failing, restart the service revision.
3. Verify Vertex AI endpoint is healthy: `curl $VERTEX_ENDPOINT/health` (from worker).
4. If a new deploy is causing the errors, roll back:
   ```bash
   gcloud run services update-traffic medibox-api \
     --to-revisions=PREVIOUS_REVISION=100 --region=us-central1
   ```

---

### Vertex AI GPU Duty Cycle > 90%

1. Check if a canary or shadow eval is running concurrently with production traffic.
2. Verify the number of deployed replicas:
   ```bash
   gcloud ai endpoints describe $VERTEX_ENDPOINT_ID --region=us-central1
   ```
3. If load is legitimate, scale up replicas (adds ~$252/month per replica):
   ```bash
   gcloud ai endpoints deploy-model $VERTEX_ENDPOINT_ID \
     --region=us-central1 --model=$MODEL_ID \
     --min-replica-count=2 --max-replica-count=3 \
     --machine-type=n1-standard-4 \
     --accelerator=type=NVIDIA_TESLA_T4,count=1
   ```

---

### Cloud SQL Active Connections > 20

**Critical — db-f1-micro max is 25 connections.**

1. Immediately reduce Cloud Run max-instances:
   ```bash
   gcloud run services update medibox-api --max-instances=3 --region=us-central1
   gcloud run services update medibox-worker --max-instances=3 --region=us-central1
   ```
2. Check for connection leaks:
   ```bash
   # Connect via Cloud SQL Auth Proxy, then:
   SELECT pid, state, wait_event_type, query_start, LEFT(query, 100)
   FROM pg_stat_activity WHERE datname = 'medibox';
   ```
3. Kill idle connections if needed:
   ```sql
   SELECT pg_terminate_backend(pid) FROM pg_stat_activity
   WHERE state = 'idle' AND query_start < NOW() - INTERVAL '5 minutes';
   ```

---

### Hallucination Rate Spike

1. Check if a new model version was promoted recently (MLOps dashboard).
2. Compare drug_f1 and rare_drug_accuracy eval metrics.
3. If model regression confirmed, roll back immediately:
   ```bash
   curl -X POST "$API_URL/v1/admin/model/rollback" \
     -H "Authorization: Bearer $ADMIN_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"deployed_model_id": "PREVIOUS_DEPLOYED_MODEL_ID"}'
   ```
4. File an incident and review the canary watch period — consider extending `canary_watch_minutes`.

---

### Vertex Endpoint Unreachable

1. Check endpoint status:
   ```bash
   gcloud ai endpoints describe $VERTEX_ENDPOINT_ID --region=us-central1
   ```
2. Check container logs for model download or startup failures:
   ```bash
   gcloud logging read 'resource.type="aiplatform.googleapis.com/Endpoint"' \
     --project=$GCP_PROJECT_ID --limit=20
   ```
3. If the endpoint is in error state, redeploy:
   ```bash
   bash scripts/03_deploy_vllm_endpoint.sh
   ```
4. If GCS model weights are missing, re-download (check `_MODEL_GCS` in cloudbuild-vllm.yaml).

---

## Maintenance Mode

Enable to block new prescription submissions (e.g., before a database migration):

```bash
# Via API
curl -X POST "$API_URL/v1/admin/maintenance" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"active": true, "reason": "Scheduled database migration"}'

# Via Redis directly (emergency)
redis-cli SET medibox:maintenance '{"active": true, "reason": "Emergency"}'
```

Disable:
```bash
curl -X POST "$API_URL/v1/admin/maintenance" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"active": false, "reason": ""}'
```

---

## Cost Emergency (Billing > $500)

The billing budget Pub/Sub trigger should auto-pause the Vertex endpoint.
If not:

```bash
bash scripts/pause_endpoint.sh
```

This undeploys all models from the Vertex endpoint. Resume when ready:

```bash
bash scripts/resume_endpoint.sh
```

**Note:** Pausing takes effect immediately but resume takes ~5 minutes.

---

## Monthly Retraining Pipeline

The KFP pipeline runs automatically every 28 days via Cloud Scheduler.

Manual trigger:
```bash
python pipelines/monthly_qlora_retrain.py  # compiles YAML
gcloud ai pipelines run --pipeline-job=pipelines/monthly_qlora_retrain.yaml \
  --region=us-central1 --project=$GCP_PROJECT_ID
```

Pipeline requirements: ≥200 pharmacist corrections since last run (configurable via `min_corrections`).

---

## Database Backup and Restore

Cloud SQL automated backups are enabled. To restore:

```bash
# List backups
gcloud sql backups list --instance=medibox-postgres

# Restore to a specific backup
gcloud sql backups restore BACKUP_ID --restore-instance=medibox-postgres
```

---

## Useful Commands

```bash
# Tail API logs
gcloud logging tail 'resource.type="cloud_run_revision" resource.labels.service_name="medibox-api"'

# Check Cloud Run service status
gcloud run services list --region=us-central1

# Check Vertex AI endpoint
gcloud ai endpoints list --region=us-central1

# Run a single evaluation against the current model
python -m src.training.evaluate \
  --eval_data_uri=gs://$GCP_PROJECT_ID-models/eval-data/latest.jsonl \
  --adapter_uri=gs://$GCP_PROJECT_ID-models/lora-adapters/latest/ \
  --output_metrics_path=/tmp/metrics.json
```
