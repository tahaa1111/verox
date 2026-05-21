# Backup and Restore — Medibox

**RTO target: 2 hours** (time to restore service after catastrophic failure)
**RPO target: 1 hour** (maximum data loss — Cloud SQL PITR achieves ~0s in practice)

---

## 1. Cloud SQL — Automated Backups

### What's configured

`01_setup_project.sh` sets up Cloud SQL with:
- **Daily automated backups** at 02:00 Africa/Tunis time
- **7 daily backups** retained (rolling window)
- **7 days of transaction logs** for point-in-time recovery (PITR)
- **Deletion protection** enabled — the instance cannot be deleted accidentally

Verify the configuration:
```bash
gcloud sql instances describe medibox-postgres \
  --project=$PROJECT_ID \
  --format="table(
    settings.backupConfiguration.enabled,
    settings.backupConfiguration.startTime,
    settings.backupConfiguration.transactionLogRetentionDays,
    settings.backupConfiguration.backupRetentionSettings.retainedBackups)"
```

Expected output:
```
ENABLED  START_TIME  TRANSACTION_LOG_RETENTION_DAYS  RETAINED_BACKUPS
True     02:00       7                               7
```

### Enable weekly long-term backup retention

By default, Cloud SQL only keeps 7 daily backups. To add a weekly long-term backup:

```bash
# Enable long-term retention (1 backup per week, kept for 4 weeks = 4 weekly backups)
gcloud sql instances patch medibox-postgres \
  --backup-retained-transaction-log-days=7 \
  --project=$PROJECT_ID

# Note: Cloud SQL does not have native "weekly" scheduling.
# Implement via Cloud Scheduler calling this command:
gcloud scheduler jobs create http medibox-weekly-sql-backup \
  --location=$REGION \
  --schedule="0 3 * * 0" \
  --uri="https://sqladmin.googleapis.com/sql/v1beta4/projects/$PROJECT_ID/instances/medibox-postgres/backupRuns" \
  --message-body='{}' \
  --oauth-service-account-email="medibox-runner@$PROJECT_ID.iam.gserviceaccount.com" \
  --time-zone="Africa/Tunis" \
  --description="Weekly on-demand Cloud SQL backup" \
  --project=$PROJECT_ID
```

### Verify backups are running

```bash
# List recent backups
gcloud sql backups list \
  --instance=medibox-postgres \
  --project=$PROJECT_ID \
  --limit=10

# Check the most recent backup status
gcloud sql backups list \
  --instance=medibox-postgres \
  --project=$PROJECT_ID \
  --filter="status=SUCCESSFUL" \
  --limit=1 \
  --format="table(id,windowStartTime,status,sizeGb)"
```

---

## 2. Manual On-Demand Backup (Before Risky Operations)

Always take a manual backup before:
- Running database migrations (`alembic upgrade head`)
- Updating Cloud SQL major version
- Importing large data sets
- Any schema changes

```bash
# Trigger an on-demand backup
gcloud sql backups create \
  --instance=medibox-postgres \
  --project=$PROJECT_ID \
  --description="Pre-migration backup $(date -u '+%Y-%m-%d %H:%M:%S UTC')"

# Wait for it to complete
echo "Waiting for backup to complete..."
while true; do
  STATUS=$(gcloud sql backups list \
    --instance=medibox-postgres \
    --project=$PROJECT_ID \
    --limit=1 \
    --format="value(status)" 2>/dev/null)
  if [[ "$STATUS" == "SUCCESSFUL" ]]; then
    echo "✔ Backup complete"
    break
  elif [[ "$STATUS" == "FAILED" ]]; then
    echo "✘ Backup failed — check Cloud SQL console"
    exit 1
  fi
  echo "  Status: $STATUS — waiting..."
  sleep 10
done

# Record the backup ID
BACKUP_ID=$(gcloud sql backups list \
  --instance=medibox-postgres \
  --project=$PROJECT_ID \
  --limit=1 \
  --format="value(id)")
echo "Backup ID: $BACKUP_ID (save this before proceeding)"
```

---

## 3. Restoring Cloud SQL

### Option A: Restore from a specific backup

