# =============================================================================
# Medibox API — Cloud Run container
# =============================================================================
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc curl \
    && rm -rf /var/lib/apt/lists/*

# Install Cloud SQL Auth Proxy (downloaded separately by Cloud Run via --add-cloudsql-instances)
# Proxy is managed by the Cloud Run platform; no manual install needed here.

COPY services/api/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy application source
COPY services/api/ /app/services/api/
COPY services/worker/utils/grid_composer.py /app/services/worker/utils/grid_composer.py
COPY services/worker/utils/__init__.py /app/services/worker/utils/__init__.py
COPY services/worker/__init__.py /app/services/worker/__init__.py
COPY referances/ /app/referances/
COPY migrations/ /app/migrations/

# Non-root user
RUN addgroup --gid 1001 appgroup && adduser --uid 1001 --gid 1001 --no-create-home appuser
USER appuser

EXPOSE 8080

# Cloud Run sets PORT env var (default 8080). Use shell form so ${PORT:-8080} expands.
CMD ["/bin/sh", "-c", "exec uvicorn services.api.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 4 --loop uvloop --http httptools --log-level warning --no-access-log"]
