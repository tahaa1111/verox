# Medibox — Complete Deployment Guide

Everything you need to go from zero to a live GCP deployment, in one file, in order.
Work through each section top to bottom. Do not skip sections — later steps depend on earlier ones.

Your `.env.deploy` is already filled in with `PROJECT_ID=verox-4dc3f` and `REGION=us-central1`.
Every shell command below assumes that file is sourced.

---

## Windows note — run bash scripts in WSL or Git Bash

The deploy scripts are bash. On Windows you have two options:

**Option A — WSL (recommended):**
```
# Open Start → search "WSL" → Ubuntu terminal
# Navigate to your project:
cd "/mnt/c/Users/Guesmi Taha/Desktop/final pfe project/medibox-cloud"
```

**Option B — Git Bash:**
```
# Open Git Bash from the medibox-cloud folder
# (right-click in Explorer → "Git Bash Here")
```

All commands below are written for bash. Run them in one of these two environments.

---

## Phase 0 — One-time machine setup

Do these once. If you have already done them on this machine, verify and move on.

### Step 0.1 — Install gcloud CLI

Download and install from: https://cloud.google.com/sdk/docs/install

After installation, in your bash terminal:
```bash
gcloud version
# Must show: Google Cloud SDK 460.x.x or higher
# If lower: gcloud components update
```

Install the components the scripts need:
```bash
gcloud components install beta alpha
```

### Step 0.2 — Authenticate

Two separate auth commands are required. The first is for interactive use; the second sets Application Default Credentials (ADC) used by scripts and SDKs.

```bash
gcloud auth login
# Opens a browser window — sign in with your Google account

gcloud auth application-default login
# Opens browser again — approve the same account

# Verify both succeeded:
gcloud auth list
# Should show your account as ACTIVE
ls ~/.config/gcloud/application_default_credentials.json
# File must exist
```

### Step 0.3 — Install Docker

Docker is required to build container images. Install Docker Desktop for Windows:
https://docs.docker.com/desktop/install/windows-install/

After installation, start Docker Desktop. Verify in bash:
```bash
docker version
docker info
# Both must work without errors
```

### Step 0.4 — Install Python 3.11+

The scripts use Python for secret generation and smoke tests.
```bash
python3 --version
# Must show Python 3.11.x or higher

# If not installed: download from python.org
# On Ubuntu/WSL:
# sudo apt update && sudo apt install python3.11 python3-pip
```

### Step 0.5 — Install jq and openssl (WSL/Ubuntu)

```bash
sudo apt install -y jq openssl curl
```

---

## Phase 1 — Firebase setup (manual — do this before running any script)

The GCP scripts need the Firebase Admin SDK JSON file to exist on disk before they run. Do this first.

### Step 1.1 — Create a Firebase project linked to your GCP project

1. Go to https://console.firebase.google.com
2. Click **Add project**
3. When asked "Link to Google Cloud project?", select your existing project `verox-4dc3f`
4. Disable Google Analytics (not needed)
5. Click **Create project** and wait for it to finish

This links Firebase to your GCP project so they share the same project ID.

**Verify:**
```bash
source .env.deploy
gcloud firebase projects list --project=$PROJECT_ID
# Should list verox-4dc3f
```

### Step 1.2 — Enable Firebase Authentication

1. In Firebase Console → select `verox-4dc3f` project → Build → **Authentication**
2. Click **Get started**
3. Enable these sign-in methods (click each → Enable → Save):
   - **Email/Password** — for pharmacist admin accounts
   - **Anonymous** — for edge device auth during development
   - **Custom tokens** are automatically available (no toggle needed)
4. Click **Save** after each method

### Step 1.3 — Download the Firebase Admin SDK JSON

You already have this file. Your `.env.deploy` points to it:
```
C:/Users/Guesmi Taha/Downloads/verox-4dc3f-firebase-adminsdk-fbsvc-308fdf1960.json
```

