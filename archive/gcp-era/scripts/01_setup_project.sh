#!/usr/bin/env bash
# =============================================================================
# Medibox — Idempotent GCP Project Setup
# Spec §11.1
#
# Usage:
#   set -a && source .env.deploy && set +a
#   bash scripts/01_setup_project.sh [--dry-run]
#
# Flags:
#   --dry-run   Print every command without executing it
#
# Edge authentication:
#   This script creates a service account key (medibox-edge-key.json) for the
#   edge device.  That is the default and recommended path for initial deploy.
#   To migrate to Workload Identity Federation later, run the separate script:
#     bash scripts/05a_setup_wif_optional.sh
#
# Idempotency guarantee:
#   Every resource-creation step calls the corresponding describe/show/list
#   command first.  If the resource already exists the step prints "already
#   exists" and moves on — no ALREADY_EXISTS errors, no side effects.
#   The script is safe to run any number of times against an already-set-up
#   project.
# =============================================================================
set -euo pipefail
export CLOUDSDK_CORE_DISABLE_PROMPTS=1

# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

log()    { echo -e "[$(date -u +%H:%M:%S)] ${BOLD}$*${NC}"; }
ok()     { echo -e "  ${GREEN}✔${NC} $*"; }
info()   { echo -e "  ${BLUE}ℹ${NC} $*"; }
warn()   { echo -e "  ${YELLOW}⚠${NC} $*"; }
err()    { echo -e "  ${RED}✘${NC} $*" >&2; }
header() {
  echo -e "\n${BOLD}${BLUE}════════════════════════════════${NC}"
  echo -e "${BOLD}${BLUE}  $*${NC}"
  echo -e "${BOLD}${BLUE}════════════════════════════════${NC}"
}

# ---------------------------------------------------------------------------
# Flag parsing
# ---------------------------------------------------------------------------
DRY_RUN=false

for arg in "$@"; do
  case "$arg" in
    --dry-run)       DRY_RUN=true ;;
    --skip-existing) warn "--skip-existing is no longer needed — every step is always idempotent" ;;
    --use-keyfile)   warn "--use-keyfile is now the default and this flag is a no-op" ;;
    --use-wif)       err "--use-wif is not supported here. Run scripts/05a_setup_wif_optional.sh instead."; exit 1 ;;
    *) err "Unknown flag: $arg"; exit 1 ;;
  esac
done

# run(): in dry-run mode prints the command; otherwise executes it.
run() {
  if [[ "$DRY_RUN" == "true" ]]; then
    echo -e "  ${YELLOW}[DRY-RUN]${NC} $*"
  else
    "$@"
  fi
}

[[ "$DRY_RUN" == "true" ]] && warn "DRY-RUN mode — no changes will be made"

# ---------------------------------------------------------------------------
# Required variables — auto-source .env.deploy if variables are not already set
# ---------------------------------------------------------------------------
if [[ -z "${PROJECT_ID:-}" && -f ".env.deploy" ]]; then
  set -a && source ".env.deploy" && set +a
  ok "Loaded .env.deploy"
elif [[ -z "${PROJECT_ID:-}" && -f "../.env.deploy" ]]; then
  set -a && source "../.env.deploy" && set +a
  ok "Loaded ../.env.deploy"
fi

: "${PROJECT_ID:?PROJECT_ID must be set — run: set -a && source .env.deploy && set +a}"
: "${BILLING_ACCOUNT:?BILLING_ACCOUNT must be set}"
REGION="${REGION:-us-central1}"
REPO="${REPO:-medibox-repo}"

SA_RUNNER="medibox-runner"
SA_EDGE="medibox-edge"
SA_CI="medibox-ci"
SA_WORKER="medibox-worker"
RUNNER_EMAIL="${SA_RUNNER}@${PROJECT_ID}.iam.gserviceaccount.com"
WORKER_EMAIL="${SA_WORKER}@${PROJECT_ID}.iam.gserviceaccount.com"
EDGE_EMAIL="${SA_EDGE}@${PROJECT_ID}.iam.gserviceaccount.com"
CI_EMAIL="${SA_CI}@${PROJECT_ID}.iam.gserviceaccount.com"
VPC_NAME="medibox-vpc"
SUBNET_NAME="medibox-subnet"
CONNECTOR_NAME="medibox-connector"
KMS_KEYRING="medibox"
KMS_KEY="pii-key"
KMS_KEY_NAME="projects/${PROJECT_ID}/locations/${REGION}/keyRings/${KMS_KEYRING}/cryptoKeys/${KMS_KEY}"
RESOURCES_FILE="gcp_resources.txt"

# record(): write KEY=VALUE to gcp_resources.txt, replacing any previous value.
record() {
  local key="$1" val="$2"
  touch "$RESOURCES_FILE"
  grep -v "^${key}=" "$RESOURCES_FILE" > /tmp/_gcp_res_tmp 2>/dev/null || true
  echo "${key}=${val}" >> /tmp/_gcp_res_tmp
  mv /tmp/_gcp_res_tmp "$RESOURCES_FILE"
}

# ---------------------------------------------------------------------------
# ensure_* functions — idempotent by construction
#
# Contract: every function checks whether the resource already exists.
# If it does, print "already exists" and return 0.
# If it doesn't, run the create command via run().
# Never call create without the prior existence check.
# ---------------------------------------------------------------------------

