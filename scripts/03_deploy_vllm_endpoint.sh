#!/usr/bin/env bash
# =============================================================================
# Build, push, register, and deploy vLLM container to Vertex AI Endpoint
# Spec §5.2
# Usage: bash scripts/03_deploy_vllm_endpoint.sh [--tag v3] [--model-gcs gs://...]
# =============================================================================
set -euo pipefail

: "${PROJECT_ID:?PROJECT_ID must be set}"
REGION="${REGION:-us-central1}"
REPO="${REPO:-medibox-repo}"
TAG="${TAG:-latest}"
MODEL_GCS="${MODEL_GCS:-}"  # gs://${PROJECT_ID}-models/vN/ — if empty, downloads from HF
ENDPOINT_DISPLAY_NAME="${ENDPOINT_DISPLAY_NAME:-medibox-vllm}"

IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/medibox-vllm:${TAG}"
log() { echo "[$(date -u +%H:%M:%S)] $*"; }

for arg in "$@"; do
  case "$arg" in
    --tag=*) TAG="${arg#*=}" ;;
    --model-gcs=*) MODEL_GCS="${arg#*=}" ;;
  esac
done

# ---------------------------------------------------------------------------
# 1. Build and push container
# ---------------------------------------------------------------------------
log "Building vLLM container: $IMAGE"
docker build \
  -f services/vllm/Dockerfile \
  -t "$IMAGE" \
  .

log "Pushing to Artifact Registry"
docker push "$IMAGE"

# ---------------------------------------------------------------------------
# 2. Register Model in Vertex AI Model Registry
# ---------------------------------------------------------------------------
MODEL_DISPLAY_NAME="medibox-vllm-${TAG}"
log "Registering Model in Vertex AI Model Registry: $MODEL_DISPLAY_NAME"

VERTEX_MODEL_ID=$(gcloud ai models upload \
  --region="$REGION" \
  --display-name="$MODEL_DISPLAY_NAME" \
  --container-image-uri="$IMAGE" \
  --container-health-route="/health" \
  --container-predict-route="/predict" \
  --container-ports=8080 \
  ${MODEL_GCS:+--artifact-uri="$MODEL_GCS"} \
  --format="value(model)" \
  2>/dev/null)

log "Model registered: $VERTEX_MODEL_ID"

# ---------------------------------------------------------------------------
# 3. Get or create Endpoint
# ---------------------------------------------------------------------------
ENDPOINT_ID=$(gcloud ai endpoints list \
  --region="$REGION" \
  --filter="displayName:${ENDPOINT_DISPLAY_NAME}" \
  --format="value(name)" | head -1)

if [[ -z "$ENDPOINT_ID" ]]; then
  log "Creating Vertex AI Endpoint: $ENDPOINT_DISPLAY_NAME"
  ENDPOINT_ID=$(gcloud ai endpoints create \
    --region="$REGION" \
    --display-name="$ENDPOINT_DISPLAY_NAME" \
    --format="value(name)")
fi

log "Endpoint: $ENDPOINT_ID"

# ---------------------------------------------------------------------------
# 4. Deploy Model to Endpoint
# ---------------------------------------------------------------------------
log "Deploying model to endpoint (this takes 5-10 min)"
gcloud ai endpoints deploy-model "$ENDPOINT_ID" \
  --region="$REGION" \
  --model="$VERTEX_MODEL_ID" \
  --display-name="medibox-vllm-${TAG}" \
  --machine-type="n1-standard-4" \
  --accelerator="type=nvidia-tesla-t4,count=1" \
  --min-replica-count=1 \
  --max-replica-count=3 \
  --traffic-split="0=100"

log "Deployment complete"
log "  Endpoint:     $ENDPOINT_ID"
log "  Model:        $VERTEX_MODEL_ID"
log "  Image:        $IMAGE"
log ""
log "Update Cloud Run VERTEX_ENDPOINT_ID environment variable:"
log "  gcloud run services update medibox-worker --update-env-vars VERTEX_ENDPOINT_ID=$(basename $ENDPOINT_ID)"
