#!/usr/bin/env bash
# =============================================================================
# Medibox — End-to-End First Deploy
#
# ROLE OF THIS SCRIPT
# ───────────────────
# This is a ONE-TIME bootstrap script.  It:
#   1. Submits the main Cloud Build pipeline (cloudbuild.yaml) which builds all
#      images and deploys all three Cloud Run services with the correct secrets
#      and static env vars.
#   2. Runs Alembic database migrations via a Cloud Run Job.
#   3. Sets the two DYNAMIC env vars that are not known at Cloud Build time:
#        REDIS_URL  — private Memorystore IP (stored in gcp_resources.txt by
#                     01_setup_project.sh)
#        DB_HOST    — Cloud SQL Auth Proxy socket path (deterministic from
#                     SQL connection name; also set here for safety in case
#                     cloudbuild.yaml's --update-env-vars ran first)
#   4. Optionally deploys the vLLM Vertex AI endpoint.
#   5. Runs end-to-end smoke tests.
#
# For subsequent deployments, push to main (or run `gcloud builds submit`
# manually) — cloudbuild.yaml handles everything and does NOT re-run migrations.
#
# Usage:
#   set -a && source .env.deploy && set +a
#   bash scripts/03_first_deploy.sh [--skip-build] [--skip-vllm] [--skip-smoke]
#
# Flags:
#   --skip-build   Skip Cloud Build image builds (use :latest that already exists)
#   --skip-vllm    Skip Vertex AI endpoint deploy (slow; do separately)
#   --skip-smoke   Skip smoke tests
# =============================================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

ok()     { echo -e "  ${GREEN}✔${NC}  $*"; }
fail()   { echo -e "  ${RED}✘${NC}  $*" >&2; }
warn()   { echo -e "  ${YELLOW}⚠${NC}  $*"; }
info()   { echo -e "  ${BLUE}ℹ${NC}  $*"; }
header() { echo -e "\n${BOLD}${BLUE}════════════════════════════════${NC}"; \
            echo -e "${BOLD}${BLUE}  $*${NC}"; \
            echo -e "${BOLD}${BLUE}════════════════════════════════${NC}"; }

SKIP_BUILD=false
SKIP_VLLM=false
SKIP_SMOKE=false

for arg in "$@"; do
  case "$arg" in
    --skip-build) SKIP_BUILD=true ;;
    --skip-vllm)  SKIP_VLLM=true ;;
    --skip-smoke) SKIP_SMOKE=true ;;
    *) echo "Unknown flag: $arg"; exit 1 ;;
  esac
done

# ---------------------------------------------------------------------------
# Required variables
# ---------------------------------------------------------------------------

# Auto-source .env.deploy if PROJECT_ID is not already set
if [[ -z "${PROJECT_ID:-}" && -f ".env.deploy" ]]; then
  set -a && source ".env.deploy" && set +a
elif [[ -z "${PROJECT_ID:-}" && -f "../.env.deploy" ]]; then
  set -a && source "../.env.deploy" && set +a
fi

: "${PROJECT_ID:?PROJECT_ID must be set — run: set -a && source .env.deploy && set +a}"
REGION="${REGION:-us-central1}"
REPO="${REPO:-medibox-repo}"
REGISTRY="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}"
SQL_CONN="${PROJECT_ID}:${REGION}:medibox-postgres"
CONNECTOR="medibox-connector"

echo -e "${BOLD}Medibox — First Deploy${NC}"
echo "Project: $PROJECT_ID | Region: $REGION"
echo "========================================"

# ---------------------------------------------------------------------------
# 0. Pre-flight: verify prerequisites ran
# ---------------------------------------------------------------------------
header "0 · Pre-flight Checks"

if [[ ! -f "gcp_resources.txt" ]]; then
  fail "gcp_resources.txt not found — run 01_setup_project.sh first"
  exit 1
fi
ok "gcp_resources.txt found"

# Check all secrets exist and have real values
SECRETS_OK=true
for secret in medibox-db-password medibox-redis-auth \
              medibox-firebase-admin-json medibox-jwt-signing-key; do
  VAL=$(gcloud secrets versions access latest \
    --secret="$secret" --project="$PROJECT_ID" 2>/dev/null || echo "")
  if [[ -z "$VAL" || "$VAL" == "REPLACE_ME_WITH_REAL_VALUE" ]]; then
    fail "Secret '$secret' is missing or is a placeholder — run 02_setup_secrets.sh"
    SECRETS_OK=false
  else
    ok "Secret $secret: set"
  fi
