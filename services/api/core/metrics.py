"""
Medibox metric instruments — single source of truth.

Backend: OpenTelemetry metrics API pushed to Grafana Cloud over OTLP/HTTP.
  - prometheus_client is removed; no /metrics scrape endpoint.
  - Grafana Cloud stores metrics in its Prometheus-compatible backend so PromQL
    and existing alert rules still work unchanged.
  - Instruments created here use the OTel global MeterProvider.  Because metrics.py
    is imported at module-load time (before configure_metrics() runs in lifespan),
    the global provider starts as a proxy that upgrades automatically when
    configure_metrics() calls set_meter_provider().

Call-site interface is kept compatible with the old prometheus_client pattern
(`.labels(**kw).inc()` / `.labels(**kw).observe()`) via thin wrapper classes
so no routers need updating.
"""

from __future__ import annotations

from opentelemetry import metrics as _otel

_meter = _otel.get_meter("medibox.api", "1.0.0")


# ---------------------------------------------------------------------------
# Wrapper classes — preserve prometheus_client call-site interface
# ---------------------------------------------------------------------------

class _Counter:
    """Wraps an OTel Counter; supports .labels(**kw).inc(n=1)."""
    __slots__ = ("_instrument",)

    def __init__(self, instrument: _otel.Counter) -> None:
        self._instrument = instrument

    def labels(self, **attributes: str) -> "_CounterBound":
        return _CounterBound(self._instrument, attributes)

    def inc(self, amount: float = 1, **attributes: str) -> None:
        self._instrument.add(amount, attributes or {})


class _CounterBound:
    __slots__ = ("_instrument", "_attrs")

    def __init__(self, instrument: _otel.Counter, attrs: dict) -> None:
        self._instrument = instrument
        self._attrs = attrs

    def inc(self, amount: float = 1) -> None:
        self._instrument.add(amount, self._attrs)


class _Histogram:
    """Wraps an OTel Histogram; supports .labels(**kw).observe(value)."""
    __slots__ = ("_instrument",)

    def __init__(self, instrument: _otel.Histogram) -> None:
        self._instrument = instrument

    def labels(self, **attributes: str) -> "_HistogramBound":
        return _HistogramBound(self._instrument, attributes)

    def record(self, value: float, **attributes: str) -> None:
        self._instrument.record(value, attributes or {})


class _HistogramBound:
    __slots__ = ("_instrument", "_attrs")

    def __init__(self, instrument: _otel.Histogram, attrs: dict) -> None:
        self._instrument = instrument
        self._attrs = attrs

    def observe(self, value: float) -> None:
        self._instrument.record(value, self._attrs)


class _UpDownCounter:
    """Wraps an OTel UpDownCounter; supports .labels(**kw).set(n) and .inc(n)."""
    __slots__ = ("_instrument", "_current")

    def __init__(self, instrument: _otel.UpDownCounter) -> None:
        self._instrument = instrument
        self._current: dict[tuple, float] = {}

    def labels(self, **attributes: str) -> "_UpDownBound":
        return _UpDownBound(self._instrument, self._current, attributes)


class _UpDownBound:
    __slots__ = ("_instrument", "_store", "_attrs", "_key")

    def __init__(self, instrument: _otel.UpDownCounter, store: dict, attrs: dict) -> None:
        self._instrument = instrument
        self._store = store
        self._attrs = attrs
        self._key = tuple(sorted(attrs.items()))

    def set(self, value: float) -> None:
        prev = self._store.get(self._key, 0.0)
        delta = value - prev
        self._store[self._key] = value
        self._instrument.add(delta, self._attrs)

    def inc(self, amount: float = 1) -> None:
        self._store[self._key] = self._store.get(self._key, 0.0) + amount
        self._instrument.add(amount, self._attrs)


# ---------------------------------------------------------------------------
# API Metrics
# ---------------------------------------------------------------------------

REQUEST_COUNT = _Counter(
    _meter.create_counter(
        "http_requests_total",
        description="Total HTTP requests",
        unit="1",
    )
)

REQUEST_LATENCY = _Histogram(
    _meter.create_histogram(
        "http_request_duration_seconds",
        description="HTTP request latency",
        unit="s",
    )
)

# ---------------------------------------------------------------------------
# Security Metrics
# ---------------------------------------------------------------------------

AUTH_FAILURES = _Counter(
    _meter.create_counter(
        "auth_failures_total",
        description="Authentication failures by reason",
        unit="1",
    )
)

RATE_LIMIT_HITS = _Counter(
    _meter.create_counter(
        "rate_limit_hits_total",
        description="Rate limit breaches by layer (ip | user | device | burst)",
        unit="1",
    )
)

SUSPICIOUS_REQUESTS = _Counter(
    _meter.create_counter(
        "suspicious_requests_total",
        description="Suspicious or abusive request patterns detected",
        unit="1",
    )
)

ADMIN_ACTIONS = _Counter(
    _meter.create_counter(
        "admin_actions_total",
        description="Admin endpoint invocations",
        unit="1",
    )
)

# ---------------------------------------------------------------------------
# Business / OCR Metrics  (prompt Phase 3 §4)
# ---------------------------------------------------------------------------

OCR_JOBS_TOTAL = _Counter(
    _meter.create_counter(
        "ocr_jobs_total",
        description="OCR pipeline jobs by outcome (queued | completed | failed | cancelled)",
        unit="1",
    )
)

OCR_PROCESSING_SECONDS = _Histogram(
    _meter.create_histogram(
        "ocr_processing_seconds",
        description="End-to-end OCR pipeline duration",
        unit="s",
    )
)

# Queue depth — UpDownCounter so it can go up and down
QUEUE_DEPTH = _UpDownCounter(
    _meter.create_up_down_counter(
        "ocr_queue_depth",
        description="Pending OCR jobs in the arq queue",
        unit="1",
    )
)

CORRECTIONS_TOTAL = _Counter(
    _meter.create_counter(
        "corrections_total",
        description="Human corrections submitted for model retraining",
        unit="1",
    )
)

# Inference latency per stage (from prompt Phase 3 §4)
INFERENCE_LATENCY = _Histogram(
    _meter.create_histogram(
        "ocr_inference_latency_seconds",
        description="RunPod inference latency per grid",
        unit="s",
    )
)

RUNPOD_COLD_STARTS = _Counter(
    _meter.create_counter(
        "runpod_cold_starts_total",
        description="RunPod worker cold-start events",
        unit="1",
    )
)

PIPELINE_RETRIES = _Counter(
    _meter.create_counter(
        "pipeline_retries_total",
        description="arq task retry count by reason",
        unit="1",
    )
)

# Redis command-rate gauge (updated by arq/camera code — lets us see churn in Grafana)
REDIS_COMMANDS = _Counter(
    _meter.create_counter(
        "redis_commands_total",
        description="Redis commands issued by this process (approximate)",
        unit="1",
    )
)

# ---------------------------------------------------------------------------
# IoT / Device Metrics
# ---------------------------------------------------------------------------

ACTIVE_DEVICES = _UpDownCounter(
    _meter.create_up_down_counter(
        "active_devices_total",
        description="Devices that pushed a frame in the last 30 s",
        unit="1",
    )
)

DEVICE_HEARTBEAT_DELAY = _Histogram(
    _meter.create_histogram(
        "device_heartbeat_delay_seconds",
        description="Delay between consecutive camera pushes per device",
        unit="s",
    )
)
