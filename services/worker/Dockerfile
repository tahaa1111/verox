# =============================================================================
# Medibox Worker — Cloud Run service (Celery, min-instances=1)
# =============================================================================
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY services/worker/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt && \
    python3 -c "import celery, redis, psycopg2, structlog, rapidfuzz, json_repair, httpx, google.cloud.storage, PIL, pydantic, pydantic_settings, tenacity, cryptography, dateutil, json_repair"

COPY services/worker/ /app/services/worker/
COPY services/vllm/prompts/ /app/services/vllm/prompts/
COPY services/api/core/pii.py /app/services/api/core/pii.py
COPY services/api/core/config.py /app/services/api/core/config.py
COPY services/api/core/__init__.py /app/services/api/core/__init__.py
COPY services/api/__init__.py /app/services/api/__init__.py
COPY services/__init__.py /app/services/__init__.py
COPY referances/ /app/referances/

# Pre-compile all application code to .pyc files for faster Celery startup.
# This runs AFTER all COPY steps so the app code .pyc files are baked in.
RUN python3 -c "\
import sys; sys.path.insert(0, '/app'); \
import services.worker.celery_app; \
import services.worker.tasks.inference; \
import services.worker.tasks.postprocessing; \
print('app pre-warm complete')" && \
    python3 -m compileall -q /app/services/worker/ /app/services/api/core/ || true

RUN addgroup --gid 1001 appgroup && adduser --uid 1001 --gid 1001 --no-create-home appuser
USER appuser

EXPOSE 8080

# Start minimal HTTP health server (Cloud Run startup probe) then exec Celery as PID 1.
# health_server.py binds to $PORT (Cloud Run default 8080) and responds 200 to any GET.
CMD ["/bin/sh", "-c", "python3 /app/services/worker/health_server.py & exec celery -A services.worker.celery_app worker --loglevel=info --concurrency=1 --queues=inference,default --pool=solo"]