Verify it is valid:
```bash
source .env.deploy
python3 -c "
import json
d = json.load(open('$FIREBASE_ADMIN_JSON_PATH'))
print('project_id :', d['project_id'])
print('client_email:', d['client_email'])
assert d['type'] == 'service_account', 'wrong type'
print('OK — Firebase admin JSON is valid')
"
```

If the file is missing, regenerate it:
1. Firebase Console → Project Settings (gear icon) → Service accounts
2. Click **Generate new private key** → **Generate key**
3. Save the downloaded file; update `FIREBASE_ADMIN_JSON_PATH` in `.env.deploy`

---

## Phase 2 — Verify environment and prerequisites

### Step 2.1 — Source your environment

Do this at the start of every bash session before running any script:
```bash
cd "/mnt/c/Users/Guesmi Taha/Desktop/final pfe project/medibox-cloud"
set -a && source .env.deploy && set +a
echo "Project: $PROJECT_ID  Region: $REGION"
# Should print: Project: verox-4dc3f  Region: us-central1
```

### Step 2.2 — Verify drug reference files exist

These JSON files are required for the drug normalization system. They must be present before deploy.
```bash
ls -lh referances/drug_dict.json referances/drug_registry.json
python3 -c "
import json
d = json.load(open('referances/drug_dict.json'))
r = json.load(open('referances/drug_registry.json'))
print(f'drug_dict    : {len(d)} entries')
print(f'drug_registry: {len(r)} entries')
"
# Both must show non-zero entry counts
```

If either file is missing, check the `referances/` directory in the project root.

### Step 2.3 — Run the pre-flight check

```bash
bash scripts/00_prereqs.sh
```

This checks every dependency and configuration value. Read the output carefully.

**All ✔ items must pass before continuing.**
**Any ✘ item must be fixed** — the error message tells you exactly what to do.
⚠ warnings are advisory — review them but you can proceed.

Common fixes:
- `gcloud not authenticated` → re-run `gcloud auth login` and `gcloud auth application-default login`
- `gcloud version too old` → `gcloud components update`
- `Docker daemon not running` → open Docker Desktop and wait for it to start
- `Firebase admin JSON missing` → check the path in `.env.deploy`

### Step 2.4 — Check GPU quota (do this now, it may take 24h)

GPU quota requests are often approved the same day for T4, but sometimes take longer. Request it now so it is ready when you need it.

```bash
source .env.deploy
# Check current T4 quota
gcloud compute regions describe $REGION \
  --project=$PROJECT_ID \
  --format="table(quotas[].metric,quotas[].limit,quotas[].usage)" 2>/dev/null | \
  grep -E "NVIDIA_T4|PREEMPTIBLE"
```

If `NVIDIA_T4_GPUS` limit is 0 (which it is for new projects):
1. Go to https://console.cloud.google.com/iam-admin/quotas?project=verox-4dc3f
2. Search for: `NVIDIA_T4_GPUS`
3. Select `us-central1`
4. Click **EDIT QUOTAS** → set new limit to `1` → submit

You can continue with the deployment while waiting. The Vertex endpoint step will fail if quota is not approved — you can re-run just that step later.

---

## Phase 3 — GCP infrastructure

This phase runs `01_setup_project.sh` which creates and configures all GCP resources:
project, APIs, service accounts, IAM roles, Cloud SQL, Memorystore, Artifact Registry,
Cloud Storage buckets, KMS keys, VPC connector, and Cloud Build infrastructure.

It also creates `medibox-edge-key.json` — the service account keyfile for the Raspberry Pi.
**Keyfile auth is the default and recommended path.** Workload Identity Federation (no key
on device) is a v2 hardening option you can enable later with `05a_setup_wif_optional.sh`
after the deployment is stable.

**Duration: ~15–25 minutes** (most of the time is waiting for Cloud SQL to provision)

### Step 3.1 — Dry run first (optional but recommended)

```bash
bash scripts/01_setup_project.sh --dry-run
```

