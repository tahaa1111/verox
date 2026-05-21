# Manual Steps — Medibox GCP Deployment

This file lists every step that the scripts cannot do for you.
Work through it top to bottom. Each item has a checkbox, instructions, and a verification command.

---

## Placeholders You Must Replace

Before doing anything else, find your values for these and put them in `.env.deploy`:

| Placeholder | Where to find it | Example |
|-------------|-----------------|---------|
| `<PROJECT_ID>` | You choose — must be globally unique, 6-30 chars, lowercase, `[a-z][a-z0-9-]+` | `medibox-prod-tn` |
| `<BILLING_ACCOUNT>` | `gcloud billing accounts list` | `01AB12-CD34EF-567890` |
| `<REGION>` | Choose from table below | `us-central1` |
| `<REPO>` | Keep as `medibox-repo` unless you have a reason to change | `medibox-repo` |
| `<FIREBASE_ADMIN_JSON_PATH>` | Local path after downloading from Firebase Console | `/home/you/Downloads/firebase-adminsdk.json` |
| `<FIREBASE_PROJECT_ID>` | Firebase Console → Project Settings → General | `medibox-prod-tn` |
| `<YOUR_EMAIL>` | Your oncall / monitoring email | `you@yourpharmacy.tn` |
| `<EDGE_DEVICE_IP>` | Your Pi's local IP | `192.168.1.42` |

**Region choice:**

| Region | Pros | Cons |
|--------|------|------|
| `us-central1` | Cheapest A100 quota, T4 always available | Farther from Tunisia (~100ms extra) |
| `europe-west1` | Closer to Tunisia (~50ms) | A100 quota harder to get |
| `europe-west4` | Good T4 availability | Slightly more expensive |

---

## A. Before Running Any Script

- [ ] **A1. Create a Google Cloud account**

  If you don't have one: go to [console.cloud.google.com](https://console.cloud.google.com) and sign in with a Google account. New accounts get **$300 free credits** valid for 90 days.

  **Verify credit balance:** Console → Billing → Credits.
  > If your trial has expired, you still get the always-free tier but Vertex AI T4 costs ~$252/month from day 1.