done
$SECRETS_OK || exit 1

gcloud config set project "$PROJECT_ID" --quiet
gcloud config set compute/region "$REGION" --quiet

# ---------------------------------------------------------------------------
# 1. Build all images via Cloud Build (cloudbuild.yaml)
#    cloudbuild.yaml handles: lint → test → build → push → deploy API/worker/frontend
# ---------------------------------------------------------------------------
header "1 · Build & Deploy via Cloud Build"

if [[ "$SKIP_BUILD" == "true" ]]; then
  warn "Skipping Cloud Build (--skip-build)"
else
  log_build() { echo -e "  ${BLUE}[build]${NC} $*"; }
  log_build "Submitting main Cloud Build (builds + deploys API + Worker + Frontend)"
  log_build "This typically takes 8-15 minutes..."

  # Capture stderr to a file so we can show it on failure (not swallowed).
  if ! gcloud builds submit \
       --config=cloudbuild.yaml \
       --project="$PROJECT_ID" \
       --region="$REGION" \
       --async \
       --format="value(id)" \
       . > /tmp/mb_build_id.txt 2>/tmp/mb_build_err.txt; then
    fail "Failed to submit Cloud Build — stderr follows:"
    cat /tmp/mb_build_err.txt >&2
    exit 1
  fi
  BUILD_ID=$(cat /tmp/mb_build_id.txt)

  ok "Cloud Build submitted: $BUILD_ID"
  info "Waiting for build to complete (polling every 30s)..."

  ELAPSED=0
  MAX_WAIT=1800  # 30 minutes
  while true; do
    STATUS=$(gcloud builds describe "$BUILD_ID" \
      --project="$PROJECT_ID" \
      --region="$REGION" \
      --format="value(status)" 2>/dev/null || echo "UNKNOWN")

    case "$STATUS" in
      SUCCESS)
        ok "Cloud Build succeeded"
        break
        ;;
      FAILURE|CANCELLED|TIMEOUT|INTERNAL_ERROR)
        fail "Cloud Build $STATUS"
        info "View logs: gcloud builds log $BUILD_ID --region=$REGION --project=$PROJECT_ID"
        info "Console: https://console.cloud.google.com/cloud-build/builds/$BUILD_ID?project=$PROJECT_ID"
        exit 1
        ;;
      WORKING|QUEUED)
        echo -ne "  ${BLUE}ℹ${NC}  Build status: $STATUS (${ELAPSED}s elapsed)\r"
        sleep 30
        ELAPSED=$((ELAPSED + 30))
        if [[ $ELAPSED -ge $MAX_WAIT ]]; then
          fail "Build timed out after ${MAX_WAIT}s"
          exit 1
        fi
        ;;
      *)
        warn "Unknown build status: $STATUS — waiting..."
        sleep 30
        ELAPSED=$((ELAPSED + 30))
        ;;
    esac
  done
fi

# Verify images exist in Artifact Registry
for image in medibox-api medibox-worker medibox-frontend; do
  if gcloud artifacts docker images describe \
       "${REGISTRY}/${image}:latest" \
       --project="$PROJECT_ID" &>/dev/null; then
    ok "Image exists: ${REGISTRY}/${image}:latest"
  else
    warn "Image not found: ${REGISTRY}/${image}:latest — build may have failed or used wrong tag"
  fi
done

# ---------------------------------------------------------------------------
# 2. Alembic Migrations via Cloud Run Job
#    Must run AFTER the API image is built (step 1) and BEFORE smoke tests.
# ---------------------------------------------------------------------------
header "2 · Database Migrations"

info "Running Alembic migrations using a one-shot Cloud Run Job"
info "This connects via Cloud SQL Auth Proxy (automatic with --add-cloudsql-instances)"

