# =============================================================================
# Medibox — single-service image (API + arq worker in one process)
# Base: python:3.11-slim  (pinned by digest in CI via Trivy/cosign)
# Non-root: appuser (uid 1001)
# Security: no capabilities dropped at image level — use seccomp at runtime
# =============================================================================
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps:
#   libpq-dev gcc   — psycopg2 build (Alembic only; asyncpg is pure Python)
#   libgl1 libglib2 — OpenCV / Pillow image processing
#   curl            — health check probe
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc libgl1 libglib2.0-0 curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (cached layer — invalidated only on requirements change)
COPY services/api/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# ---------------------------------------------------------------------------
# Reference data + spaCy NER model bake
#
# LAYER ORDER: referances/ and NER bake come BEFORE application code.
# This means Python source changes don't invalidate the 20-minute NER layer.
# Only changes to referances/ or requirements trigger a rebuild of this layer.
# ---------------------------------------------------------------------------
COPY referances/ /app/referances/

# Build + serialise the spaCy NER pipeline into the image at build time.
# Loading from on-disk format takes <5 s at container startup vs ~20 min rebuild.
RUN python3 -c "\
import sys, json, time; sys.path.insert(0, '/app'); \
import spacy; \
t0 = time.time(); \
REFS = '/app/referances'; \
nlp = spacy.blank('fr'); \
ruler = nlp.add_pipe('entity_ruler', config={'phrase_matcher_attr': 'LOWER'}); \
patterns = []; \
reg = json.load(open(f'{REFS}/drug_registry.json')); \
[patterns.append({'label':'DRUG','pattern':n.strip()}) for i,n in enumerate(reg) if i<3000 and n and len(n.strip())>=3]; \
dct = json.load(open(f'{REFS}/drug_dict.json')); \
[patterns.append({'label':'DRUG_INN','pattern':d.strip()}) for i,d in enumerate(dct) if i<1000 and d and len(d.strip())>=3]; \
ruler.add_patterns(patterns); \
nlp.to_disk(f'{REFS}/ner_model'); \
print(f'NER baked: {len(patterns)} patterns in {time.time()-t0:.1f}s')"

# ---------------------------------------------------------------------------
# Application code (placed AFTER NER bake to preserve cache)
# ---------------------------------------------------------------------------
COPY services/api/     /app/services/api/
COPY services/worker/  /app/services/worker/
COPY services/vllm/    /app/services/vllm/
COPY migrations/       /app/migrations/
COPY services/__init__.py /app/services/__init__.py

# Pre-compile for faster startup
RUN python3 -m compileall -q /app/services/ || true

# ---------------------------------------------------------------------------
# Non-root user — security hardening
# ---------------------------------------------------------------------------
RUN addgroup --gid 1001 appgroup \
    && adduser --uid 1001 --gid 1001 --no-create-home --disabled-password appuser \
    && chown -R appuser:appgroup /app

USER appuser

EXPOSE 8080

# Single uvicorn worker (arq uses the event loop — multiple uvicorn workers
# would spawn multiple arq workers competing for the same GPU job).
CMD ["/bin/sh", "-c", \
     "exec uvicorn services.api.main:app \
      --host 0.0.0.0 \
      --port ${PORT:-8080} \
      --workers 1 \
      --loop uvloop \
      --http httptools \
      --log-level warning \
      --no-access-log"]