- [ ] **A2. Install gcloud CLI**

  Install from: [cloud.google.com/sdk/docs/install](https://cloud.google.com/sdk/docs/install)

  ```bash
  # Linux/macOS
  curl https://sdk.cloud.google.com | bash
  exec -l $SHELL
  gcloud init

  # Verify
  gcloud version
  # Must show: Google Cloud SDK 460.x.x or higher
  ```

  Update if needed: `gcloud components update`

- [ ] **A3. Authenticate**

  ```bash
  gcloud auth login
  # Opens browser — sign in with your Google account

  gcloud auth application-default login
  # Opens browser again — this sets up Application Default Credentials
  # used by scripts and SDKs

  # Verify
  gcloud auth list
  # Must show your account as ACTIVE
  ```

- [ ] **A4. Get your billing account ID**

  ```bash
  gcloud billing accounts list
  # Output example:
  # ACCOUNT_ID            NAME                OPEN  MASTER_ACCOUNT_ID
  # 01AB12-CD34EF-567890  My Billing Account  True
  ```

  Copy the `ACCOUNT_ID` value. Put it in `.env.deploy` as `BILLING_ACCOUNT`.

- [ ] **A5. Choose and record your PROJECT_ID**

  Rules: 6-30 characters, lowercase letters, digits, and hyphens. Must start with a letter. **Globally unique** — if someone else has it, you can't use it.

  ```bash
  # Check if your chosen ID is available
  gcloud projects describe medibox-prod-tn 2>&1
  # If output says "NOT_FOUND" — it's available
  # If it describes a project — pick a different name
  ```

- [ ] **A6. Copy and fill the environment template**

  ```bash
  cp .env.deploy.example .env.deploy
  nano .env.deploy   # or use any text editor
  # Fill in: PROJECT_ID, BILLING_ACCOUNT, REGION, FIREBASE_ADMIN_JSON_PATH
  ```

- [ ] **A7. Run the prerequisites check**

  ```bash
  set -a && source .env.deploy && set +a
  bash scripts/00_prereqs.sh
  # Fix any RED ✘ errors before proceeding
  ```

---

## B. Org Policy and Quota Checks

- [ ] **B1. Check for service account key creation restriction**

  Some organizations enforce `iam.disableServiceAccountKeyCreation`. Personal GCP accounts typically do not have this.

  ```bash
  gcloud org-policies describe iam.disableServiceAccountKeyCreation \
    --project=$PROJECT_ID 2>/dev/null || echo "No restriction (policy not set)"
  ```

  - If output says `NOT_FOUND` or no `enforced` state → you can create keyfiles (WIF is still preferred).
  - If output says `enforced: true` → you **must** use WIF. Run `01_setup_project.sh` **without** `--use-keyfile`.

- [ ] **B2. Check Vertex AI T4 GPU quota**

  ```bash
  gcloud compute regions describe $REGION \
    --project=$PROJECT_ID \
    --format="table(quotas[].metric,quotas[].limit,quotas[].usage)" 2>/dev/null | \
    grep -i "nvidia_t4"
  ```

  You need `NVIDIA_T4_GPUS` with limit ≥ 1. Default for new projects is usually 0 in `us-central1` — you must request it.

  **Request quota:**
  1. Console → IAM & Admin → Quotas & System Limits
  2. Filter: `NVIDIA_T4_GPUS` in region `us-central1`
  3. Click `EDIT QUOTAS` → request limit: **1**
  4. Typical approval time: **same day for T4, 24-48h for A100**

  > Without T4 quota, the Vertex endpoint will fail to deploy. Request this quota before running `03_first_deploy.sh`.

- [ ] **B3. Check Vertex AI A100 quota (for training)**

  A100 is needed for the monthly QLoRA retraining pipeline (optional — can use T4 instead).

  ```bash
  gcloud compute regions describe $REGION \
    --format="table(quotas[].metric,quotas[].limit)" 2>/dev/null | \
    grep -i "a100"
  ```

  If `NVIDIA_A100_GPUS` limit is 0, request via Console → Quotas → `NVIDIA_A100_GPUS`.
  Approval takes **1-5 business days** and may require a business justification.

  > If A100 quota is denied or delayed, edit `pipelines/monthly_qlora_retrain.py` and change `accelerator_type` to `NVIDIA_TESLA_T4`. Training will take ~5-7h instead of ~2-3h.

- [ ] **B4. Check Cloud Run CPU allocation quota**

  Only needed if you plan >50 concurrent users.

  ```bash
  gcloud compute regions describe $REGION \
    --format="table(quotas[].metric,quotas[].limit)" 2>/dev/null | \
    grep -i "run"
  ```

  Default Cloud Run quotas are generous. Monitor and request increases only if Cloud Run starts rejecting deployments.

---

## C. Firebase Setup

- [ ] **C1. Create a Firebase project**

  1. Go to [console.firebase.google.com](https://console.firebase.google.com)
  2. Click **Add project**
  3. **Important:** On the "Link to Google Cloud project?" step, choose your existing `<PROJECT_ID>` project. This links Firebase to GCP so the same project ID is used.
  4. Disable Google Analytics unless you specifically want it.
  5. Click **Create project**

  **Verify:** `gcloud firebase projects list` should show your project.

- [ ] **C2. Enable Firebase Authentication**

  1. In Firebase Console → your project → Build → Authentication
  2. Click **Get started**
  3. Enable the sign-in methods your edge devices will use:
     - **Anonymous** (simplest for device auth — tokens are issued without login)
     - **Email/Password** (for pharmacist admin users)
     - **Custom token** (for Pi device auth via backend-signed tokens)
  4. Click **Save**

- [ ] **C3. Configure Firebase JWT claims your API expects**

  The Medibox API checks these claims in every Firebase JWT:

  | Claim | Type | Required | Description |
  |-------|------|----------|-------------|
  | `uid` | string | Yes | Firebase user ID — used as pharmacist identifier |
  | `admin` | boolean | No | Set to `true` for admin users (also requires DB row) |

  Edge devices use **anonymous auth** or **custom tokens** — `admin` will be `false`.
  Admin users: set the `admin` custom claim via Firebase Admin SDK after creating the user:

  ```python
  # Run this once after deploy to make yourself admin:
  import firebase_admin
  from firebase_admin import auth
  firebase_admin.initialize_app()
  auth.set_custom_user_claims("YOUR_UID", {"admin": True})
  # Then also add a row to admin_role_grants table (see RUNBOOK.md)
  ```

- [ ] **C4. Download Firebase Admin SDK JSON**

  1. Firebase Console → Project Settings (gear icon) → Service accounts
  2. Click **Generate new private key**
  3. Click **Generate key** in the confirmation dialog
  4. Save the downloaded JSON to a safe location (e.g., `~/medibox-firebase-adminsdk.json`)
  5. `chmod 600 ~/medibox-firebase-adminsdk.json`

  Put the path in `.env.deploy`:
  ```bash
  FIREBASE_ADMIN_JSON_PATH=/home/you/medibox-firebase-adminsdk.json
  ```

  **Verify:**
  ```bash
  python3 -c "
  import json
  d = json.load(open('$FIREBASE_ADMIN_JSON_PATH'))
  print('project_id:', d['project_id'])
  print('client_email:', d['client_email'])
  assert d['type'] == 'service_account'
  print('OK — Firebase admin JSON is valid')
  "
  ```

---

## D. Files to Provide

- [ ] **D1. Firebase Admin SDK JSON** — covered in C4 above.

- [ ] **D2. Verify reference drug files exist**

  The drug normalization system uses two real JSON files. Verify they are present:

  ```bash
  ls -la referances/drug_dict.json referances/drug_registry.json
  # Both must exist and have content
  python3 -c "
  import json
  d = json.load(open('referances/drug_dict.json'))
  r = json.load(open('referances/drug_registry.json'))
  print(f'drug_dict: {len(d)} entries')
  print(f'drug_registry: {len(r)} entries')
  "
  ```

  These files are already in the repository from the prototype. Do NOT replace them with the fictional `tunisian_drug_formulary.csv` from the spec.

- [ ] **D3. Custom domain (optional)**

  If you want `api.medibox.tn` instead of the `.run.app` URL:

  1. After deploying, in Cloud Run console → medibox-api → Manage Custom Domains
  2. Click **Add mapping**
  3. Enter your domain: `api.medibox.tn`
  4. Cloud Run will give you DNS records to add at your domain registrar
  5. TLS certificate is managed automatically by Google — no manual cert work needed
  6. DNS propagation: 5-60 minutes

---

## E. GitHub Setup for CI/CD

- [ ] **E1. Push code to GitHub**

  ```bash
  git init
  git add .
  git commit -m "Initial Medibox GCP deployment"
  git remote add origin https://github.com/<GITHUB_OWNER>/<GITHUB_REPO>.git
  git push -u origin main
  ```

- [ ] **E2. Connect GitHub to Cloud Build**

  1. Cloud Console → Cloud Build → Triggers → **Manage repositories**
  2. Click **Connect repository**
  3. Select **GitHub** as the source
  4. Authorize Google Cloud Build GitHub App (one-time OAuth)
  5. Select your repository
  6. Click **Connect**

- [ ] **E3. Create Cloud Build trigger — main CI/CD**

  ```bash
  gcloud builds triggers create github \
    --name="medibox-main-deploy" \
    --repo-name="<GITHUB_REPO>" \
    --repo-owner="<GITHUB_OWNER>" \
    --branch-pattern="^main$" \
    --build-config="cloudbuild.yaml" \
    --region="$REGION" \
    --project="$PROJECT_ID" \
    --service-account="projects/$PROJECT_ID/serviceAccounts/medibox-ci@$PROJECT_ID.iam.gserviceaccount.com"
  ```

  **Verify:** Push a commit to `main` → check Cloud Build console for a running build.

- [ ] **E4. Create Cloud Build trigger — vLLM rebuild (manual only)**

  ```bash
  gcloud builds triggers create manual \
    --name="medibox-vllm-rebuild" \
    --repo-name="<GITHUB_REPO>" \
    --repo-owner="<GITHUB_OWNER>" \
    --branch="main" \
    --build-config="cloudbuild-vllm.yaml" \
    --region="$REGION" \
    --project="$PROJECT_ID"
  ```

  Trigger manually when you update the vLLM container or model weights.

---

## F. After First Deploy

- [ ] **F1. Verify Cloud Run services are healthy**

  ```bash
  gcloud run services list --region=$REGION --project=$PROJECT_ID
  # All services should show SERVING status

  # Get API URL
  API_URL=$(gcloud run services describe medibox-api \
    --region=$REGION --project=$PROJECT_ID \
    --format="value(status.url)")
  echo "API: $API_URL"
  ```

- [ ] **F2. Verify Vertex AI endpoint has a deployed model**

  ```bash
  gcloud ai endpoints list --region=$REGION --project=$PROJECT_ID
  # Should show medibox-vllm endpoint

  ENDPOINT_ID=$(gcloud ai endpoints list --region=$REGION --project=$PROJECT_ID \
    --filter="displayName:medibox-vllm" \
    --format="value(name)" | rev | cut -d/ -f1 | rev)

  gcloud ai endpoints describe $ENDPOINT_ID --region=$REGION --project=$PROJECT_ID \
    --format="table(deployedModels[].id,deployedModels[].displayName)"
  # Should show at least 1 deployed model
  ```

- [ ] **F3. Submit a test job via curl**

  ```bash
  # Get identity token (for private Cloud Run)
  TOKEN=$(gcloud auth print-identity-token)
  API_URL=$(grep "^API_URL=" gcp_resources.txt | cut -d= -f2-)

  # Health check
  curl -s -H "Authorization: Bearer $TOKEN" "$API_URL/v1/healthz" | python3 -m json.tool

  # Readiness check
  curl -s -H "Authorization: Bearer $TOKEN" "$API_URL/v1/readyz" | python3 -m json.tool

  # Create a test JPEG
  python3 -c "
  from PIL import Image
  img = Image.new('RGB', (512,512), (255,255,255))
  img.save('/tmp/test.jpg')
  print('Created /tmp/test.jpg')
  "

  # Submit job (requires a real Firebase JWT for production auth)
  # For testing with gcloud identity token (only works if auth is relaxed in dev):
  curl -s -X POST \
    -H "Authorization: Bearer $TOKEN" \
    -F "crops=@/tmp/test.jpg;type=image/jpeg" \
    -F "device_id=pi-0001" \
    -F "session_id=$(python3 -c 'import uuid; print(uuid.uuid4())')" \
    "$API_URL/v1/submit" | python3 -m json.tool
  ```

- [ ] **F4. Set up Cloud Monitoring dashboards**

  ```bash
  export NOTIFICATION_EMAIL=<YOUR_EMAIL>
  bash monitoring/deploy_monitoring.sh
  ```

  **Verify:** Console → Monitoring → Dashboards — should show 4 Medibox dashboards.

- [ ] **F5. Set up budget alerts**

  The scripts cannot create billing budgets (requires billing API with specific permissions).
  Do this manually:

  1. Console → Billing → your billing account → Budgets & alerts
  2. Click **Create budget**
  3. Name: `medibox-monthly`
  4. Scope: select project `<PROJECT_ID>`
  5. Budget type: Monthly
  6. Budget amount: `$500` (upper bound)
  7. Alert thresholds:
     - 50% of budget → $250 (notify: email)
     - 70% of budget → $350 (notify: email)
     - 100% of budget → $500 (notify: email + consider Pub/Sub to pause endpoint)
  8. Notifications: add your email
  9. Click **Finish**

- [ ] **F6. Create admin_role_grants row for yourself**

  The admin dashboard requires your Firebase UID to be in the `admin_role_grants` table.

  ```bash
  # First, get your Firebase UID (sign in to your app once and check the Firebase Console
  # → Authentication → Users → find your email → copy the User UID)

  FIREBASE_UID="YOUR_FIREBASE_UID_HERE"

  # Connect to Cloud SQL via Cloud SQL Auth Proxy
  # Download proxy:
  curl -o cloud-sql-proxy \
    "https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/v2.13.0/cloud-sql-proxy.linux.amd64"
  chmod +x cloud-sql-proxy

  DB_PASS=$(gcloud secrets versions access latest \
    --secret=medibox-db-password --project=$PROJECT_ID)
  SQL_CONN="${PROJECT_ID}:${REGION}:medibox-postgres"

  # Start proxy in background
  ./cloud-sql-proxy --port=5432 "$SQL_CONN" &
  PROXY_PID=$!
  sleep 3

  # Insert admin row
  PGPASSWORD="$DB_PASS" psql -h 127.0.0.1 -U medibox -d medibox -c "
  INSERT INTO admin_role_grants (user_id, granted_by, granted_at)
  VALUES ('$FIREBASE_UID', 'initial-setup', NOW())
  ON CONFLICT (user_id) DO NOTHING;
  "

  kill $PROXY_PID
  ```

  **Verify:** The admin dashboard at `$FRONTEND_URL/admin` should load without a 403.

---

## G. Ongoing Operations

### Viewing Logs

```bash
# Real-time API logs
gcloud logging tail \
  'resource.type="cloud_run_revision" resource.labels.service_name="medibox-api"' \
  --project=$PROJECT_ID

# Real-time worker logs
gcloud logging tail \
  'resource.type="cloud_run_revision" resource.labels.service_name="medibox-worker"' \
  --project=$PROJECT_ID

# Last 100 errors across all services
gcloud logging read \
  'resource.type="cloud_run_revision" severity>=ERROR' \
  --project=$PROJECT_ID --limit=100 --format=json | \
  python3 -m json.tool | grep -A5 '"textPayload"'

# Console shortcut:
# https://console.cloud.google.com/logs/query?project=$PROJECT_ID
```

### Scaling Services Manually

```bash
# Scale up API (e.g., before a high-traffic event)
gcloud run services update medibox-api \
  --min-instances=2 --max-instances=10 \
  --region=$REGION --project=$PROJECT_ID

# Scale down (to save costs after the event)
gcloud run services update medibox-api \
  --min-instances=0 --max-instances=5 \
  --region=$REGION --project=$PROJECT_ID

# Emergency: reduce connections to Cloud SQL (if near 25 limit)
gcloud run services update medibox-api \
  --max-instances=3 \
  --region=$REGION --project=$PROJECT_ID
gcloud run services update medibox-worker \
  --max-instances=3 \
  --region=$REGION --project=$PROJECT_ID
```

### Rolling Back a Cloud Run Deployment

```bash
# List recent revisions
gcloud run revisions list --service=medibox-api \
  --region=$REGION --project=$PROJECT_ID

# Roll back to a specific revision
gcloud run services update-traffic medibox-api \
  --to-revisions=medibox-api-00012-abc=100 \
  --region=$REGION --project=$PROJECT_ID

# Verify
gcloud run services describe medibox-api \
  --region=$REGION --project=$PROJECT_ID \
  --format="table(spec.traffic[].revisionName,spec.traffic[].percent)"
```

### Rolling Back a Vertex AI Model

```bash
# Via API (requires admin Firebase token):
API_URL=$(grep "^API_URL=" gcp_resources.txt | cut -d= -f2-)
ADMIN_TOKEN="your-firebase-admin-jwt"

# List deployed models to find the previous one
ENDPOINT_ID=$(gcloud ai endpoints list --region=$REGION --project=$PROJECT_ID \
  --filter="displayName:medibox-vllm" \
  --format="value(name)" | rev | cut -d/ -f1 | rev)

gcloud ai endpoints describe $ENDPOINT_ID \
  --region=$REGION --project=$PROJECT_ID \
  --format="table(deployedModels[].id,deployedModels[].displayName)"

# Roll back
curl -X POST "$API_URL/v1/admin/model/rollback" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"deployed_model_id": "PREVIOUS_DEPLOYED_MODEL_ID"}'
```

### Pausing the Vertex Endpoint (Cost Savings)

```bash
# Pause (saves ~$252/month — takes effect in ~1 minute)
bash scripts/pause_endpoint.sh

# Resume (takes ~5 minutes for the model to load)
bash scripts/resume_endpoint.sh

# Verify endpoint status
ENDPOINT_ID=$(gcloud ai endpoints list --region=$REGION --project=$PROJECT_ID \
  --filter="displayName:medibox-vllm" --format="value(name)" | \
  rev | cut -d/ -f1 | rev)
gcloud ai endpoints describe $ENDPOINT_ID --region=$REGION --project=$PROJECT_ID \
  --format="table(deployedModels[].id)"
```

### Restoring from Cloud SQL Backup

```bash
# List available backups
gcloud sql backups list --instance=medibox-postgres --project=$PROJECT_ID

# Restore to a specific backup (destructive — stops the instance briefly)
gcloud sql backups restore <BACKUP_ID> \
  --restore-instance=medibox-postgres \
  --project=$PROJECT_ID

# For point-in-time recovery (PITR) to a specific timestamp:
gcloud sql instances restore-backup medibox-postgres \
  --backup-instance=medibox-postgres \
  --restore-database-from-instance=medibox-postgres \
  --restore-time="2026-05-20T10:30:00Z" \
  --project=$PROJECT_ID
```

### Where Everything Lives

| Resource | URL |
|----------|-----|
| API | `$API_URL` (from gcp_resources.txt) |
| Frontend | `$FRONTEND_URL` |
| Cloud Run console | `https://console.cloud.google.com/run?project=$PROJECT_ID` |
| Vertex AI console | `https://console.cloud.google.com/vertex-ai/endpoints?project=$PROJECT_ID` |
| Cloud Build | `https://console.cloud.google.com/cloud-build/builds?project=$PROJECT_ID` |
| Monitoring | `https://console.cloud.google.com/monitoring?project=$PROJECT_ID` |
| Logs | `https://console.cloud.google.com/logs?project=$PROJECT_ID` |
| Secret Manager | `https://console.cloud.google.com/security/secret-manager?project=$PROJECT_ID` |
| Cloud SQL | `https://console.cloud.google.com/sql/instances?project=$PROJECT_ID` |
| Billing | `https://console.cloud.google.com/billing` |
| BigQuery | `https://console.cloud.google.com/bigquery?project=$PROJECT_ID` |