This prints every command it would run without executing anything. Read through it to understand what will be created. No GCP resources are touched.

### Step 3.2 — Run the infrastructure setup

```bash
bash scripts/01_setup_project.sh
```

The script is **fully idempotent** — every create step checks for existence first. If it fails partway through, fix the error and re-run as-is; it will skip everything that already exists and pick up where it left off:

```bash
# Safe to re-run any number of times:
bash scripts/01_setup_project.sh
```

### Step 3.3 — Verify what was created

The script writes a `gcp_resources.txt` file with all resource identifiers. Check it:
```bash
cat gcp_resources.txt
```

You should see values for:
- `SQL_CONNECTION_NAME=verox-4dc3f:us-central1:medibox-postgres`
- `REDIS_HOST=` (Memorystore internal IP)
- `REGISTRY=us-central1-docker.pkg.dev/verox-4dc3f/medibox-repo`
- `VPC_CONNECTOR=medibox-connector`
- And more

Spot-check a few resources:
```bash
source .env.deploy

# Cloud SQL instance exists and is RUNNABLE
gcloud sql instances describe medibox-postgres \
  --project=$PROJECT_ID \
  --format="value(state)"
# Expected: RUNNABLE

# Artifact Registry repository exists
gcloud artifacts repositories describe $REPO \
  --location=$REGION \
  --project=$PROJECT_ID \
  --format="value(name)"

# Service accounts created
gcloud iam service-accounts list --project=$PROJECT_ID \
  --filter="email:medibox-" \
  --format="table(email,displayName)"
# Should show: medibox-runner, medibox-worker, medibox-ci, medibox-edge

# KMS key ring exists
gcloud kms keyrings list --location=global --project=$PROJECT_ID
# Should show: medibox-keyring

# VPC connector is READY
gcloud compute networks vpc-access connectors describe medibox-connector \
  --region=$REGION --project=$PROJECT_ID \
  --format="value(state)"
# Expected: READY
```

---

## Phase 4 — Secrets

This phase runs `02_setup_secrets.sh` which generates cryptographic secrets, prompts you to confirm them, and stores everything in GCP Secret Manager. Nothing is written to disk in plaintext.

**What it creates:**
- `medibox-db-password` — PostgreSQL password (auto-generated)
- `medibox-redis-auth` — Redis AUTH token (auto-generated)
- `medibox-jwt-signing-key` — API JWT signing key (auto-generated)
- `medibox-pii-encryption-key` — AES key for encrypting patient/doctor names (auto-generated)
- `medibox-firebase-admin-json` — your Firebase Admin SDK JSON file
- `medibox-database-url` — full async PostgreSQL connection string (auto-constructed)
- `medibox-database-url-sync` — full sync PostgreSQL connection string (auto-constructed)

### Step 4.1 — Run the secrets setup

```bash
bash scripts/02_setup_secrets.sh
```

The script is interactive. It will:
1. Show you each secret it is about to create
2. Ask you to confirm before storing anything
3. Print a manifest table at the end showing SET/MISSING status for each secret

Watch for any `MISSING` items in the final table — those must be fixed before deploying.

**Re-running is safe.** The script checks if a secret already has a real value before overwriting it, so running it twice will not destroy your secrets.

### Step 4.2 — Verify secrets are accessible

```bash
source .env.deploy

for SECRET in medibox-db-password medibox-redis-auth medibox-jwt-signing-key \
              medibox-pii-encryption-key medibox-firebase-admin-json \
              medibox-database-url medibox-database-url-sync; do
  VALUE=$(gcloud secrets versions access latest \
    --secret="$SECRET" --project=$PROJECT_ID 2>/dev/null | wc -c)
  if [[ "$VALUE" -gt 10 ]]; then
    echo "✔ $SECRET  (${VALUE} chars)"
  else
    echo "✘ $SECRET  MISSING OR EMPTY"
  fi
done
```

All seven must show ✔. If any show ✘, re-run `02_setup_secrets.sh`.

