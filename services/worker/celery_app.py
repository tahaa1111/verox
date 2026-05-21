"""Celery application configured for Memorystore Redis."""

from __future__ import annotations

import os

from celery import Celery

_broker = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
_backend = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")

# Redis AUTH string support
_auth = os.getenv("REDIS_AUTH_STRING", "")
if _auth:
    _broker = _broker.replace("redis://", f"redis://:{_auth}@")
    _backend = _backend.replace("redis://", f"redis://:{_auth}@")

app = Celery("medibox", broker=_broker, backend=_backend)

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
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

app.autodiscover_tasks(["services.worker.tasks"])
