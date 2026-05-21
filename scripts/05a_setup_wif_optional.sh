#!/usr/bin/env bash
# =============================================================================
# Medibox — Optional: Workload Identity Federation for edge device
#
# Run this ONLY after the main deployment is stable and working with keyfile
# auth (01_setup_project.sh + 02_setup_secrets.sh + 03_first_deploy.sh).
# This script is a v2 hardening step — it is not required for a working deploy.
#
# What this does:
#   1. Creates a Workload Identity Pool (medibox-wif-pool)
#   2. Creates an OIDC provider inside the pool configured for the Pi's
#      identity (device serial or X.509 cert)
#   3. Binds the medibox-edge SA to the pool with roles/iam.workloadIdentityUser
#   4. Generates a WIF credential config file to copy to the Pi
#   5. Tells you how to update MEDIBOX_AUTH_MODE=wif on the Pi
#
# After this script, the Pi no longer needs the medibox-edge-key.json file on
# disk.  The key can be revoked once you confirm WIF is working.
#
# Usage:
#   set -a && source .env.deploy && set +a
#   bash scripts/05a_setup_wif_optional.sh [--dry-run]
#
# Known issue — IAM propagation race condition:
#   GCP's Workload Identity API may return "Identity Pool does not exist" in
#   the seconds immediately after pool creation, even though the pool was just
#   successfully created.  This is a server-side eventual-consistency issue.
#   The script retries the binding step automatically with exponential backoff
#   (up to 5 attempts, 5s/10s/20s/40s between retries).
#   If it still fails after all retries, wait 60 seconds and re-run the script
#   from step 3 — the pool creation step is idempotent and will be skipped.
# =============================================================================
set -euo pipefail

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

DRY_RUN=false
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=true ;;
    *) err "Unknown flag: $arg"; exit 1 ;;
  esac
done

run() {
  if [[ "$DRY_RUN" == "true" ]]; then
    echo -e "  ${YELLOW}[DRY-RUN]${NC} $*"
  else
    "$@"
  fi
}

[[ "$DRY_RUN" == "true" ]] && warn "DRY-RUN mode — no changes will be made"

: "${PROJECT_ID:?PROJECT_ID must be set — source .env.deploy first}"
REGION="${REGION:-us-central1}"
SA_EDGE="medibox-edge"
EDGE_EMAIL="${SA_EDGE}@${PROJECT_ID}.iam.gserviceaccount.com"
WIF_POOL="medibox-wif-pool"
WIF_PROVIDER="medibox-edge-provider"
CRED_CONFIG_OUT="./medibox-wif-cred-config.json"

echo ""
header "Medibox WIF Setup (optional hardening)"
info "Project:  $PROJECT_ID"
info "Edge SA:  $EDGE_EMAIL"
info "Dry-run:  $DRY_RUN"
echo ""

# ---------------------------------------------------------------------------
# Pre-flight: confirm the deployment is already working
# ---------------------------------------------------------------------------
header "Pre-flight"

if [[ ! -f "gcp_resources.txt" ]]; then
  err "gcp_resources.txt not found — run 01_setup_project.sh first"
  exit 1
fi
ok "gcp_resources.txt exists"

if ! gcloud iam service-accounts describe "$EDGE_EMAIL" \
     --project="$PROJECT_ID" &>/dev/null; then
  err "Service account $EDGE_EMAIL does not exist — run 01_setup_project.sh first"
  exit 1
fi
ok "Edge SA $EDGE_EMAIL exists"

# ---------------------------------------------------------------------------
# Step 1: Resolve project number (required for IAM principal path)
# ---------------------------------------------------------------------------
header "Step 1 · Resolve project number"

PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" \
  --format="value(projectNumber)" 2>/dev/null || echo "")
if [[ -z "$PROJECT_NUMBER" ]]; then
  err "Could not resolve numeric project number for $PROJECT_ID"
  exit 1
fi
ok "Project number: $PROJECT_NUMBER"

WIF_POOL_NAME="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${WIF_POOL}"

# ---------------------------------------------------------------------------
# Step 2: Create WIF pool (idempotent)
# ---------------------------------------------------------------------------
header "Step 2 · Workload Identity Pool"

if gcloud iam workload-identity-pools describe "$WIF_POOL" \
     --location=global --project="$PROJECT_ID" &>/dev/null; then
  ok "WIF pool '$WIF_POOL' already exists"
else
  log "Creating Workload Identity Pool: $WIF_POOL"
  run gcloud iam workload-identity-pools create "$WIF_POOL" \
    --location=global \
    --display-name="Medibox Edge WIF Pool" \
    --description="Allows Raspberry Pi edge devices to authenticate without a keyfile" \
    --project="$PROJECT_ID"
  ok "Pool created: $WIF_POOL_NAME"

  # The IAM binding below may fail immediately after pool creation due to
  # GCP-side propagation delay.  Wait a moment before proceeding.
  if [[ "$DRY_RUN" == "false" ]]; then
    log "Waiting 15 s for pool to propagate before binding..."
    sleep 15
  fi
fi

# ---------------------------------------------------------------------------
# Step 3: Create OIDC provider (idempotent)
#
# This uses a self-signed OIDC issuer.  For a production setup you would
# replace ISSUER_URI with your TPM attestation endpoint or your own OIDC
# service.  For development/testing, a simple file-based credential works.
# ---------------------------------------------------------------------------
header "Step 3 · OIDC Provider"