---

## Phase 5 — First deploy

This phase runs `03_first_deploy.sh` which:
1. Builds the API, worker, and vLLM Docker images via Cloud Build
2. Runs database migrations
3. Deploys Cloud Run services (API, worker, frontend)
4. Deploys the Vertex AI endpoint with the Qwen2.5-VL-7B model
5. Runs smoke tests to confirm everything is working

**Duration: 45–90 minutes** (most of the time is building the vLLM image and waiting for Vertex endpoint)

### Step 5.1 — Run the first deploy

```bash
bash scripts/03_first_deploy.sh
```

The script prints progress in real time. Key milestones to watch for:

```
[BUILD] Submitting main Docker build...
[BUILD] Build <id> running...        ← Cloud Build is building API + worker
[MIGRATE] Running database migrations...
[DEPLOY] Deploying medibox-api...
[DEPLOY] Deploying medibox-worker...
[VERTEX] Deploying vLLM endpoint...  ← longest step, ~20-45 min
[SMOKE] Running smoke tests...
[DONE] Deploy complete
```

If the build fails, the output will show which step failed. Common issues:

**"gcp_resources.txt not found"** — Phase 3 did not complete. Re-run `01_setup_project.sh`.

**"Secret X has placeholder value"** — Phase 4 did not complete. Re-run `02_setup_secrets.sh`.

**"Build failed"** — Check Cloud Build logs:
```bash
gcloud builds list --project=$PROJECT_ID --limit=3
gcloud builds log <BUILD_ID> --project=$PROJECT_ID
```

**"Vertex endpoint deploy timed out"** — Check if T4 quota was approved:
```bash
gcloud compute regions describe $REGION --project=$PROJECT_ID \
  --format="table(quotas[].metric,quotas[].limit)" | grep T4
```
If quota is still 0, wait for approval (see Step 2.4) then re-run only the Vertex step:
```bash
bash scripts/03_deploy_vllm_endpoint.sh
```

**Re-running after a partial failure is safe** — the script checks what already exists and skips it.

### Step 5.2 — Record your URLs

After the script completes, your API URL is saved in `gcp_resources.txt`:
```bash
grep "API_URL\|FRONTEND_URL\|VERTEX_ENDPOINT_ID" gcp_resources.txt
```

Save these — you will need the API URL for the edge device configuration.

---

## Phase 6 — Post-deploy manual steps

These cannot be automated by scripts. Do them once after the first deploy.

### Step 6.0 — Copy the edge keyfile to the Raspberry Pi

`01_setup_project.sh` created `medibox-edge-key.json` in the project directory.
Copy it to the Pi now before you forget.

```bash
# From your dev machine (adjust the IP to match EDGE_DEVICE_IP in .env.deploy):
scp ./medibox-edge-key.json pi@100.84.95.114:/tmp/medibox-edge-key.json

# On the Pi (SSH in and run these):
sudo mkdir -p /etc/medibox
sudo mv /tmp/medibox-edge-key.json /etc/medibox/medibox-edge.json
sudo chown medibox:medibox /etc/medibox/medibox-edge.json
sudo chmod 600 /etc/medibox/medibox-edge.json
```

Set the corresponding env vars in `/etc/medibox/edge.env` on the Pi:
```
MEDIBOX_AUTH_MODE=keyfile
MEDIBOX_CREDENTIALS_PATH=/etc/medibox/medibox-edge.json
```

The original `medibox-edge-key.json` in the project directory should be
deleted or moved to secure offline storage after copying:
```bash
# Back on your dev machine:
rm ./medibox-edge-key.json   # or move to password manager / encrypted USB
```

**To migrate to WIF later** (no key on the Pi), run `bash scripts/05a_setup_wif_optional.sh`
after the deployment is stable and tested.

---

### Step 6.1 — Verify all Cloud Run services are SERVING

```bash
source .env.deploy
gcloud run services list --region=$REGION --project=$PROJECT_ID \
  --format="table(metadata.name,status.conditions[0].type,status.conditions[0].status)"
```