# Create/update the migration job (create falls back to update if already exists)
gcloud run jobs create medibox-migrate \
  --image="${REGISTRY}/medibox-api:latest" \
  --command="alembic" \
  --args="upgrade,head" \
  --region="$REGION" \
  --service-account="medibox-runner@${PROJECT_ID}.iam.gserviceaccount.com" \
  --add-cloudsql-instances="$SQL_CONN" \
  --vpc-connector="$CONNECTOR" \
  --vpc-egress=private-ranges-only \
  --set-secrets="DATABASE_URL=medibox-database-url-sync:latest" \
  --set-env-vars="ENVIRONMENT=production" \
  --max-retries=1 \
  --project="$PROJECT_ID" 2>/dev/null || \
gcloud run jobs update medibox-migrate \
  --image="${REGISTRY}/medibox-api:latest" \
  --command="alembic" \
  --args="upgrade,head" \
  --region="$REGION" \
  --service-account="medibox-runner@${PROJECT_ID}.iam.gserviceaccount.com" \
  --add-cloudsql-instances="$SQL_CONN" \
  --vpc-connector="$CONNECTOR" \
  --vpc-egress=private-ranges-only \
  --set-secrets="DATABASE_URL=medibox-database-url-sync:latest" \
  --set-env-vars="ENVIRONMENT=production" \
  --max-retries=1 \
  --project="$PROJECT_ID"

ok "Migration job configured"

info "Executing migration job..."
gcloud run jobs execute medibox-migrate \
  --region="$REGION" \
  --project="$PROJECT_ID" \
  --wait

ok "Database migrations complete"

# ---------------------------------------------------------------------------
# 3. Set dynamic env vars that cloudbuild.yaml cannot know at build time
#
#    REDIS_URL  — Memorystore private IP is project-specific; stored in
#                 gcp_resources.txt by 01_setup_project.sh.
#    DB_HOST    — Cloud SQL Auth Proxy socket path; deterministic but also
#                 set here as a belt-and-suspenders measure.
#
#    --update-env-vars preserves all other env vars set by Cloud Build.
#    This step is idempotent: re-running it on the same values is a no-op.
# ---------------------------------------------------------------------------
header "3 · Post-Deploy Dynamic Env Vars"

REDIS_HOST=$(grep "^REDIS_HOST=" gcp_resources.txt 2>/dev/null | cut -d= -f2 || echo "")
REDIS_PORT=$(grep "^REDIS_PORT=" gcp_resources.txt 2>/dev/null | cut -d= -f2 || echo "6379")
DB_HOST="/cloudsql/${SQL_CONN}"

if [[ -z "$REDIS_HOST" ]]; then
  warn "REDIS_HOST not found in gcp_resources.txt — REDIS_URL not set on services"
  warn "Re-run 01_setup_project.sh once Redis is READY, then re-run this script"
else
  REDIS_URL="redis://${REDIS_HOST}:${REDIS_PORT}/0"
  info "Setting REDIS_URL=${REDIS_URL} and DB_HOST=${DB_HOST} on API and worker"

  gcloud run services update medibox-api \
    --region="$REGION" --project="$PROJECT_ID" \
    --update-env-vars="REDIS_URL=${REDIS_URL},DB_HOST=${DB_HOST}" \
    --quiet
  ok "medibox-api: REDIS_URL + DB_HOST set"

  gcloud run services update medibox-worker \
    --region="$REGION" --project="$PROJECT_ID" \
    --update-env-vars="REDIS_URL=${REDIS_URL}" \
    --quiet
  ok "medibox-worker: REDIS_URL set"
fi

# Capture service URLs from Cloud Run (set by cloudbuild.yaml deploy steps)
API_URL=$(gcloud run services describe medibox-api \
  --region="$REGION" --project="$PROJECT_ID" \
  --format="value(status.url)" 2>/dev/null || echo "")
WORKER_URL=$(gcloud run services describe medibox-worker \
  --region="$REGION" --project="$PROJECT_ID" \
  --format="value(status.url)" 2>/dev/null || echo "")
FRONTEND_URL=$(gcloud run services describe medibox-frontend \
  --region="$REGION" --project="$PROJECT_ID" \
  --format="value(status.url)" 2>/dev/null || echo "")