ensure_artifact_registry() {
  # $1=repo  $2=location  $3=project
  local repo="$1" location="$2" project="$3"
  if gcloud artifacts repositories describe "$repo" \
       --location="$location" --project="$project" &>/dev/null; then
    ok "Artifact Registry '$repo' already exists"
    return 0
  fi
  log "Creating Artifact Registry repo: $repo"
  run gcloud artifacts repositories create "$repo" \
    --repository-format=docker \
    --location="$location" \
    --description="Medibox container images" \
    --project="$project"
}

ensure_service_account() {
  # $1=short-name  $2=project
  local name="$1" project="$2"
  local email="${name}@${project}.iam.gserviceaccount.com"
  if gcloud iam service-accounts describe "$email" \
       --project="$project" &>/dev/null; then
    ok "Service account '$name' already exists"
    return 0
  fi
  log "Creating service account: $name"
  run gcloud iam service-accounts create "$name" \
    --display-name="Medibox ${name}" \
    --project="$project"
}

ensure_vpc_network() {
  # $1=network-name  $2=project
  local network="$1" project="$2"
  if gcloud compute networks describe "$network" \
       --project="$project" &>/dev/null; then
    ok "VPC network '$network' already exists"
    return 0
  fi
  log "Creating VPC network: $network"
  run gcloud compute networks create "$network" \
    --subnet-mode=custom \
    --project="$project"
}

ensure_subnet() {
  # $1=subnet-name  $2=network  $3=region  $4=range  $5=project
  local subnet="$1" network="$2" region="$3" range="$4" project="$5"
  if gcloud compute networks subnets describe "$subnet" \
       --region="$region" --project="$project" &>/dev/null; then
    ok "Subnet '$subnet' already exists"
    return 0
  fi
  log "Creating subnet: $subnet ($range)"
  run gcloud compute networks subnets create "$subnet" \
    --network="$network" \
    --region="$region" \
    --range="$range" \
    --project="$project"
}

ensure_vpc_connector() {
  # $1=connector-name  $2=network  $3=region  $4=project
  local connector="$1" network="$2" region="$3" project="$4"
  if gcloud compute networks vpc-access connectors describe "$connector" \
       --region="$region" --project="$project" &>/dev/null; then
    ok "VPC connector '$connector' already exists"
    return 0
  fi
  log "Creating Serverless VPC Connector: $connector"
  run gcloud compute networks vpc-access connectors create "$connector" \
    --region="$region" \
    --network="$network" \
    --range="10.8.0.0/28" \
    --min-throughput=200 \
    --max-throughput=300 \
    --project="$project"
}

ensure_kms_keyring() {
  # $1=keyring  $2=location  $3=project
  local keyring="$1" location="$2" project="$3"
  if gcloud kms keyrings describe "$keyring" \
       --location="$location" --project="$project" &>/dev/null; then
    ok "KMS keyring '$keyring' already exists"
    return 0
  fi
  log "Creating KMS keyring: $keyring"
  run gcloud kms keyrings create "$keyring" \
    --location="$location" \
    --project="$project"
}

