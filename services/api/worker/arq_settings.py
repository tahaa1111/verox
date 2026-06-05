"""
arq WorkerSettings — runs inside the FastAPI process (same event loop).

Design choices:
  max_jobs=1           Single Railway process → single arq worker → one RunPod call
                       in flight at a time. RunPod max_workers=1 matches this.
                       (Setting RunPod max_workers>1 is useless here — burst capacity
                       requires either multiple Railway processes or raising this to >1.)
  job_timeout=360      6 min — covers RunPod serverless cold start (60s) + inference.
  health_check_interval=30  Heartbeat to Redis.
  poll_delay=2.0s      Each poll = 1 Upstash command. At 2.0s: ~1.3M cmds/month.
                       This exceeds the Upstash 500K free tier — a paid plan or
                       clinic-hours-only deployment is required. Set ARQ_POLL_DELAY
                       env var to tune (e.g. 10.0s for ~390K/month under free tier,
                       at the cost of up to 10s job pickup delay).
  keep_result=3600     Keep arq result key in Redis for 1 h (enough for WS).
  retry_jobs=True      arq re-enqueues on exception; task checks ctx["job_try"].
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
