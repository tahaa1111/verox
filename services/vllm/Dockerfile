# =============================================================================
# Medibox vLLM — Vertex AI custom container (DD-011)
# Base: vLLM official with CUDA 12.x + Python 3.11
# Conforms to Vertex AI custom-container prediction contract:
#   Health: GET $AIP_HEALTH_ROUTE (default /health)
#   Predict: POST $AIP_PREDICT_ROUTE (default /predict)
#   Port: $AIP_HTTP_PORT (default 8080)
# =============================================================================
FROM vllm/vllm-openai:v0.6.6.post1

ENV PYTHONUNBUFFERED=1 \
    AIP_HTTP_PORT=8080 \
    AIP_HEALTH_ROUTE=/health \
    AIP_PREDICT_ROUTE=/predict \
    HF_HUB_ENABLE_HF_TRANSFER=1

# Runtime deps — only curl needed for healthcheck; gcloud SDK not required
# since the model is downloaded from HuggingFace Hub at startup.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Upgrade transformers to >=4.47.0 — required for Qwen2.5-VL (qwen2_5_vl architecture).
# v0.6.6 ships with transformers that lacks qwen2_5_vl in its CONFIG_MAPPING.
# --force-reinstall bypasses any conflicting pin that would keep the old version.
# CRITICAL: do NOT touch torch, autoawq, or the vLLM CUDA kernels — only transformers.
RUN pip show transformers 2>&1 | grep -E "^(Name|Version)" && \
    pip install --no-cache-dir --force-reinstall "transformers==4.49.0" && \
    python3 -c "import transformers; v=transformers.__version__; print('transformers installed:', v); assert v >= '4.49', f'UPGRADE FAILED: {v}'" && \
    python3 -c "from transformers import Qwen2_5_VLConfig; print('Qwen2_5_VLConfig: FOUND', Qwen2_5_VLConfig)"

# Install wrapper server dependencies on top of vLLM image.
RUN pip install --no-cache-dir \
    "fastapi>=0.115.0,<0.120" \
    "uvicorn[standard]>=0.30.6,<0.35" \
    "httpx>=0.27.2,<0.30" \
    "structlog>=24.4.0" \
    "Pillow>=10.4.0,<12"

WORKDIR /app

COPY services/vllm/ /app/services/vllm/
COPY services/__init__.py /app/services/__init__.py

# Health check matches Vertex AI health probe
HEALTHCHECK --interval=30s --timeout=15s --start-period=600s --retries=10 \
    CMD curl -sf http://localhost:${AIP_HTTP_PORT}${AIP_HEALTH_ROUTE} || exit 1

EXPOSE ${AIP_HTTP_PORT}

# Use shell form so $PATH is resolved at runtime (vLLM image uses conda Python).
# Cloud Run injects PORT env var; Vertex AI uses AIP_HTTP_PORT — both default to 8080.
ENTRYPOINT ["/bin/bash", "-c", "cd /app && exec python3 -m services.vllm.server"]
