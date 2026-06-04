"""
arq WorkerSettings — runs inside the FastAPI process (same event loop).

Design choices:
  max_jobs=1           Only one inference at a time (GPU serialises requests).
  job_timeout=360      6 min — covers RunPod serverless cold start (60s) + inference.
  health_check_interval=30  Reduces Redis polling to ~3 000 commands/day so
                             we stay within Upstash free tier (10 000/day).
  keep_result=3600     Keep arq result key in Redis for 1 h (enough for WS).
  retry_jobs=True      arq re-enqueues jobs that raise; task sets max_tries.
"""

from __future__ import annotations

import os

from arq.connections import RedisSettings

from services.api.worker.tasks import run_pipeline, startup_ctx, shutdown_ctx


def _redis_settings() -> RedisSettings:
    url = os.getenv("REDIS_URL", "redis://localhost:6379")
    return RedisSettings.from_dsn(url)


def _poll_delay() -> float:
    raw = os.getenv("ARQ_POLL_DELAY", "2.0")
    try:
        return max(0.5, float(raw))  # floor at 0.5 — arq minimum
    except ValueError:
        return 2.0


class WorkerSettings:
    functions = [run_pipeline]
    redis_settings = _redis_settings()

    # Single GPU job at a time — must not prefetch
    max_jobs = 1
    job_timeout = 360           # seconds — RunPod cold start ~60s + inference ~60s + overhead
    keep_result = 3600          # seconds — keep result in Redis
    # Raised from 0.5s default: each ZRANGEBYSCORE = 1 Upstash command.
    # 0.5s → 5.18M/month; 2.0s → 1.3M/month. Set ARQ_POLL_DELAY env var to tune.
    poll_delay = _poll_delay()
    health_check_interval = 30  # seconds — worker heartbeat in Redis
    retry_jobs = True           # re-enqueue on exception (tasks check ctx["job_try"])

    on_startup = startup_ctx
    on_shutdown = shutdown_ctx