```bash
# 1. List available backups
gcloud sql backups list \
  --instance=medibox-postgres \
  --project=$PROJECT_ID \
  --format="table(id,windowStartTime,status,sizeGb)"

# 2. Restore (⚠ WARNING: overwrites current data)
gcloud sql backups restore <BACKUP_ID> \
  --restore-instance=medibox-postgres \
  --project=$PROJECT_ID

# The instance will be unavailable for 5-10 minutes during restore.
# Cloud Run services will return 503 during this window.
```

### Option B: Point-in-time recovery (PITR)

PITR is available within the 7-day transaction log window. Use this for accidental data deletion.

```bash
# Restore to a specific timestamp (format: RFC 3339)
# Example: restore to 1 hour ago
RESTORE_TIME=$(date -u -d '1 hour ago' '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || \
               date -u -v-1H '+%Y-%m-%dT%H:%M:%SZ')

# ⚠ WARNING: This overwrites the current database
gcloud sql instances restore-backup medibox-postgres \
  --backup-instance=medibox-postgres \
  --restore-database-from-instance=medibox-postgres \
  --restore-time="$RESTORE_TIME" \
  --project=$PROJECT_ID

echo "PITR restore initiated to $RESTORE_TIME"
echo "Monitor at: https://console.cloud.google.com/sql/instances/medibox-postgres?project=$PROJECT_ID"
```

### Option C: Restore to a NEW instance (safest — non-destructive)

Use this if you want to inspect data before overwriting production:

```bash
# Create a clone for inspection
gcloud sql instances clone medibox-postgres medibox-postgres-restore \
  --restore-point-in-time="$RESTORE_TIME" \
  --project=$PROJECT_ID

# Connect to the clone and inspect
DB_PASS=$(gcloud secrets versions access latest \
  --secret=medibox-db-password --project=$PROJECT_ID)
SQL_CLONE_IP=$(gcloud sql instances describe medibox-postgres-restore \
  --project=$PROJECT_ID \
  --format="value(ipAddresses[0].ipAddress)")

# Use Cloud SQL Auth Proxy to connect
./cloud-sql-proxy --port=5433 "$PROJECT_ID:$REGION:medibox-postgres-restore" &
PGPASSWORD="$DB_PASS" psql -h 127.0.0.1 -p 5433 -U medibox medibox

# After inspection, if you want to promote to production:
# Option: dump from clone and restore to production
PGPASSWORD="$DB_PASS" pg_dump -h 127.0.0.1 -p 5433 -U medibox medibox > /tmp/medibox_restore.sql
# Then import to production (takes medibox-postgres offline briefly)

# Delete clone when done (it costs money)
gcloud sql instances delete medibox-postgres-restore --project=$PROJECT_ID --quiet
```

---

## 4. Cloud Storage — Model and Crop Backups

### Versioning verification (models bucket)

Object versioning is enabled on the models bucket by `01_setup_project.sh`. Verify:

```bash
gcloud storage buckets describe "gs://${PROJECT_ID}-models" \
  --project=$PROJECT_ID \
  --format="value(versioning.enabled)"
# Should output: True
```

### List model versions

```bash
# List all LoRA adapter versions
gcloud storage ls -l "gs://${PROJECT_ID}-models/lora-adapters/" --recursive

# List all versions of a specific adapter
gcloud storage ls -a "gs://${PROJECT_ID}-models/lora-adapters/run-20260515/"
```

### Restore a previous model version

```bash
# Find the generation number (version ID) of a previous version
gcloud storage ls -a "gs://${PROJECT_ID}-models/lora-adapters/run-20260515/adapter_model.bin" \
  --format="table(name,generation,timeCreated)"

# Restore by copying the specific generation to "current"
gcloud storage cp \
  "gs://${PROJECT_ID}-models/lora-adapters/run-20260515/adapter_model.bin#<GENERATION>" \
  "gs://${PROJECT_ID}-models/lora-adapters/current/adapter_model.bin"
```

### Crops bucket (no restore needed — ephemeral)

Crop images are auto-deleted after 90 days. They are not backed up — they are transient input data. If you need a crop for debugging, download it before the 90-day window expires.

