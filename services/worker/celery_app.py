"""Celery application configured for Memorystore Redis."""

from __future__ import annotations

import os

from celery import Celery

# REDIS_URL is set as a plain env var by the deploy scripts (not a secret ref).
# CELERY_BROKER_URL / CELERY_RESULT_BACKEND are optional secret refs; fall back
# to REDIS_URL (db=0 for broker, db=1 for backend) when they are absent.
_redis_base = os.getenv("REDIS_URL", "redis://localhost:6379")
# Strip trailing db index from REDIS_URL so we can append /0 and /1 cleanly
_redis_base_no_db = _redis_base.rsplit("/", 1)[0] if "/" in _redis_base.split("://", 1)[-1] else _redis_base

_broker = (
    os.getenv("CELERY_BROKER_URL")
    or _redis_base_no_db + "/0"
)
_backend = (
    os.getenv("CELERY_RESULT_BACKEND")
    or _redis_base_no_db + "/1"
)

# Redis AUTH string support — inject only if the URL does not already carry auth
_auth = os.getenv("REDIS_AUTH_STRING", "")
if _auth:
    # "redis://host" has no "@"; "redis://:pass@host" already has it — don't double
    if "@" not in _broker.split("://", 1)[-1]:
        _broker = _broker.replace("redis://", f"redis://:{_auth}@")
    if "@" not in _backend.split("://", 1)[-1]:
        _backend = _backend.replace("redis://", f"redis://:{_auth}@")

app = Celery("medibox", broker=_broker, backend=_backend)

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    broker_connection_retry_on_startup=True,
    timezone="Africa/Tunis",
    enable_utc=True,
    # GPU jobs must not be prefetched — one task per worker at a time
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_soft_time_limit=120,
    task_time_limit=180,
    task_routes={
        "services.worker.tasks.inference.run_pipeline": {"queue": "inference"},
    },
    task_queues={
        "inference": {"exchange": "inference", "routing_key": "inference"},
        "default": {"exchange": "default", "routing_key": "default"},
    },
    task_default_queue="default",
    broker_transport_options={
        "visibility_timeout": 300,
        "fanout_prefix": True,
        "fanout_patterns": True,
    },
    result_expires=3600,
)

# Explicitly import task modules so Celery registers them on the worker.
# The API container imports celery_app only to send tasks (no task code
# present there), so we guard with try/except to avoid ModuleNotFoundError.
try:
    import services.worker.tasks.inference        # noqa: F401, E402
    import services.worker.tasks.postprocessing   # noqa: F401, E402
except ImportError:
    pass  # Running in API container — task modules not needed for sending