Expected output:
```
NAME               TYPE    STATUS
medibox-api        Ready   True
medibox-worker     Ready   True
medibox-frontend   Ready   True
```

If any service is not Ready, check its logs:
```bash
gcloud logging read \
  'resource.type="cloud_run_revision"
   resource.labels.service_name="medibox-api"
   severity>=ERROR' \
  --project=$PROJECT_ID --freshness=30m --limit=20
```

### Step 6.2 — Verify the Vertex endpoint has a deployed model

```bash
source .env.deploy
ENDPOINT_ID=$(grep VERTEX_ENDPOINT_ID gcp_resources.txt | cut -d= -f2)
gcloud ai endpoints describe $ENDPOINT_ID \
  --region=$REGION --project=$PROJECT_ID \
  --format="table(deployedModels[].id,deployedModels[].displayName,deployedModels[].trafficSplit)"
```

Must show at least one deployed model with traffic split 100. If no models appear, the Vertex deploy step timed out — run:
```bash
bash scripts/03_deploy_vllm_endpoint.sh
```

### Step 6.3 — Run the health and smoke tests manually

```bash
source .env.deploy
API_URL=$(grep "^API_URL=" gcp_resources.txt | cut -d= -f2-)
TOKEN=$(gcloud auth print-identity-token)

# Health endpoint
curl -s -H "Authorization: Bearer $TOKEN" "$API_URL/v1/healthz" | python3 -m json.tool
# Expected: {"status": "ok", ...}

# Readiness endpoint
curl -s -H "Authorization: Bearer $TOKEN" "$API_URL/v1/readyz" | python3 -m json.tool
# Expected: {"status": "ready", "checks": {"database": "ok", "redis": "ok", ...}}
```

### Step 6.4 — Set up Cloud Monitoring dashboards and alerts

```bash
source .env.deploy
bash monitoring/deploy_monitoring.sh
```

This creates 4 dashboards and 10 alert policies. After it runs, verify in the console:
https://console.cloud.google.com/monitoring/dashboards?project=verox-4dc3f

You should see: System Overview, Model Quality, MLOps, Cost.

### Step 6.5 — Create budget alert (manual — GCP does not allow script creation)

1. Go to: https://console.cloud.google.com/billing/budgets?project=verox-4dc3f
2. Click **Create budget**
3. Fill in:
   - Name: `medibox-monthly`
   - Scope: Project `verox-4dc3f`
   - Budget type: Monthly, amount `$500`
   - Alert thresholds: 50% ($250), 80% ($400), 100% ($500)
   - Notifications: add `guesmitaha96@gmail.com`
4. Click **Finish**

Without this, overspending can go unnoticed until the month-end bill arrives.

### Step 6.6 — Create your admin account

The admin dashboard requires your Firebase UID in the `admin_role_grants` database table.

**Step A: Get your Firebase UID**
1. Go to https://console.firebase.google.com/project/verox-4dc3f/authentication/users
2. If no users exist, sign in to the frontend app at `$FRONTEND_URL` using email/password to create your account
3. Find your account in the list → copy the **User UID** (looks like: `abc123XyZ...`)

**Step B: Download Cloud SQL Auth Proxy**
```bash
# Linux/WSL:
curl -o cloud-sql-proxy \
  "https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/v2.13.0/cloud-sql-proxy.linux.amd64"
chmod +x cloud-sql-proxy

# macOS:
curl -o cloud-sql-proxy \
  "https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/v2.13.0/cloud-sql-proxy.darwin.amd64"
chmod +x cloud-sql-proxy
```

