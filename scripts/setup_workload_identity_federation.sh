#!/usr/bin/env bash
# =============================================================================
# Workload Identity Federation setup for edge Raspberry Pi devices (DD-010)
# Enables the Pi to authenticate as medibox-edge SA using short-lived OIDC tokens
# from a custom identity provider (or GitHub Actions for CI).
# =============================================================================
set -euo pipefail

: "${PROJECT_ID:?}" "${EDGE_EMAIL:=${SA_EDGE:-medibox-edge}@${PROJECT_ID}.iam.gserviceaccount.com}"
REGION="${REGION:-us-central1}"
POOL_ID="${POOL_ID:-medibox-edge-pool}"
PROVIDER_ID="${PROVIDER_ID:-medibox-oidc-provider}"
ISSUER_URI="${ISSUER_URI:-}"  # Your OIDC provider URI (e.g., https://keycloak.example.com/realms/medibox)

log() { echo "[$(date -u +%H:%M:%S)] $*"; }

if [[ -z "$ISSUER_URI" ]]; then
  log "ISSUER_URI not set — skipping WIF setup"
  log "Set ISSUER_URI to your OIDC provider and re-run"
  exit 1
fi

# 1. Create workload identity pool
log "Creating Workload Identity Pool: $POOL_ID"
gcloud iam workload-identity-pools create "$POOL_ID" \
  --location=global \
  --display-name="Medibox Edge Pool" \
  --description="WIF pool for Raspberry Pi edge devices" 2>/dev/null || true

# 2. Create OIDC provider
log "Creating OIDC provider: $PROVIDER_ID"
gcloud iam workload-identity-pools providers create-oidc "$PROVIDER_ID" \
  --location=global \
  --workload-identity-pool="$POOL_ID" \
  --display-name="Medibox OIDC Provider" \
  --issuer-uri="$ISSUER_URI" \
  --allowed-audiences="medibox-edge" \
  --attribute-mapping="google.subject=assertion.sub,attribute.device_id=assertion.device_id" 2>/dev/null || true

POOL_NAME="projects/${PROJECT_ID}/locations/global/workloadIdentityPools/${POOL_ID}"

# 3. Grant the pool permission to impersonate the edge SA
log "Binding WIF pool to edge SA"
gcloud iam service-accounts add-iam-policy-binding "$EDGE_EMAIL" \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/${POOL_NAME}/attribute.device_id/*"

# 4. Generate credential configuration file for the Pi
CRED_CONFIG_PATH="./medibox-edge-wif-config.json"
gcloud iam workload-identity-pools create-cred-config \
  "${POOL_NAME}/providers/${PROVIDER_ID}" \
  --service-account="$EDGE_EMAIL" \
  --output-file="$CRED_CONFIG_PATH" \
  --credential-source-file="/var/run/medibox/oidc-token" \
  --credential-source-type=text

log "WIF credential config saved to $CRED_CONFIG_PATH"
log "Deploy this file to the Pi at /etc/medibox/wif-config.json"
log "Pi must write its OIDC token to /var/run/medibox/oidc-token before each request"
log ""
log "On the Pi, set: GOOGLE_APPLICATION_CREDENTIALS=/etc/medibox/wif-config.json"