[[ -n "$API_URL" ]]      && echo "API_URL=${API_URL}"           >> gcp_resources.txt
[[ -n "$WORKER_URL" ]]   && echo "WORKER_URL=${WORKER_URL}"     >> gcp_resources.txt
[[ -n "$FRONTEND_URL" ]] && echo "FRONTEND_URL=${FRONTEND_URL}" >> gcp_resources.txt

ok "Service URLs captured"

# ---------------------------------------------------------------------------
# 4. Deploy vLLM Endpoint (Vertex AI) — optional
# ---------------------------------------------------------------------------
header "4 · Vertex AI vLLM Endpoint"

if [[ "$SKIP_VLLM" == "true" ]]; then
  warn "Skipping Vertex AI endpoint deploy (--skip-vllm)"
  warn "Run separately: gcloud builds submit --config cloudbuild-vllm.yaml --region=$REGION ."
  VERTEX_ENDPOINT_ID="SKIPPED"
else
  info "Building vLLM container (cloudbuild-vllm.yaml) — takes 20-40 minutes"
  info "The model weights will be downloaded from GCS or HuggingFace on first start"

  # Stderr is captured to a temp file so failures are visible, not swallowed.
  if ! gcloud builds submit \
       --config=cloudbuild-vllm.yaml \
       --project="$PROJECT_ID" \
       --region="$REGION" \
       --format="value(id)" \
       --async \
       . > /tmp/mb_vllm_build_id.txt 2>/tmp/mb_vllm_build_err.txt; then
    warn "vLLM Cloud Build failed to submit — stderr follows:"
    cat /tmp/mb_vllm_build_err.txt >&2
    warn "Trying direct deploy script as fallback..."
    bash scripts/03_deploy_vllm_endpoint.sh || \
      warn "vLLM deploy failed — run manually: bash scripts/03_deploy_vllm_endpoint.sh"
    SKIP_VLLM=true
  else
    VLLM_BUILD_ID=$(cat /tmp/mb_vllm_build_id.txt)
    ok "vLLM Cloud Build submitted: $VLLM_BUILD_ID"
    info "Waiting for vLLM build (this is long — ~30 min)..."

    ELAPSED=0
    MAX_WAIT=3600
    while true; do
      STATUS=$(gcloud builds describe "$VLLM_BUILD_ID" \
        --project="$PROJECT_ID" \
        --region="$REGION" \
        --format="value(status)" 2>/dev/null || echo "UNKNOWN")
      case "$STATUS" in
        SUCCESS) ok "vLLM build succeeded"; break ;;
        FAILURE|CANCELLED|TIMEOUT)
          fail "vLLM build $STATUS"
          info "Check: https://console.cloud.google.com/cloud-build/builds/$VLLM_BUILD_ID?project=$PROJECT_ID"
          SKIP_VLLM=true; break ;;
        *)
          echo -ne "  ${BLUE}ℹ${NC}  vLLM build: $STATUS (${ELAPSED}s)\r"
          sleep 60; ELAPSED=$((ELAPSED + 60))
          [[ $ELAPSED -ge $MAX_WAIT ]] && { fail "vLLM build timed out"; SKIP_VLLM=true; break; }
          ;;
      esac
    done
  fi

  if [[ "$SKIP_VLLM" == "false" ]]; then
    VERTEX_ENDPOINT_ID=$(gcloud ai endpoints list \
      --region="$REGION" \
      --project="$PROJECT_ID" \
      --filter="displayName:medibox-vllm" \
      --format="value(name)" 2>/dev/null | head -1 | rev | cut -d/ -f1 | rev || echo "")

    if [[ -n "$VERTEX_ENDPOINT_ID" ]]; then
      ok "Vertex AI endpoint: $VERTEX_ENDPOINT_ID"
      echo "VERTEX_ENDPOINT_ID=$VERTEX_ENDPOINT_ID" >> gcp_resources.txt

      info "Waiting for Vertex endpoint to become healthy (may take 5-10 min)..."
      for i in $(seq 1 20); do
        HEALTHY=$(gcloud ai endpoints describe "$VERTEX_ENDPOINT_ID" \
          --region="$REGION" \
          --project="$PROJECT_ID" \
          --format="json" 2>/dev/null | \
          python3 -c "import sys,json; d=json.load(sys.stdin); \
            models=d.get('deployedModels',[]); \
            print('yes' if models else 'no')" 2>/dev/null || echo "no")
        if [[ "$HEALTHY" == "yes" ]]; then
          ok "Vertex endpoint has deployed models"
          break
        fi
        echo -ne "  ${BLUE}ℹ${NC}  Waiting for endpoint health check ($i/20)...\r"
        sleep 30
      done

      gcloud run services update medibox-worker \
        --region="$REGION" \
        --project="$PROJECT_ID" \
        --update-env-vars="VERTEX_ENDPOINT_ID=${VERTEX_ENDPOINT_ID}" \
        --quiet
      ok "Worker updated with VERTEX_ENDPOINT_ID"
    else
      warn "Could not determine Vertex endpoint ID — update medibox-worker manually"
      warn "Add env var: VERTEX_ENDPOINT_ID=<id> to medibox-worker Cloud Run service"
    fi
  fi
