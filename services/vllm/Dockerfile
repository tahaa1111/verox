# =============================================================================
# Medibox vLLM — Cloud Run GPU container
# Base: vLLM v0.7.3 — CUDA 12.4, Python 3.12
#   First vLLM release with Qwen2_5_VLForConditionalGeneration in its registry.
# Conforms to Vertex AI custom-container prediction contract:
#   Health: GET $AIP_HEALTH_ROUTE (default /health)
#   Predict: POST $AIP_PREDICT_ROUTE (default /predict)
#   Port: $AIP_HTTP_PORT (default 8080)
# =============================================================================
FROM vllm/vllm-openai:v0.7.3

ENV PYTHONUNBUFFERED=1 \
    AIP_HTTP_PORT=8080 \
    AIP_HEALTH_ROUTE=/health \
    AIP_PREDICT_ROUTE=/predict \
    HF_HUB_ENABLE_HF_TRANSFER=1

# Runtime deps — only curl needed for healthcheck.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Verify Qwen2.5-VL is supported — fails build immediately if wrong vLLM version.
# v0.7.3+ registers Qwen2_5_VLForConditionalGeneration; v0.6.x does not.
RUN python3 -c "from transformers import Qwen2_5_VLConfig; print('OK: Qwen2_5_VLConfig importable')" && \
    python3 -c "import vllm; print('vLLM version:', vllm.__version__)"

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

ENTRYPOINT ["/bin/bash", "-c", "cd /app && exec python3 -m services.vllm.server"]
