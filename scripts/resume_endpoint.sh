#!/usr/bin/env bash
# Redeploy the latest active model to the Vertex AI Endpoint.
# Takes ~5 minutes. Run before pharmacy opening hours.
set -euo pipefail

: "${PROJECT_ID:?}" "${VERTEX_ENDPOINT_ID:?}" "${VERTEX_MODEL_ID:?VERTEX_MODEL_ID must be set}"
REGION="${REGION:-us-central1}"

log() { echo "[$(date -u +%H:%M:%S)] $*"; }
log "Resuming endpoint $VERTEX_ENDPOINT_ID with model $VERTEX_MODEL_ID"

gcloud ai endpoints deploy-model "$VERTEX_ENDPOINT_ID" \
  --region="$REGION" \
  --model="$VERTEX_MODEL_ID" \
  --display-name="medibox-vllm-resumed" \
  --machine-type="n1-standard-4" \
  --accelerator="type=nvidia-tesla-t4,count=1" \
  --min-replica-count=1 \
  --max-replica-count=3 \
  --traffic-split="0=100"

log "Endpoint resumed. Warmup in progress (~5 min)."
log "Monitor: gcloud ai endpoints describe $VERTEX_ENDPOINT_ID --region=$REGION"