**Step C: Connect and insert the admin row**
```bash
source .env.deploy
FIREBASE_UID="PASTE_YOUR_UID_HERE"

DB_PASS=$(gcloud secrets versions access latest \
  --secret=medibox-db-password --project=$PROJECT_ID)
SQL_CONN="${PROJECT_ID}:${REGION}:medibox-postgres"

# Start proxy
./cloud-sql-proxy --port=5432 "$SQL_CONN" &
PROXY_PID=$!
sleep 4

# Insert admin row
PGPASSWORD="$DB_PASS" psql -h 127.0.0.1 -p 5432 -U medibox -d medibox -c "
INSERT INTO admin_role_grants (user_id, granted_by, granted_at)
VALUES ('$FIREBASE_UID', 'initial-setup', NOW())
ON CONFLICT (user_id) DO NOTHING;
SELECT user_id, granted_at FROM admin_role_grants;
"

# Stop proxy
kill $PROXY_PID

# Set Firebase custom claim so JWT includes admin:true
# Run this Python snippet once:
python3 - <<EOF
import firebase_admin
from firebase_admin import credentials, auth
cred = credentials.Certificate("$FIREBASE_ADMIN_JSON_PATH")
app = firebase_admin.initialize_app(cred)
auth.set_custom_user_claims("$FIREBASE_UID", {"admin": True})
print("Admin claim set for", "$FIREBASE_UID")
EOF
```

**Verify:** Go to `$FRONTEND_URL/admin` — it should load the admin dashboard without a 403.

### Step 6.7 — Test a real job submission

```bash
source .env.deploy
API_URL=$(grep "^API_URL=" gcp_resources.txt | cut -d= -f2-)

# Create a minimal test JPEG
python3 -c "
from PIL import Image
img = Image.new('RGB', (512,512), (255,255,255))
img.save('/tmp/test_crop.jpg')
print('Created /tmp/test_crop.jpg')
"

# You need a real Firebase JWT (not gcloud identity token) for /v1/submit
# Get one by signing in via Firebase REST API:
FIREBASE_API_KEY=$(python3 -c "
import json
# The web API key is in the Firebase Console → Project Settings → General → Web API Key
# Paste it here:
print('YOUR_FIREBASE_WEB_API_KEY')
")

# OR get an ID token directly (requires a test user in Firebase Auth):
ID_TOKEN=$(curl -s -X POST \
  "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=$FIREBASE_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"test@example.com\",\"password\":\"yourpassword\",\"returnSecureToken\":true}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['idToken'])")

# Submit
JOB_RESPONSE=$(curl -s -X POST "$API_URL/v1/submit" \
  -H "Authorization: Bearer $ID_TOKEN" \
  -F "crops=@/tmp/test_crop.jpg;type=image/jpeg" \
  -F "device_id=pi-0001" \
  -F "session_id=$(python3 -c 'import uuid; print(uuid.uuid4())')")
echo "$JOB_RESPONSE" | python3 -m json.tool

JOB_ID=$(echo "$JOB_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])")
echo "Job ID: $JOB_ID"

# Poll for result
for i in $(seq 1 24); do
  sleep 5
  RESULT=$(curl -s "$API_URL/v1/result/$JOB_ID" -H "Authorization: Bearer $ID_TOKEN")
  STATUS=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null)
  echo "  Poll $i: status=$STATUS"
  if [[ "$STATUS" == "completed" || "$STATUS" == "failed" ]]; then
    echo "$RESULT" | python3 -m json.tool
    break
  fi
done
```

---

## Phase 7 — CI/CD setup (optional but recommended)

Automates future deployments on every push to `main`.

### Step 7.1 — Push code to GitHub

```bash
cd "/mnt/c/Users/Guesmi Taha/Desktop/final pfe project/medibox-cloud"
git init
git add .
git commit -m "Initial Medibox GCP deployment"
# Create a GitHub repo first at github.com, then:
git remote add origin https://github.com/YOUR_GITHUB_USERNAME/medibox-cloud.git
git push -u origin main
```

### Step 7.2 — Connect GitHub to Cloud Build

1. Go to https://console.cloud.google.com/cloud-build/triggers?project=verox-4dc3f
2. Click **Manage repositories** → **Connect repository**
3. Select **GitHub** → authorize Google Cloud Build GitHub App
4. Select your repo → **Connect**

