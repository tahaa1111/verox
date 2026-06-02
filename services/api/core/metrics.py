"""
Prometheus metrics definitions — single source of truth for all counters/histograms.

Exposed at GET /metrics (requires Authorization: Bearer <METRICS_SECRET>).
Scraped by Prometheus; visualized in Grafana.

Metric families:
  API        — request count, latency, error rate
  Security   — auth failures, rate limit hits, suspicious activity, blocked IPs
  Business   — OCR job outcomes, processing time, queue depth
  IoT        — active devices, heartbeat delay, offline detection
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# ---------------------------------------------------------------------------
# API Metrics
# ---------------------------------------------------------------------------

REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status_code"],
)

REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["endpoint"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

# ---------------------------------------------------------------------------
# Security Metrics
# ---------------------------------------------------------------------------

AUTH_FAILURES = Counter(
    "auth_failures_total",
    "Authentication failures by reason",
    ["reason"],   # reason: missing_token | invalid_token | expired | revoked | device_mismatch
)

RATE_LIMIT_HITS = Counter(
    "rate_limit_hits_total",
    "Rate limit breaches by layer",
    ["layer"],    # layer: ip | user | device | burst
)

SUSPICIOUS_REQUESTS = Counter(
    "suspicious_requests_total",
    "Suspicious or abusive request patterns detected",
    ["type"],     # type: brute_force | blocked_ip_attempt | token_replay
)

ADMIN_ACTIONS = Counter(
    "admin_actions_total",
    "Admin endpoint invocations",
    ["action"],
)

# ---------------------------------------------------------------------------
# Business Metrics
# ---------------------------------------------------------------------------

OCR_JOBS_TOTAL = Counter(
    "ocr_jobs_total",
    "OCR pipeline jobs by outcome",
    ["status"],   # status: queued | completed | failed | cancelled
)

OCR_PROCESSING_SECONDS = Histogram(
    "ocr_processing_seconds",
    "End-to-end OCR pipeline duration in seconds",
    buckets=[1, 5, 10, 20, 30, 60, 120, 300],
)

QUEUE_DEPTH = Gauge(
    "celery_queue_depth",
    "Estimated number of pending Celery tasks",
    ["queue"],
)

CORRECTIONS_TOTAL = Counter(
    "corrections_total",
    "Human corrections submitted for model retraining",
)

# ---------------------------------------------------------------------------
# IoT / Device Metrics
# ---------------------------------------------------------------------------

ACTIVE_DEVICES = Gauge(
    "active_devices_total",
    "Number of devices that have pushed a frame in the last 30 seconds",
)

DEVICE_HEARTBEAT_DELAY = Histogram(
    "device_heartbeat_delay_seconds",
    "Delay between consecutive camera pushes per device",
    buckets=[0.5, 1, 2, 5, 10, 30, 60],
)
