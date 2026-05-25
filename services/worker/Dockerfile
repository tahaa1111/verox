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
    python3 -c "import celery, redis, psycopg2, structlog, rapidfuzz, json_repair, httpx, google.cloud.storage, PIL, pydantic, pydantic_settings, tenacity, cryptography, dateutil, json_repair, spacy, symspellpy"

# ---------------------------------------------------------------------------
# LAYER ORDER: referances/ and NER bake come BEFORE worker Python code.
# This means code-only changes (celery_app.py etc.) don't invalidate the
# 20-minute NER layer — only changes to referances/ or requirements do.
# ---------------------------------------------------------------------------
COPY referances/ /app/referances/

# Build + serialise the spaCy NER pipeline into the image.
# Running ruler.add_patterns() at image-build time (once, on CI hardware)
# means containers load the model from disk in < 5 s instead of rebuilding
# it at runtime in ~20 min on every cold start.
# Output: /app/referances/ner_model/  (spaCy on-disk format)
RUN python3 -c "\
import sys, json, os, time; sys.path.insert(0, '/app'); \
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
print(f'NER model serialised: {len(patterns)} patterns in {time.time()-t0:.1f}s -> {REFS}/ner_model')"

# Worker application code — placed AFTER NER bake so code edits don't
# invalidate the expensive NER layer above.
COPY services/worker/ /app/services/worker/
COPY services/vllm/prompts/ /app/services/vllm/prompts/
COPY services/api/core/pii.py /app/services/api/core/pii.py
COPY services/api/core/config.py /app/services/api/core/config.py
COPY services/api/core/__init__.py /app/services/api/core/__init__.py
COPY services/api/__init__.py /app/services/api/__init__.py
COPY services/__init__.py /app/services/__init__.py

# Pre-compile all application code to .pyc files for faster Celery startup.
RUN python3 -m compileall -q /app/services/worker/ /app/services/api/core/ || true

RUN addgroup --gid 1001 appgroup && adduser --uid 1001 --gid 1001 --no-create-home appuser
USER appuser

EXPOSE 8080

# Start minimal HTTP health server (Cloud Run startup probe) then exec Celery as PID 1.
# health_server.py binds to $PORT (Cloud Run default 8080) and responds 200 to any GET.
CMD ["/bin/sh", "-c", "python3 /app/services/worker/health_server.py & exec celery -A services.worker.celery_app worker --loglevel=info --concurrency=1 --queues=inference,default --pool=solo"]