### Step 7.3 — Create the deploy trigger

```bash
source .env.deploy
GITHUB_OWNER="YOUR_GITHUB_USERNAME"
GITHUB_REPO="medibox-cloud"

gcloud builds triggers create github \
  --name="medibox-main-deploy" \
  --repo-name="$GITHUB_REPO" \
  --repo-owner="$GITHUB_OWNER" \
  --branch-pattern="^main$" \
  --build-config="cloudbuild.yaml" \
  --region="$REGION" \
  --project="$PROJECT_ID" \
  --service-account="projects/$PROJECT_ID/serviceAccounts/medibox-ci@$PROJECT_ID.iam.gserviceaccount.com"
```

After this, every push to `main` will automatically build and deploy.

---

## Phase 8 — Operations reference

Use these commands after deployment for day-to-day management.

### View live logs

```bash
source .env.deploy

# API logs (real-time)
gcloud logging tail \
  'resource.type="cloud_run_revision" resource.labels.service_name="medibox-api"' \
  --project=$PROJECT_ID

# Worker logs (real-time)
gcloud logging tail \
  'resource.type="cloud_run_revision" resource.labels.service_name="medibox-worker"' \
  --project=$PROJECT_ID

# Last 50 errors across all services
gcloud logging read \
  'resource.type="cloud_run_revision" severity>=ERROR' \
  --project=$PROJECT_ID --freshness=1h --limit=50
```

### Restart a service (clears stuck containers)

```bash
source .env.deploy
gcloud run services update medibox-api \
  --region=$REGION --project=$PROJECT_ID \
  --set-env-vars="RESTART_TS=$(date +%s)"
# Change medibox-api to medibox-worker or medibox-frontend as needed
```

### Roll back Cloud Run to a previous version

```bash
source .env.deploy
# List recent revisions
gcloud run revisions list --service=medibox-api \
  --region=$REGION --project=$PROJECT_ID \
  --format="table(metadata.name,metadata.creationTimestamp,spec.containers[0].image)"

# Roll back to a specific revision (replace the revision name)
gcloud run services update-traffic medibox-api \
  --to-revisions=medibox-api-00012-abc=100 \
  --region=$REGION --project=$PROJECT_ID
```

### Scale up for high traffic

```bash
source .env.deploy
gcloud run services update medibox-api \
  --min-instances=2 --max-instances=10 \
  --region=$REGION --project=$PROJECT_ID
```

### Scale down to save costs

```bash
source .env.deploy
gcloud run services update medibox-api \
  --min-instances=0 --max-instances=5 \
  --region=$REGION --project=$PROJECT_ID
```

### Pause the Vertex endpoint (saves ~$252/month)

```bash
source .env.deploy
ENDPOINT_ID=$(grep VERTEX_ENDPOINT_ID gcp_resources.txt | cut -d= -f2)
DEPLOYED_MODEL_ID=$(gcloud ai endpoints describe $ENDPOINT_ID \
  --region=$REGION --project=$PROJECT_ID \
  --format="value(deployedModels[0].id)")

gcloud ai endpoints undeploy-model $ENDPOINT_ID \
  --region=$REGION --project=$PROJECT_ID \
  --deployed-model-id=$DEPLOYED_MODEL_ID

# Tell the worker to use local fallback while Vertex is paused
gcloud run services update medibox-worker \
  --region=$REGION --project=$PROJECT_ID \
  --set-env-vars="LOCAL_FALLBACK_ENABLED=true"
```

To resume:
```bash
bash scripts/03_deploy_vllm_endpoint.sh
# Then restore LOCAL_FALLBACK_ENABLED=false on the worker
```

### Connect to the database (for debugging)