ensure_kms_key() {
  # $1=key  $2=keyring  $3=location  $4=project
  local key="$1" keyring="$2" location="$3" project="$4"
  if gcloud kms keys describe "$key" \
       --keyring="$keyring" --location="$location" --project="$project" &>/dev/null; then
    ok "KMS key '$key' already exists"
    return 0
  fi
  log "Creating KMS key: $key (90-day rotation)"
  local next_rotation
  next_rotation=$(date -u -d '+90 days' '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null \
                  || date -u -v+90d '+%Y-%m-%dT%H:%M:%SZ')
  run gcloud kms keys create "$key" \
    --keyring="$keyring" \
    --location="$location" \
    --purpose=encryption \
    --rotation-period=7776000s \
    --next-rotation-time="$next_rotation" \
    --protection-level=software \
    --project="$project"
}

ensure_bucket() {
  # $1=bucket-name (without gs://)  $2=location  $3=project
  local bucket="$1" location="$2" project="$3"
  if gcloud storage buckets describe "gs://${bucket}" \
       --project="$project" &>/dev/null; then
    ok "Bucket gs://${bucket} already exists"
    return 0
  fi
  log "Creating bucket: gs://${bucket}"
  run gcloud storage buckets create "gs://${bucket}" \
    --location="$location" \
    --uniform-bucket-level-access \
    --project="$project"
  run gcloud storage buckets update "gs://${bucket}" \
    --default-encryption-key="$KMS_KEY_NAME"
}

ensure_bq_dataset() {
  # $1=project  $2=dataset  $3=location
  local project="$1" dataset="$2" location="$3"
  if bq --project_id="$project" show "${project}:${dataset}" &>/dev/null; then
    ok "BigQuery dataset '$dataset' already exists"
    return 0
  fi
  log "Creating BigQuery dataset: $dataset"
  run bq --location="$location" mk \
    --dataset \
    --description="Medibox analytics" \
    "${project}:${dataset}"
}

ensure_bq_table() {
  # $1=project  $2=dataset  $3=table  $4=description  $5=schema  [$6=partition-field]
  local project="$1" dataset="$2" table="$3" desc="$4" schema="$5"
  local partition_field="${6:-}"
  if bq --project_id="$project" show "${project}:${dataset}.${table}" &>/dev/null; then
    ok "BigQuery table '$dataset.$table' already exists"
    return 0
  fi
  log "Creating BigQuery table: $dataset.$table"
  if [[ -n "$partition_field" ]]; then
    run bq mk --table \
      --time_partitioning_field="$partition_field" \
      --time_partitioning_expiration=94608000 \
      --description="$desc" \
      "${project}:${dataset}.${table}" \
      "$schema"
  else
    run bq mk --table \
      --description="$desc" \
      "${project}:${dataset}.${table}" \
      "$schema"
  fi
}

ensure_redis_instance() {
  # $1=name  $2=region  $3=network  $4=project
  local name="$1" region="$2" network="$3" project="$4"
  if gcloud redis instances describe "$name" \
       --region="$region" --project="$project" &>/dev/null; then
    ok "Redis instance '$name' already exists"
    return 0
  fi
  log "Creating Memorystore Redis (BASIC, 1 GB, AUTH enabled) — takes 3-5 minutes"
  run gcloud redis instances create "$name" \
    --size=1 \
    --region="$region" \
    --network="projects/${project}/global/networks/${network}" \
    --tier=BASIC \
    --redis-version=redis_7_0 \
    --enable-auth \
    --project="$project" \
    --quiet
}

ensure_sql_instance() {
  # $1=instance  $2=region  $3=network  $4=kms-key  $5=project
  local instance="$1" region="$2" network="$3" kms_key="$4" project="$5"
  if gcloud sql instances describe "$instance" \
       --project="$project" &>/dev/null; then
    ok "Cloud SQL instance '$instance' already exists"
    return 0
  fi
  log "Creating Cloud SQL Postgres 15 (db-f1-micro, private IP) — takes 5-10 minutes"
  run gcloud sql instances create "$instance" \
    --database-version=POSTGRES_15 \
    --tier=db-f1-micro \
    --region="$region" \
    --no-assign-ip \
    --network="projects/${project}/global/networks/${network}" \
    --disk-encryption-key="$kms_key" \
    --backup \
    --backup-start-time=02:00 \
    --retained-backups-count=7 \
    --enable-point-in-time-recovery \
    --retained-transaction-log-days=7 \
    --deletion-protection \
    --project="$project"
}

ensure_sql_database() {
  # $1=db-name  $2=instance  $3=project
  local dbname="$1" instance="$2" project="$3"
  if gcloud sql databases describe "$dbname" \
       --instance="$instance" --project="$project" &>/dev/null; then
    ok "Cloud SQL database '$dbname' already exists"
    return 0
  fi
  log "Creating Cloud SQL database: $dbname"
  run gcloud sql databases create "$dbname" \
    --instance="$instance" \
    --project="$project"
}

ensure_sql_user() {
  # Creates the DB user + Secret Manager password only on first run.
  # $1=username  $2=instance  $3=project
  local username="$1" instance="$2" project="$3"
  local secret_name="medibox-db-password"
  if gcloud secrets describe "$secret_name" --project="$project" &>/dev/null; then
    ok "DB password already exists in Secret Manager"
    return 0
  fi
  if [[ "$DRY_RUN" == "true" ]]; then
    run echo "openssl rand → gcloud sql users create → gcloud secrets create"
    return 0
  fi
  log "Generating DB password and creating SQL user"
  local db_pass
  db_pass=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
  run gcloud sql users create "$username" \
    --instance="$instance" \
    --password="$db_pass" \
    --project="$project"
  printf '%s' "$db_pass" | run gcloud secrets create "$secret_name" \
    --data-file=- \
    --labels="app=medibox,component=postgres" \
    --project="$project"
  ok "DB user '$username' and password stored in Secret Manager"
}


ensure_scheduler_job() {
  # $1=job-name  $2=location  $3=project  then all remaining args are passed to create
  local job="$1" location="$2" project="$3"
  shift 3
  if gcloud scheduler jobs describe "$job" \
       --location="$location" --project="$project" &>/dev/null; then
    ok "Cloud Scheduler job '$job' already exists"
    return 0
  fi
  log "Creating Cloud Scheduler job: $job"
  run gcloud scheduler jobs create http "$job" \
    --location="$location" \
    --project="$project" \
    "$@" 2>/dev/null \
    || warn "Scheduler job '$job' creation failed — create manually after first pipeline run"
}

ensure_vpc_peering() {
  # Creates the Service Networking VPC peering that Cloud SQL private IP requires.
  # Without this step, Cloud SQL instance creation fails with NETWORK_NOT_PEERED.
  # Both sub-steps are idempotent: the address create is skipped if the range
  # already exists, and vpc-peerings connect is a no-op if the peering is current.
  # $1=network  $2=project
  local network="$1" project="$2"
  local range_name="google-managed-services-${network}"

  # 1. Reserve a /16 IP range for Service Networking (idempotent)
  if gcloud compute addresses describe "$range_name" \
       --global --project="$project" &>/dev/null; then
    ok "Service Networking IP range '$range_name' already reserved"
  else
    log "Reserving /16 IP range for Service Networking: $range_name"
    run gcloud compute addresses create "$range_name" \
      --global \
      --purpose=VPC_PEERING \
      --prefix-length=16 \
      --network="projects/${project}/global/networks/${network}" \
      --project="$project"
  fi

  # 2. Connect the peering (update-safe — re-running is a no-op if already connected)
  log "Connecting Service Networking VPC peering"
  run gcloud services vpc-peerings connect \
    --service=servicenetworking.googleapis.com \
    --ranges="$range_name" \
    --network="$network" \
    --project="$project"
  ok "Service Networking VPC peering connected"
}

ensure_sql_cmek_sa() {
  # Provisions the Cloud SQL service agent and grants it KMS encrypter/decrypter.
  # REQUIRED before creating a CMEK-encrypted Cloud SQL instance; without this the
  # instance creation fails with ERROR_P4_SA_NOT_FOUND.
  # `gcloud beta services identity create` is idempotent — it creates the SA on
  # first call and returns the existing SA on subsequent calls.
  # $1=kms-key-short-name  $2=keyring  $3=location  $4=project
  local kms_key="$1" keyring="$2" location="$3" project="$4"

  log "Provisioning Cloud SQL service agent (required for CMEK)"

  if [[ "$DRY_RUN" == "true" ]]; then
    echo -e "  ${YELLOW}[DRY-RUN]${NC} gcloud beta services identity create --service=sqladmin.googleapis.com --project=$project"
    echo -e "  ${YELLOW}[DRY-RUN]${NC} gcloud kms keys add-iam-policy-binding $kms_key (Cloud SQL SA → cloudkms.cryptoKeyEncrypterDecrypter)"
    return 0
  fi

  local cloudsql_sa
  # gcloud beta services identity create prints the SA email; capture it.
  cloudsql_sa=$(gcloud beta services identity create \
    --service=sqladmin.googleapis.com \
    --project="$project" \
    --format="value(email)" 2>/dev/null) || {
    # Fallback: derive the well-known email from project number if beta is unavailable.
    local project_number
    project_number=$(gcloud projects describe "$project" \
      --format="value(projectNumber)" 2>/dev/null || echo "")
    if [[ -n "$project_number" ]]; then
      cloudsql_sa="service-${project_number}@gcp-sa-cloud-sql.iam.gserviceaccount.com"
      warn "gcloud beta unavailable — using derived Cloud SQL SA: $cloudsql_sa"
    fi
  }

  if [[ -z "$cloudsql_sa" ]]; then
    err "Could not determine Cloud SQL service agent — CMEK instance creation may fail."
    err "Run manually: gcloud beta services identity create --service=sqladmin.googleapis.com --project=$project"
    return 1
  fi

  ok "Cloud SQL service agent: $cloudsql_sa"
  gcloud kms keys add-iam-policy-binding "$kms_key" \
    --keyring="$keyring" --location="$location" \
    --member="serviceAccount:${cloudsql_sa}" \
    --role="roles/cloudkms.cryptoKeyEncrypterDecrypter" \
    --project="$project" --quiet >/dev/null
  ok "Cloud SQL service agent granted cloudkms.cryptoKeyEncrypterDecrypter on $kms_key"
}

ensure_secret_placeholder() {
  # Creates the secret with a placeholder value only if it does not exist.
  # $1=secret-name  $2=project
  local secret="$1" project="$2"
  if gcloud secrets describe "$secret" --project="$project" &>/dev/null; then
    ok "Secret '$secret' already exists"
  else
    log "Creating placeholder secret: $secret"
    printf '%s' "REPLACE_ME_WITH_REAL_VALUE" | run gcloud secrets create "$secret" \
      --data-file=- \
      --labels="app=medibox,status=placeholder" \
      --project="$project"
    warn "Secret '$secret' is a PLACEHOLDER — run 02_setup_secrets.sh to populate"
  fi
  # Grant runner SA access (add-iam-policy-binding is idempotent)
  run gcloud secrets add-iam-policy-binding "$secret" \
    --member="serviceAccount:${RUNNER_EMAIL}" \
    --role="roles/secretmanager.secretAccessor" \
    --project="$project" \
    --quiet >/dev/null
}

# ---------------------------------------------------------------------------
# Print banner
# ---------------------------------------------------------------------------
echo ""
header "Medibox GCP Setup — Project: $PROJECT_ID"
info "Region:   $REGION"
info "Billing:  $BILLING_ACCOUNT"
info "Repo:     $REPO"
info "Dry-run:  $DRY_RUN"
echo ""

# ---------------------------------------------------------------------------
# 1. Project & billing
# ---------------------------------------------------------------------------
header "1 · Project & Billing"

if gcloud projects describe "$PROJECT_ID" &>/dev/null; then
  ok "Project $PROJECT_ID already exists"
else
  log "Creating project $PROJECT_ID"
  run gcloud projects create "$PROJECT_ID" --name="Medibox Production"
fi

run gcloud config set project "$PROJECT_ID"
run gcloud config set compute/region "$REGION"

log "Linking billing account (idempotent)"
run gcloud billing projects link "$PROJECT_ID" --billing-account="$BILLING_ACCOUNT"
ok "Billing linked"

record PROJECT_ID "$PROJECT_ID"
record REGION     "$REGION"

# ---------------------------------------------------------------------------
# 2. Enable APIs (gcloud services enable is idempotent)
# ---------------------------------------------------------------------------
header "2 · Enable APIs"
log "Enabling all required APIs (may take 2-3 minutes)"

APIS=(
  aiplatform.googleapis.com
  artifactregistry.googleapis.com
  cloudbuild.googleapis.com
  run.googleapis.com
  storage.googleapis.com
  logging.googleapis.com
  monitoring.googleapis.com
  cloudtrace.googleapis.com
  bigquery.googleapis.com
  bigquerystorage.googleapis.com
  iam.googleapis.com
  iamcredentials.googleapis.com
  sqladmin.googleapis.com
  redis.googleapis.com
  secretmanager.googleapis.com
  vpcaccess.googleapis.com
  cloudscheduler.googleapis.com
  pubsub.googleapis.com
  cloudkms.googleapis.com
  compute.googleapis.com
  servicenetworking.googleapis.com
  cloudresourcemanager.googleapis.com
  billingbudgets.googleapis.com
  firebase.googleapis.com
  identitytoolkit.googleapis.com
)

# gcloud services enable caps at 20 per call — chunk the array
BATCH_SIZE=20
TOTAL=${#APIS[@]}
for ((i=0, batch=1; i<TOTAL; i+=BATCH_SIZE, batch++)); do
  BATCH=("${APIS[@]:i:BATCH_SIZE}")
  log "Enabling APIs batch ${batch} (${#BATCH[@]} services)"
  run gcloud services enable "${BATCH[@]}" --project="$PROJECT_ID"
done
ok "All APIs enabled (${TOTAL} services)"

# ---------------------------------------------------------------------------
# 3. Artifact Registry
# ---------------------------------------------------------------------------
header "3 · Artifact Registry"

ensure_artifact_registry "$REPO" "$REGION" "$PROJECT_ID"
run gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet

REGISTRY="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}"
record REGISTRY "$REGISTRY"
ok "Registry: $REGISTRY"

# ---------------------------------------------------------------------------
# 4. Service Accounts
# ---------------------------------------------------------------------------
header "4 · Service Accounts"

for sa_name in "$SA_RUNNER" "$SA_WORKER" "$SA_EDGE" "$SA_CI"; do
  ensure_service_account "$sa_name" "$PROJECT_ID"
done

# ── Runner SA roles ──────────────────────────────────────────────────────────
log "Binding roles to medibox-runner"
for role in \
  roles/aiplatform.user \
  roles/cloudsql.client \
  roles/storage.objectUser \
  roles/secretmanager.secretAccessor \
  roles/logging.logWriter \
  roles/monitoring.metricWriter \
  roles/cloudtrace.agent \
  roles/bigquery.dataEditor \
  roles/run.invoker \
  roles/cloudkms.cryptoKeyEncrypterDecrypter; do
  run gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${RUNNER_EMAIL}" \
    --role="$role" --condition=None --quiet >/dev/null
done
ok "Runner SA roles bound"

# ── Worker SA roles ──────────────────────────────────────────────────────────
log "Binding roles to medibox-worker"
for role in \
  roles/aiplatform.user \
  roles/cloudsql.client \
  roles/storage.objectUser \
  roles/secretmanager.secretAccessor \
  roles/logging.logWriter \
  roles/monitoring.metricWriter \
  roles/cloudkms.cryptoKeyEncrypterDecrypter; do
  run gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${WORKER_EMAIL}" \
    --role="$role" --condition=None --quiet >/dev/null
done
ok "Worker SA roles bound"

# ── Edge SA roles (run.invoker only — DD-010) ────────────────────────────────
log "Binding roles to medibox-edge"
run gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${EDGE_EMAIL}" \
  --role="roles/run.invoker" --condition=None --quiet >/dev/null
ok "Edge SA role bound"

# ── CI SA roles ───────────────────────────────────────────────────────────────
log "Binding roles to medibox-ci"
for role in \
  roles/artifactregistry.writer \
  roles/cloudbuild.builds.editor \
  roles/run.developer \
  roles/aiplatform.user \
  roles/storage.objectUser \
  roles/iam.serviceAccountUser; do
  run gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${CI_EMAIL}" \
    --role="$role" --condition=None --quiet >/dev/null
done
ok "CI SA roles bound"

record RUNNER_SA "$RUNNER_EMAIL"
record WORKER_SA "$WORKER_EMAIL"
record EDGE_SA   "$EDGE_EMAIL"
record CI_SA     "$CI_EMAIL"

# ---------------------------------------------------------------------------
# 5. Edge Authentication — service account key (default path)
#
# A keyfile is created once and copied securely to the Pi.
# To migrate to Workload Identity Federation later (no key on device),
# run scripts/05a_setup_wif_optional.sh after initial deployment is stable.
# ---------------------------------------------------------------------------
header "5 · Edge Authentication (keyfile)"

# Check org policy with a hard timeout.
# Personal GCP projects are not under a GCP Organization and will never have
# this policy enforced — but the API call can hang indefinitely on those
# accounts.  We time-box it to 8 s; any result other than a clean "enforced"
# match is treated as "no restriction".
ORG_POLICY_BLOCKED=false
if command -v timeout &>/dev/null; then
  if timeout 8 gcloud org-policies describe iam.disableServiceAccountKeyCreation \
       --project="$PROJECT_ID" 2>/dev/null | grep -q "enforced"; then
    ORG_POLICY_BLOCKED=true
  fi
else
  # timeout not available (e.g. macOS without GNU coreutils) — skip the check
  info "Skipping org policy check (timeout command not available)"
fi

if [[ "$ORG_POLICY_BLOCKED" == "true" ]]; then
  err "Org policy iam.disableServiceAccountKeyCreation is enforced on this project."
  err "You cannot use a keyfile. Run scripts/05a_setup_wif_optional.sh instead."
  exit 1
fi

# Only create the key if it does not already exist on disk.
if [[ -f "./medibox-edge-key.json" ]]; then
  ok "medibox-edge-key.json already exists — skipping key creation"
  info "Delete it and re-run if you need to rotate the key"
else
  log "Creating service account key for medibox-edge SA"
  run gcloud iam service-accounts keys create "./medibox-edge-key.json" \
    --iam-account="$EDGE_EMAIL" --project="$PROJECT_ID"
  [[ "$DRY_RUN" == "false" ]] && chmod 600 ./medibox-edge-key.json
  ok "Edge key saved to ./medibox-edge-key.json"
  warn "Copy this file securely to the Pi — do not commit it to git"
fi

record EDGE_KEY_FILE "./medibox-edge-key.json"
info "To migrate to WIF later: bash scripts/05a_setup_wif_optional.sh"

# ---------------------------------------------------------------------------
# 6. VPC + Serverless VPC Connector
# ---------------------------------------------------------------------------
header "6 · VPC & Connector"

ensure_vpc_network   "$VPC_NAME"                  "$PROJECT_ID"
ensure_subnet        "$SUBNET_NAME" "$VPC_NAME" "$REGION" "10.0.0.0/24" "$PROJECT_ID"
ensure_vpc_connector "$CONNECTOR_NAME" "$VPC_NAME" "$REGION" "$PROJECT_ID"

# Service Networking peering — required for Cloud SQL private IP (NETWORK_NOT_PEERED fix)
ensure_vpc_peering   "$VPC_NAME" "$PROJECT_ID"

record VPC_NAME      "$VPC_NAME"
record VPC_CONNECTOR "$CONNECTOR_NAME"
ok "VPC, connector, and Service Networking peering ready"

# ---------------------------------------------------------------------------
# 7. Cloud KMS — PII encryption (DD-008)
# ---------------------------------------------------------------------------
header "7 · Cloud KMS"

ensure_kms_keyring "$KMS_KEYRING" "$REGION"  "$PROJECT_ID"
ensure_kms_key     "$KMS_KEY"     "$KMS_KEYRING" "$REGION" "$PROJECT_ID"

# Grant runner SA encrypt/decrypt on the key (idempotent)
run gcloud kms keys add-iam-policy-binding "$KMS_KEY" \
  --keyring="$KMS_KEYRING" --location="$REGION" \
  --member="serviceAccount:${RUNNER_EMAIL}" \
  --role="roles/cloudkms.cryptoKeyEncrypterDecrypter" \
  --project="$PROJECT_ID" --quiet >/dev/null

# Grant worker SA encrypt/decrypt on the key (idempotent)
run gcloud kms keys add-iam-policy-binding "$KMS_KEY" \
  --keyring="$KMS_KEYRING" --location="$REGION" \
  --member="serviceAccount:${WORKER_EMAIL}" \
  --role="roles/cloudkms.cryptoKeyEncrypterDecrypter" \
  --project="$PROJECT_ID" --quiet >/dev/null

record KMS_KEY_NAME "$KMS_KEY_NAME"
ok "KMS key: $KMS_KEY_NAME"

# Provision Cloud SQL service agent + grant KMS access before SQL instance creation
# (ERROR_P4_SA_NOT_FOUND fix — must run before section 11)
ensure_sql_cmek_sa "$KMS_KEY" "$KMS_KEYRING" "$REGION" "$PROJECT_ID"

# ---------------------------------------------------------------------------
# 8. Cloud Storage Buckets
# ---------------------------------------------------------------------------
header "8 · Cloud Storage"

for suffix in raw crops models; do
  BUCKET="${PROJECT_ID}-${suffix}"
  ensure_bucket "$BUCKET" "$REGION" "$PROJECT_ID"
  # These updates are safe to apply on every run (gcloud storage update is idempotent)
  run gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
    --member="serviceAccount:${RUNNER_EMAIL}" \
    --role="roles/storage.objectUser" \
    --project="$PROJECT_ID" --quiet >/dev/null
  record "BUCKET_${suffix^^}" "gs://${BUCKET}"
done

# Crops: 90-day auto-delete lifecycle (idempotent — replaces any existing lifecycle)
cat > /tmp/_crops_lifecycle.json <<'LIFECYCLE'
{
  "rule": [
    {
      "action":    {"type": "Delete"},
      "condition": {"age": 90, "isLive": true}
    }
  ]
}
LIFECYCLE
run gcloud storage buckets update "gs://${PROJECT_ID}-crops" \
  --lifecycle-file=/tmp/_crops_lifecycle.json --project="$PROJECT_ID"
ok "Crops bucket: 90-day delete lifecycle applied"

# Models: versioning enabled (idempotent — enabling is a no-op if already on)
run gcloud storage buckets update "gs://${PROJECT_ID}-models" \
  --versioning --project="$PROJECT_ID"
ok "Models bucket: versioning enabled"

# ---------------------------------------------------------------------------
# 9. BigQuery Dataset + Tables
# ---------------------------------------------------------------------------
header "9 · BigQuery"

# The bq CLI is a Python wrapper.  It uses whatever Python executable was
# configured when gcloud was installed (e.g. python3.13), which may not be
# on PATH.  Override it with the first Python 3 binary we can find.
if [[ -z "${CLOUDSDK_PYTHON:-}" ]]; then
  for _py in python3 python python3.13 python3.12 python3.11 python3.10; do
    if command -v "$_py" &>/dev/null; then
      export CLOUDSDK_PYTHON="$_py"
      info "bq CLI will use: $CLOUDSDK_PYTHON"
      break
    fi
  done
fi

ensure_bq_dataset "$PROJECT_ID" "medibox" "$REGION"

ensure_bq_table "$PROJECT_ID" "medibox" "requests" \
  "Inference request log" \
  "request_id:STRING,ts:TIMESTAMP,device_id:STRING,latency_ms:FLOAT,\
model_version:STRING,structured_json:JSON,confidence:FLOAT,\
formulary_miss:BOOLEAN,low_confidence:BOOLEAN" \
  "ts"

ensure_bq_table "$PROJECT_ID" "medibox" "feedback" \
  "Pharmacist corrections (training data)" \
  "request_id:STRING,reviewer:STRING,corrected_json:JSON,reviewed_at:TIMESTAMP"

ensure_bq_table "$PROJECT_ID" "medibox" "eval_runs" \
  "Model evaluation run results" \
  "run_id:STRING,model_version:STRING,ts:TIMESTAMP,n_samples:INTEGER,\
drug_f1:FLOAT,dosage_accuracy:FLOAT,date_accuracy:FLOAT,\
rare_drug_accuracy:FLOAT,json_validity_rate:FLOAT,passed:BOOLEAN"

# BigQuery IAM is project-level (add-iam-policy-binding is idempotent)
run gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${RUNNER_EMAIL}" \
  --role="roles/bigquery.dataEditor" \
  --condition=None --quiet >/dev/null

record BQ_DATASET "${PROJECT_ID}:medibox"
ok "BigQuery dataset and tables ready"

# ---------------------------------------------------------------------------
# 10. Memorystore Redis
# ---------------------------------------------------------------------------
header "10 · Memorystore Redis"

ensure_redis_instance "medibox-redis" "$REGION" "$VPC_NAME" "$PROJECT_ID"

if [[ "$DRY_RUN" == "false" ]]; then
  # Wait until the instance is READY before trying to read its values.
  # It may still be CREATING on the first run.
  log "Waiting for Redis instance to reach READY state..."
  for _i in $(seq 1 24); do
    _STATE=$(timeout 15 gcloud redis instances describe medibox-redis \
      --region="$REGION" --project="$PROJECT_ID" \
      --format="value(state)" 2>/dev/null || echo "UNKNOWN")
    if [[ "$_STATE" == "READY" ]]; then
      ok "Redis is READY"
      break
    fi
    if [[ $_i -eq 24 ]]; then
      warn "Redis did not reach READY after 4 minutes — re-run the script once it is ready"
      warn "Check status: gcloud redis instances describe medibox-redis --region=$REGION --project=$PROJECT_ID"
    fi
    info "  Redis state: $_STATE — waiting 10 s (attempt $_i/24)..."
    sleep 10
  done

  REDIS_HOST=$(timeout 15 gcloud redis instances describe medibox-redis \
    --region="$REGION" --project="$PROJECT_ID" \
    --format="value(host)" 2>/dev/null || echo "PENDING")
  REDIS_PORT=$(timeout 15 gcloud redis instances describe medibox-redis \
    --region="$REGION" --project="$PROJECT_ID" \
    --format="value(port)" 2>/dev/null || echo "6379")
  record REDIS_HOST "$REDIS_HOST"
  record REDIS_PORT "$REDIS_PORT"
  ok "Redis: ${REDIS_HOST}:${REDIS_PORT}"

  # get-auth-string is slow on some gcloud versions — hard time-box it
  REDIS_AUTH=$(timeout 20 gcloud redis instances get-auth-string medibox-redis \
    --region="$REGION" --project="$PROJECT_ID" \
    --format="value(authString)" 2>/dev/null || echo "")

  if [[ -n "$REDIS_AUTH" ]]; then
    if gcloud secrets describe medibox-redis-auth --project="$PROJECT_ID" &>/dev/null; then
      printf '%s' "$REDIS_AUTH" | run gcloud secrets versions add medibox-redis-auth \
        --data-file=- --project="$PROJECT_ID"
      ok "Redis AUTH string refreshed in Secret Manager"
    else
      printf '%s' "$REDIS_AUTH" | run gcloud secrets create medibox-redis-auth \
        --data-file=- \
        --labels="app=medibox,component=redis" \
        --project="$PROJECT_ID"
      ok "Redis AUTH string stored in Secret Manager"
    fi
    record REDIS_AUTH_SECRET "medibox-redis-auth"
  else
    warn "Could not retrieve Redis AUTH string — re-run the script once Redis is READY"
    warn "Or run manually: gcloud redis instances get-auth-string medibox-redis --region=$REGION --project=$PROJECT_ID"
  fi
fi

# ---------------------------------------------------------------------------
# 11. Cloud SQL Postgres
# ---------------------------------------------------------------------------
header "11 · Cloud SQL Postgres"

ensure_sql_instance  "medibox-postgres" "$REGION" "$VPC_NAME" "$KMS_KEY_NAME" "$PROJECT_ID"
ensure_sql_database  "medibox" "medibox-postgres" "$PROJECT_ID"
ensure_sql_user      "medibox" "medibox-postgres" "$PROJECT_ID"

SQL_CONN="${PROJECT_ID}:${REGION}:medibox-postgres"
record SQL_CONNECTION_NAME "$SQL_CONN"
ok "Cloud SQL connection: $SQL_CONN"

# Cloud SQL client role (idempotent)
run gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${RUNNER_EMAIL}" \
  --role="roles/cloudsql.client" \
  --condition=None --quiet >/dev/null
run gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${WORKER_EMAIL}" \
  --role="roles/cloudsql.client" \
  --condition=None --quiet >/dev/null

# ---------------------------------------------------------------------------
# 12. Secret Manager — placeholder secrets
# ---------------------------------------------------------------------------
header "12 · Secret Manager Placeholders"

for secret in \
  medibox-firebase-admin-json \
  medibox-jwt-signing-key \
  medibox-pii-encryption-key; do
  ensure_secret_placeholder "$secret" "$PROJECT_ID"
done

record SECRET_FIREBASE "medibox-firebase-admin-json"
record SECRET_JWT      "medibox-jwt-signing-key"
record SECRET_PII      "medibox-pii-encryption-key"
record SECRET_DB       "medibox-db-password"
record SECRET_REDIS    "medibox-redis-auth"

# ---------------------------------------------------------------------------
# 13. Cloud Scheduler — monthly retraining trigger
# ---------------------------------------------------------------------------
header "13 · Cloud Scheduler"

ensure_scheduler_job \
  "medibox-monthly-retrain" "$REGION" "$PROJECT_ID" \
  --schedule="0 2 1 * *" \
  --uri="https://us-central1-aiplatform.googleapis.com/v1/projects/${PROJECT_ID}/locations/${REGION}/pipelineJobs" \
  --message-body='{"displayName":"monthly-qlora-retrain","pipelineSpec":{}}' \
  --oauth-service-account-email="$RUNNER_EMAIL" \
  --time-zone="Africa/Tunis" \
  --description="Monthly QLoRA retraining trigger"

# ---------------------------------------------------------------------------
# 14. Cloud Build SA permissions
# ---------------------------------------------------------------------------
header "14 · Cloud Build Permissions"

CB_PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" \
  --format="value(projectNumber)" 2>/dev/null || echo "")

if [[ -n "$CB_PROJECT_NUMBER" ]]; then
  # GCP changed the default Cloud Build identity in mid-2023.
  # Older projects use the Cloud Build legacy SA:
  #   {PROJECT_NUMBER}@cloudbuild.gserviceaccount.com
  # Newer projects (and `gcloud builds submit` on current SDK) use the
  # Compute Engine default SA:
  #   {PROJECT_NUMBER}-compute@developer.gserviceaccount.com
  # We grant roles to BOTH so the script is correct regardless of project age.
  CB_SA_LEGACY="${CB_PROJECT_NUMBER}@cloudbuild.gserviceaccount.com"
  CB_SA_COMPUTE="${CB_PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

  CB_ROLES=(
    roles/run.developer
    roles/iam.serviceAccountUser
    roles/artifactregistry.writer
    roles/cloudsql.client
    roles/secretmanager.secretAccessor
  )

  log "Granting required roles to legacy Cloud Build SA ($CB_SA_LEGACY)"
  for role in "${CB_ROLES[@]}"; do
    run gcloud projects add-iam-policy-binding "$PROJECT_ID" \
      --member="serviceAccount:${CB_SA_LEGACY}" \
      --role="$role" --condition=None --quiet >/dev/null
  done

  log "Granting required roles to Compute Engine default SA ($CB_SA_COMPUTE)"
  for role in "${CB_ROLES[@]}"; do
    run gcloud projects add-iam-policy-binding "$PROJECT_ID" \
      --member="serviceAccount:${CB_SA_COMPUTE}" \
      --role="$role" --condition=None --quiet >/dev/null
  done

  record CLOUD_BUILD_SA_LEGACY  "$CB_SA_LEGACY"
  record CLOUD_BUILD_SA_COMPUTE "$CB_SA_COMPUTE"
  ok "Cloud Build SA permissions bound (both legacy and Compute Engine default SAs)"
fi

# ---------------------------------------------------------------------------
# 15. Write final resource summary
# ---------------------------------------------------------------------------
header "15 · Resource Summary"

if [[ "$DRY_RUN" == "false" ]]; then
  {
    echo "# Medibox GCP Resources — generated $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
    echo "# DO NOT COMMIT THIS FILE — it contains resource IDs"
    echo ""
  } > /tmp/_gcp_header.txt
  cat /tmp/_gcp_header.txt "$RESOURCES_FILE" > /tmp/_gcp_final.txt 2>/dev/null \
    || cp /tmp/_gcp_header.txt /tmp/_gcp_final.txt
  mv /tmp/_gcp_final.txt "$RESOURCES_FILE"
  ok "Resource IDs written to: $RESOURCES_FILE"
fi

echo ""
echo -e "${BOLD}${GREEN}════════════════════════════════${NC}"
echo -e "${BOLD}${GREEN}  Setup Complete!${NC}"
echo -e "${BOLD}${GREEN}════════════════════════════════${NC}"
echo ""
echo -e "  Project:   ${BOLD}$PROJECT_ID${NC}"
echo -e "  Region:    ${BOLD}$REGION${NC}"
echo -e "  Registry:  ${BOLD}${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}${NC}"
echo -e "  Runner SA: ${BOLD}$RUNNER_EMAIL${NC}"
echo -e "  Worker SA: ${BOLD}$WORKER_EMAIL${NC}"
echo -e "  Edge SA:   ${BOLD}$EDGE_EMAIL${NC}"
echo -e "  KMS Key:   ${BOLD}$KMS_KEY_NAME${NC}"
echo -e "  SQL Conn:  ${BOLD}${PROJECT_ID}:${REGION}:medibox-postgres${NC}"
echo ""
echo -e "${YELLOW}  ⚠ Next: run 02_setup_secrets.sh to populate placeholder secrets${NC}"
echo ""
