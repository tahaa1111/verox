# =============================================================================
# Medibox vLLM — Vertex AI custom container (DD-011)
# Base: vLLM official with CUDA 12.1 + Python 3.11
# Conforms to Vertex AI custom-container prediction contract:
#   Health: GET $AIP_HEALTH_ROUTE (default /health)
#   Predict: POST $AIP_PREDICT_ROUTE (default /predict)
#   Port: $AIP_HTTP_PORT (default 8080)
# =============================================================================
FROM vllm/vllm-openai:v0.6.6.post1

ENV PYTHONUNBUFFERED=1 \
    AIP_HTTP_PORT=8080 \
    AIP_HEALTH_ROUTE=/health \
    AIP_PREDICT_ROUTE=/predict

# Runtime deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl gnupg lsb-release \
    && curl -sSL https://sdk.cloud.google.com | bash -s -- --disable-prompts \
    && rm -rf /var/lib/apt/lists/*

ENV PATH="/root/google-cloud-sdk/bin:${PATH}"

RUN pip install --no-cache-dir \
    autoawq==0.2.7 \
    qwen-vl-utils==0.0.8 \
    fastapi==0.115.0 \
    uvicorn[standard]==0.30.6 \
    httpx==0.27.2 \
    structlog==24.4.0 \
    Pillow==10.4.0

WORKDIR /app

COPY services/vllm/ /app/services/vllm/
COPY services/__init__.py /app/services/__init__.py

# Health check matches Vertex AI health probe
HEALTHCHECK --interval=30s --timeout=15s --start-period=300s --retries=5 \
    CMD curl -sf http://localhost:${AIP_HTTP_PORT}${AIP_HEALTH_ROUTE} || exit 1

EXPOSE ${AIP_HTTP_PORT}

ENTRYPOINT ["python", "-m", "services.vllm.server"]
