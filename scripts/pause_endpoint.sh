#!/usr/bin/env bash
# Undeploy the active model from the Vertex AI Endpoint to save ~$0.35/h (DD-001).
# Resume takes ~5 minutes. Use for extended off-hours periods.
set -euo pipefail

: "${PROJECT_ID:?}" "${VERTEX_ENDPOINT_ID:?VERTEX_ENDPOINT_ID must be set}"
REGION="${REGION:-us-central1}"

log() { echo "[$(date -u +%H:%M:%S)] $*"; }

# Get deployed model ID
DEPLOYED_ID=$(gcloud ai endpoints describe "$VERTEX_ENDPOINT_ID" \
  --region="$REGION" \
  --format="value(deployedModels[0].id)")

if [[ -z "$DEPLOYED_ID" ]]; then
  log "No deployed model found — endpoint already paused"
  exit 0
fi

log "Undeploying model $DEPLOYED_ID from endpoint $VERTEX_ENDPOINT_ID"
gcloud ai endpoints undeploy-model "$VERTEX_ENDPOINT_ID" \
  --region="$REGION" \
  --deployed-model-id="$DEPLOYED_ID"

log "Endpoint paused. GPU billing stopped."
log "To resume: bash scripts/resume_endpoint.sh"