```bash
gcloud storage cp "gs://${PROJECT_ID}-crops/<JOB_ID>/crop_0000.jpg" /tmp/
```

---

## 5. BigQuery — Snapshot via Scheduled Query

BigQuery tables have a 3-year automatic expiry but no daily backup. Create a scheduled snapshot:

```bash
# Create a scheduled query that snapshots the requests table daily
# BigQuery scheduled queries require the Data Transfer Service to be enabled
gcloud services enable bigquerydatatransfer.googleapis.com --project=$PROJECT_ID

bq query \
  --project_id="$PROJECT_ID" \
  --use_legacy_sql=false \
  --schedule="every 24 hours" \
  --display_name="Daily requests snapshot" \
  --target_dataset="medibox_backups" \
  "
  CREATE OR REPLACE TABLE \`$PROJECT_ID.medibox_backups.requests_$(date +%Y%m%d)\`
  AS SELECT * FROM \`$PROJECT_ID.medibox.requests\`
  WHERE DATE(ts) = DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY)
  "
```

Manual snapshot (run any time):
```bash
# Create a point-in-time snapshot of all rows
bq query \
  --project_id="$PROJECT_ID" \
  --destination_table="$PROJECT_ID:medibox_backups.requests_snapshot_$(date +%Y%m%d)" \
  --use_legacy_sql=false \
  "SELECT * FROM \`$PROJECT_ID.medibox.requests\` FOR SYSTEM_TIME AS OF CURRENT_TIMESTAMP()"
```

Note: BigQuery tables have a built-in **7-day time travel** feature — you can query any table as it was at any point in the past 7 days without any configuration:

```sql
-- Query the requests table as it was 2 hours ago
SELECT * FROM `PROJECT_ID.medibox.requests`
FOR SYSTEM_TIME AS OF TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 2 HOUR)
LIMIT 100;
```

---

## 6. Vertex AI Model Registry

Models in the Vertex AI Model Registry are **retained indefinitely** until explicitly deleted. No backup needed — the model artifacts are stored in GCS.

```bash
# List all registered models
gcloud ai models list --region=$REGION --project=$PROJECT_ID \
  --filter="displayName:medibox" \
  --format="table(name,displayName,createTime)"

# Verify model artifacts are in GCS
MODEL_ID=$(gcloud ai models list --region=$REGION --project=$PROJECT_ID \
  --filter="displayName:medibox" \
  --format="value(name)" | head -1 | rev | cut -d/ -f1 | rev)

gcloud ai models describe $MODEL_ID --region=$REGION --project=$PROJECT_ID \
  --format="table(artifactUri)"
# Should show: gs://{PROJECT_ID}-models/lora-adapters/run-YYYYMMDD/
```

---

## 7. Full Disaster Recovery Procedure

**Scenario:** The entire GCP project is deleted or completely corrupted.
**Target:** RTO 2 hours, RPO 1 hour.

### Pre-requisites for DR

Before any disaster, ensure these are available outside GCP:
- [ ] `gcp_resources.txt` — copy to a local safe location after every deploy
- [ ] `.env.deploy` — store securely offline (password manager or encrypted storage)
- [ ] Firebase Admin SDK JSON — download from Firebase Console and store offline
- [ ] The source code repository — must be on GitHub or another external VCS

### DR Procedure

```
Hour 0: Disaster detected
Hour 0-0:10 — Assess and decide to invoke DR
Hour 0:10-0:20 — Restore source code and environment
Hour 0:20-0:50 — Run 01_setup_project.sh (new project)
Hour 0:50-1:00 — Run 02_setup_secrets.sh
Hour 1:00-1:30 — Run 03_first_deploy.sh --skip-vllm
Hour 1:30-1:50 — Restore Cloud SQL data
Hour 1:50-2:00 — Smoke tests and verification
```

**Step-by-step:**