# Default: use a file-based credential source (simplest for Pi without TPM)
ISSUER_URI="https://accounts.google.com"    # placeholder — override for real OIDC

if gcloud iam workload-identity-pools providers describe "$WIF_PROVIDER" \
     --workload-identity-pool="$WIF_POOL" \
     --location=global --project="$PROJECT_ID" &>/dev/null; then
  ok "WIF provider '$WIF_PROVIDER' already exists"
else
  log "Creating OIDC provider: $WIF_PROVIDER"
  warn "Using Google OIDC as issuer URI (placeholder)."
  warn "For real Pi attestation, replace --issuer-uri with your device OIDC endpoint."
  run gcloud iam workload-identity-pools providers create-oidc "$WIF_PROVIDER" \
    --workload-identity-pool="$WIF_POOL" \
    --location=global \
    --issuer-uri="$ISSUER_URI" \
    --attribute-mapping="google.subject=assertion.sub,attribute.device_id=assertion.device_id" \
    --description="Medibox Raspberry Pi OIDC provider" \
    --project="$PROJECT_ID"
  ok "OIDC provider created"
fi

# ---------------------------------------------------------------------------
# Step 4: Bind edge SA to WIF pool (with retry for propagation race)
# ---------------------------------------------------------------------------
header "Step 4 · IAM Binding (with retry)"

PRINCIPAL="principalSet://iam.googleapis.com/${WIF_POOL_NAME}/*"
log "Binding $EDGE_EMAIL to pool principal $PRINCIPAL"

if [[ "$DRY_RUN" == "true" ]]; then
  run gcloud iam service-accounts add-iam-policy-binding "$EDGE_EMAIL" \
    --role="roles/iam.workloadIdentityUser" \
    --member="$PRINCIPAL" \
    --project="$PROJECT_ID" --quiet
else
  MAX_ATTEMPTS=5
  WAIT=5
  for ((attempt=1; attempt<=MAX_ATTEMPTS; attempt++)); do
    if gcloud iam service-accounts add-iam-policy-binding "$EDGE_EMAIL" \
         --role="roles/iam.workloadIdentityUser" \
         --member="$PRINCIPAL" \
         --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
      ok "IAM binding succeeded (attempt $attempt)"
      break
    fi
    if [[ $attempt -eq $MAX_ATTEMPTS ]]; then
      err "IAM binding failed after $MAX_ATTEMPTS attempts."
      err "Known race condition: the pool exists but IAM doesn't see it yet."
      err "Wait 60 s and re-run this script — pool creation will be skipped."
      exit 1
    fi
    warn "Binding failed (attempt $attempt/$MAX_ATTEMPTS) — retrying in ${WAIT}s..."
    sleep "$WAIT"
    WAIT=$((WAIT * 2))
  done
fi

# ---------------------------------------------------------------------------
# Step 5: Generate credential config file for the Pi
# ---------------------------------------------------------------------------
header "Step 5 · Credential Config"

if [[ -f "$CRED_CONFIG_OUT" ]]; then
  ok "$CRED_CONFIG_OUT already exists — skipping generation"
  info "Delete it and re-run if you want to regenerate"
else
  log "Generating WIF credential config: $CRED_CONFIG_OUT"
  run gcloud iam workload-identity-pools create-cred-config \
    "${WIF_POOL_NAME}/providers/${WIF_PROVIDER}" \
    --service-account="$EDGE_EMAIL" \
    --credential-source-file="/run/medibox/device-token" \
    --credential-source-type=text \
    --output-file="$CRED_CONFIG_OUT"
  [[ "$DRY_RUN" == "false" ]] && chmod 600 "$CRED_CONFIG_OUT"
  ok "Credential config written to $CRED_CONFIG_OUT"
fi

# ---------------------------------------------------------------------------
# Done — print Pi-side instructions
# ---------------------------------------------------------------------------
echo ""
echo -e "${BOLD}${GREEN}════════════════════════════════${NC}"
echo -e "${BOLD}${GREEN}  WIF Setup Complete${NC}"
echo -e "${BOLD}${GREEN}════════════════════════════════${NC}"
echo ""
echo -e "  Next steps on the Raspberry Pi:"
echo ""
echo -e "  1. Copy the credential config to the Pi:"
echo -e "     ${BOLD}scp $CRED_CONFIG_OUT pi@\$EDGE_DEVICE_IP:/etc/medibox/medibox-edge.json${NC}"
echo ""
echo -e "  2. Update /etc/medibox/edge.env on the Pi:"
echo -e "     ${BOLD}MEDIBOX_AUTH_MODE=wif${NC}"
echo -e "     ${BOLD}MEDIBOX_CREDENTIALS_PATH=/etc/medibox/medibox-edge.json${NC}"
echo ""
echo -e "  3. Restart the edge service:"
echo -e "     ${BOLD}sudo systemctl restart medibox-edge${NC}"
echo ""
echo -e "  4. Verify it works, then revoke the old keyfile:"
echo -e "     ${BOLD}gcloud iam service-accounts keys list --iam-account=$EDGE_EMAIL --project=$PROJECT_ID${NC}"
echo -e "     ${BOLD}gcloud iam service-accounts keys delete KEY_ID --iam-account=$EDGE_EMAIL --project=$PROJECT_ID${NC}"
echo ""
warn "Do not delete the keyfile until you have confirmed WIF auth is working."
echo ""