fi

# ---------------------------------------------------------------------------
# 5. Smoke Tests
# ---------------------------------------------------------------------------
header "5 · Smoke Tests"

if [[ "$SKIP_SMOKE" == "true" ]]; then
  warn "Skipping smoke tests (--skip-smoke)"
else
  info "Getting identity token for smoke tests..."
  TOKEN=$(gcloud auth print-identity-token 2>/dev/null || echo "")

  if [[ -z "$TOKEN" ]]; then
    warn "Cannot get identity token — skipping authenticated smoke tests"
    warn "Run manually: curl -H 'Authorization: Bearer \$(gcloud auth print-identity-token)' ${API_URL}/v1/healthz"
  else
    AUTH_HEADER="Authorization: Bearer $TOKEN"

    # Test 1: healthz
    info "Test 1: GET /v1/healthz"
    HEALTH=$(curl -sf --max-time 10 \
      -H "$AUTH_HEADER" \
      "${API_URL}/v1/healthz" 2>/dev/null || echo "ERROR")
    if echo "$HEALTH" | grep -qi '"status"'; then
      ok "healthz: $HEALTH"
    else
      fail "healthz failed: $HEALTH"
    fi

    # Test 2: readyz
    info "Test 2: GET /v1/readyz"
    READY=$(curl -sf --max-time 15 \
      -H "$AUTH_HEADER" \
      "${API_URL}/v1/readyz" 2>/dev/null || echo "ERROR")
    if echo "$READY" | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0 if d.get('status')=='ok' else 1)" 2>/dev/null; then
      ok "readyz: healthy"
    else
      warn "readyz returned non-ok status: $READY"
      warn "This may be normal if Redis/Postgres aren't fully reachable yet"
    fi

    # Test 3: Submit a synthetic job
    info "Test 3: Submitting a synthetic prescription job"
    python3 -c "
