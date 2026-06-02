"""
arq WorkerSettings — runs inside the FastAPI process (same event loop).

Design choices:
  max_jobs=1           Only one inference at a time (GPU serialises requests).
  job_timeout=180      Hard kill after 3 min — matches old Celery time_limit.
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


class WorkerSettings:
    functions = [run_pipeline]
    redis_settings = _redis_settings()

    # Single GPU job at a time — must not prefetch
    max_jobs = 1
    job_timeout = 180          # seconds — hard kill
    keep_result = 3600         # seconds — keep result in Redis
    health_check_interval = 30 # seconds — save Upstash commands
    retry_jobs = True          # re-enqueue on exception (tasks check ctx["job_try"])

    on_startup = startup_ctx
    on_shutdown = shutdown_ctx