```bash
source .env.deploy
DB_PASS=$(gcloud secrets versions access latest \
  --secret=medibox-db-password --project=$PROJECT_ID)

./cloud-sql-proxy --port=5432 "${PROJECT_ID}:${REGION}:medibox-postgres" &
PROXY_PID=$!
sleep 3

PGPASSWORD="$DB_PASS" psql -h 127.0.0.1 -p 5432 -U medibox -d medibox

# When done:
kill $PROXY_PID
```

### Useful SQL queries

```sql
-- Jobs summary
SELECT status, COUNT(*) FROM jobs GROUP BY status;

-- Jobs stuck in processing > 10 min
SELECT job_id, device_id, created_at, NOW() - updated_at AS stuck_for
FROM jobs WHERE status='processing' AND updated_at < NOW() - INTERVAL '10 minutes';

-- Recent corrections
SELECT job_id, corrected_at, corrected_by FROM feedback
ORDER BY corrected_at DESC LIMIT 20;
```

### Run monthly backup verification

```bash
source .env.deploy
bash scripts/verify_backups.sh
```

### Key URLs for your project

| Resource | URL |
|----------|-----|
| Cloud Run services | https://console.cloud.google.com/run?project=verox-4dc3f |
| Vertex AI endpoints | https://console.cloud.google.com/vertex-ai/endpoints?project=verox-4dc3f |
| Cloud Build history | https://console.cloud.google.com/cloud-build/builds?project=verox-4dc3f |
| Monitoring dashboards | https://console.cloud.google.com/monitoring/dashboards?project=verox-4dc3f |
| Logs explorer | https://console.cloud.google.com/logs?project=verox-4dc3f |
| Secret Manager | https://console.cloud.google.com/security/secret-manager?project=verox-4dc3f |
| Cloud SQL | https://console.cloud.google.com/sql/instances?project=verox-4dc3f |
| Firebase console | https://console.firebase.google.com/project/verox-4dc3f |
| Billing | https://console.cloud.google.com/billing |
| Quota requests | https://console.cloud.google.com/iam-admin/quotas?project=verox-4dc3f |

---

## Deployment checklist

Use this to track your progress. Check each item as you complete it.

**Phase 0 — Machine setup**
- [ ] gcloud CLI installed and version ≥ 460
- [ ] `gcloud auth login` completed
- [ ] `gcloud auth application-default login` completed
- [ ] Docker Desktop installed and running
- [ ] Python 3.11+ installed

**Phase 1 — Firebase**
- [ ] Firebase project created and linked to `verox-4dc3f`
- [ ] Firebase Authentication enabled (Email/Password + Anonymous)
- [ ] Firebase Admin SDK JSON downloaded and path set in `.env.deploy`

**Phase 2 — Prerequisites**
- [ ] `bash scripts/00_prereqs.sh` passes with no ✘ errors
- [ ] T4 GPU quota requested (Step 2.4)
- [ ] Drug reference files exist (`referances/drug_dict.json`, `referances/drug_registry.json`)

**Phase 3 — Infrastructure**
- [ ] `bash scripts/01_setup_project.sh` completed successfully
- [ ] `gcp_resources.txt` exists with all values populated
- [ ] Cloud SQL instance is RUNNABLE
- [ ] VPC connector is READY

**Phase 4 — Secrets**
- [ ] `bash scripts/02_setup_secrets.sh` completed
- [ ] All 7 secrets show ✔ in verification check

**Phase 5 — Deploy**
- [ ] `bash scripts/03_first_deploy.sh` completed
- [ ] API URL recorded from `gcp_resources.txt`

**Phase 6 — Post-deploy**
- [ ] Edge keyfile copied to Pi and deleted from dev machine (Step 6.0)
- [ ] All 3 Cloud Run services show Ready
- [ ] Vertex endpoint shows a deployed model
- [ ] Health endpoint returns `{"status": "ok"}`
- [ ] Budget alert created in Billing console
- [ ] Monitoring dashboards deployed
- [ ] Admin UID inserted into `admin_role_grants`
- [ ] Admin Firebase custom claim set
- [ ] Test job submitted and completed successfully