from PIL import Image; import io
img = Image.new('RGB', (512, 512), (255, 255, 255))
img.save('/tmp/test_crop.jpg', format='JPEG')
print('Test image created: /tmp/test_crop.jpg')
" 2>/dev/null || {
      warn "PIL not installed locally — creating minimal JPEG bytes"
      printf '\xff\xd8\xff\xe0\x00\x10\x4a\x46\x49\x46\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xdb\x00\x43\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\x09\x09\x08\x0a\x0c\x14\x0d\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c\x20\x24\x2e\x27\x20\x22\x2c\x23\x1c\x1c\x28\x37\x29\x2c\x30\x31\x34\x34\x34\x1f\x27\x39\x3d\x38\x32\x3c\x2e\x33\x34\x32\xff\xc0\x00\x0b\x08\x00\x02\x00\x02\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00\xfb\x26\xff\xd9' > /tmp/test_crop.jpg
    }

    SESSION_ID=$(python3 -c "import uuid; print(uuid.uuid4())" 2>/dev/null || echo "00000000-0000-4000-a000-000000000000")
    SUBMIT_RESP=$(curl -sf --max-time 30 \
      -H "$AUTH_HEADER" \
      -F "crops=@/tmp/test_crop.jpg;type=image/jpeg" \
      -F "device_id=pi-0001" \
      -F "session_id=$SESSION_ID" \
      "${API_URL}/v1/submit" 2>/dev/null || echo "ERROR")

    if echo "$SUBMIT_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print('job_id:', d['job_id'])" 2>/dev/null; then
      JOB_ID=$(echo "$SUBMIT_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])" 2>/dev/null || echo "")
      ok "Submit succeeded — job_id: $JOB_ID"

      if [[ -n "$JOB_ID" ]]; then
        info "Polling job until completed (max 2 min)..."
        for i in $(seq 1 24); do
          POLL=$(curl -sf --max-time 10 \
            -H "$AUTH_HEADER" \
            "${API_URL}/v1/result/${JOB_ID}" 2>/dev/null || echo '{"status":"error"}')
          STATUS=$(echo "$POLL" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','unknown'))" 2>/dev/null || echo "unknown")
          if [[ "$STATUS" == "completed" ]]; then
            ok "Job completed successfully!"
            HAS_DISCLAIMER=$(echo "$POLL" | python3 -c "
import sys,json; d=json.load(sys.stdin)
r=d.get('result',{}) or {}
print('yes' if 'Pharmacist verification' in str(r.get('disclaimer','')) else 'no')
" 2>/dev/null || echo "no")
            [[ "$HAS_DISCLAIMER" == "yes" ]] && ok "Disclaimer present in response" \
              || warn "Disclaimer not found in response — check response schema"
            break
          elif [[ "$STATUS" == "failed" ]]; then
            ERR=$(echo "$POLL" | python3 -c "import sys,json; print(json.load(sys.stdin).get('error_message','unknown'))" 2>/dev/null || echo "unknown")
            warn "Test job failed: $ERR (may be expected if Vertex endpoint is not deployed)"
            break
          else
            echo -ne "  ${BLUE}ℹ${NC}  Job status: $STATUS ($((i*5))s)\r"
            sleep 5
          fi
        done
      fi
    else
      warn "Submit returned: $SUBMIT_RESP"
      warn "This may be normal if Firebase auth is required — the API requires a real token"
    fi
  fi
fi

# ---------------------------------------------------------------------------
# 6. Final summary
# ---------------------------------------------------------------------------
header "Deploy Complete"

echo ""
echo -e "${BOLD}${GREEN}════════════════════════════════════════${NC}"
echo -e "${BOLD}${GREEN}  Medibox is deployed!${NC}"
echo -e "${BOLD}${GREEN}════════════════════════════════════════${NC}"
echo ""
echo -e "  ${BOLD}API URL:${NC}         ${API_URL:-<not deployed>}"
echo -e "  ${BOLD}Frontend URL:${NC}    ${FRONTEND_URL:-<not deployed>}"
echo -e "  ${BOLD}Worker URL:${NC}      ${WORKER_URL:-<not deployed>}"
if [[ -n "${VERTEX_ENDPOINT_ID:-}" && "$VERTEX_ENDPOINT_ID" != "SKIPPED" ]]; then
  echo -e "  ${BOLD}Vertex Endpoint:${NC} $VERTEX_ENDPOINT_ID"
fi
echo ""
echo -e "  ${BOLD}GCP Console:${NC}     https://console.cloud.google.com/run?project=$PROJECT_ID"
echo -e "  ${BOLD}Cloud Build:${NC}     https://console.cloud.google.com/cloud-build/builds?project=$PROJECT_ID"
echo -e "  ${BOLD}Vertex AI:${NC}       https://console.cloud.google.com/vertex-ai/endpoints?project=$PROJECT_ID"
echo -e "  ${BOLD}Monitoring:${NC}      https://console.cloud.google.com/monitoring?project=$PROJECT_ID"
echo ""
echo -e "${YELLOW}  Next steps:${NC}"
echo -e "  1. Set up budget alerts: Cloud Console → Billing → Budgets & alerts"
echo -e "  2. Connect GitHub repo to Cloud Build for CI/CD (see MANUAL_STEPS.md §E)"
echo -e "  3. Run monitoring setup: bash monitoring/deploy_monitoring.sh"
echo -e "  4. Test with a real prescription image and a Firebase JWT token"
echo -e "  5. Read POST_DEPLOY_CHECKLIST.md and go through every checkbox"
echo ""

{
  echo "DEPLOY_TIMESTAMP=$(date -u '+%Y-%m-%d %H:%M:%S UTC')"
} >> gcp_resources.txt

ok "All URLs saved to gcp_resources.txt"