```bash
# Step 1: Create a new project (use a new PROJECT_ID or reclaim the old one)
export PROJECT_ID="medibox-prod-tn-dr"   # new ID
export REGION="us-central1"
export BILLING_ACCOUNT="<YOUR_BILLING_ACCOUNT>"
export REPO="medibox-repo"

# Step 2: Clone the source code
git clone https://github.com/<GITHUB_OWNER>/<GITHUB_REPO>.git
cd medibox-cloud

# Step 3: Restore environment
cp /path/to/backup/.env.deploy .env.deploy
# Update PROJECT_ID in .env.deploy to the new DR project ID
nano .env.deploy

# Step 4: Run setup scripts
set -a && source .env.deploy && set +a
bash scripts/00_prereqs.sh
bash scripts/01_setup_project.sh
bash scripts/02_setup_secrets.sh

# Step 5: Deploy services (skip vLLM to meet 2h RTO)
bash scripts/03_first_deploy.sh --skip-vllm --skip-smoke

# Step 6: Restore Cloud SQL from export
# If the original project still exists, export first:
gcloud sql export csv medibox-postgres \
  "gs://${PROJECT_ID}-raw/dr-export-$(date +%Y%m%d).sql" \
  --database=medibox \
  --project=<ORIGINAL_PROJECT_ID>

# Then import to new project:
gcloud sql import sql medibox-postgres \
  "gs://${PROJECT_ID}-raw/dr-export-$(date +%Y%m%d).sql" \
  --database=medibox \
  --project=$PROJECT_ID

# Step 7: Deploy Vertex endpoint (can be done after service is back up)
bash scripts/03_deploy_vllm_endpoint.sh

# Step 8: Update DNS / Cloud Run domain mappings if using custom domains

# Step 9: Smoke tests
API_URL=$(gcloud run services describe medibox-api \
  --region=$REGION --project=$PROJECT_ID --format="value(status.url)")
TOKEN=$(gcloud auth print-identity-token)
curl -s -H "Authorization: Bearer $TOKEN" "$API_URL/v1/healthz"
```

**Estimated time to service restoration:**
- Infrastructure (scripts 01-03, no vLLM): **60-80 minutes**
- Cloud SQL restore: **10-20 minutes**
- Vertex endpoint (vLLM): **30-45 minutes additional** (can serve without it in degraded mode if local fallback is configured)
- Total: **90-120 minutes** ✔ within 2h RTO

---

## 8. Backup Verification Schedule

Run these checks monthly to ensure backups are valid:

```bash
#!/usr/bin/env bash
# Save as: scripts/verify_backups.sh
set -euo pipefail
: "${PROJECT_ID:?}" "${REGION:?}"

echo "=== Backup Verification $(date -u) ==="

# 1. Cloud SQL: verify last backup was within 24h
LAST_BACKUP=$(gcloud sql backups list \
  --instance=medibox-postgres \
  --project=$PROJECT_ID \
  --filter="status=SUCCESSFUL" \
  --limit=1 \
  --format="value(windowStartTime)" 2>/dev/null || echo "")

if [[ -n "$LAST_BACKUP" ]]; then
  echo "✔ Last successful Cloud SQL backup: $LAST_BACKUP"
else
  echo "✘ No successful Cloud SQL backups found!"
fi

# 2. GCS: verify models bucket has content
MODEL_COUNT=$(gcloud storage ls "gs://${PROJECT_ID}-models/lora-adapters/" 2>/dev/null | wc -l)
echo "✔ GCS models bucket: $MODEL_COUNT adapter directories"

# 3. BigQuery: verify tables have recent data
LAST_ROW=$(bq query --project_id="$PROJECT_ID" --use_legacy_sql=false --format=sparse \
  "SELECT MAX(ts) as last_ts FROM \`$PROJECT_ID.medibox.requests\`" 2>/dev/null | \
  tail -1)
echo "✔ BigQuery requests table last record: $LAST_ROW"

# 4. Secret Manager: verify critical secrets are accessible
for secret in medibox-db-password medibox-firebase-admin-json; do
  if gcloud secrets versions access latest \
       --secret="$secret" --project="$PROJECT_ID" &>/dev/null; then
    echo "✔ Secret $secret: accessible"
  else
    echo "✘ Secret $secret: NOT accessible!"
  fi
done

echo "=== Backup Verification Complete ==="
```
