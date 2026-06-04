"""
OpenTelemetry setup — single OTLP push pipeline to Grafana Cloud.

Decision (do not add a scrape endpoint or a Collector sidecar):
  - Traces AND metrics are pushed over OTLP/HTTP from within the process.
  - Grafana Cloud's storage backend is Prometheus-compatible; PromQL / alerts
    still work — we're replacing the transport (scrape → push), not the query layer.
  - Push survives spin-down while the process is alive; a scraper would either
    keep the service awake (defeats scale-to-zero) or miss metrics during idle.

Required env vars (all optional — disables push if absent):
  GRAFANA_CLOUD_OTLP_ENDPOINT  e.g. https://otlp-gateway-prod-us-east-0.grafana.net/otlp
  GRAFANA_CLOUD_INSTANCE_ID    numeric Grafana Cloud instance / stack ID
  GRAFANA_CLOUD_TOKEN          Grafana Cloud token with traces:write + metrics:write

PII policy:
  PiiScrubSpanProcessor strips known PII attribute names from every span before
  export.  This is additive to the log scrubbing already in logging_processors.py.
  Sentry is kept for error tracking (separate before_send scrubbing there).
"""

from __future__ import annotations

import base64
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

# PII attribute names — must never appear in span attributes or metric labels.
_PII_ATTRS = frozenset({
    "patient_name", "doctor_name", "patient.name", "doctor.name",
    "patient.last_name", "patient.address", "token", "authorization",
    "x-camera-secret", "password", "image_base64", "frame",
    "raw_output", "db.statement",   # SQL statements may contain PII
})


from opentelemetry.sdk.trace import SpanProcessor


class PiiScrubSpanProcessor(SpanProcessor):
    """SpanProcessor that redacts PII attribute keys before export."""

    def on_start(self, span, parent_context=None):  # type: ignore[override]
        _scrub(span)

    def on_end(self, span):  # type: ignore[override]
        _scrub(span)

    def shutdown(self): pass
    def force_flush(self, timeout_millis=30_000): return True


def _scrub(span) -> None:  # type: ignore[type-arg]
    try:
        for key in list(span.attributes or {}):
            if key.lower() in _PII_ATTRS:
                span.set_attribute(key, "[REDACTED]")
    except Exception:
        pass


def _grafana_auth_header(instance_id: str, token: str) -> str:
    credentials = f"{instance_id}:{token}"
    return "Basic " + base64.b64encode(credentials.encode()).decode()


def configure_telemetry(service_name: str = "medibox-api") -> None:
    """
    Initialise OTel tracing.  Called once in FastAPI lifespan before serving traffic.

    If Grafana Cloud env vars are present → push to Grafana Cloud via OTLP/HTTP.
    Otherwise (dev / missing config) → ConsoleSpanExporter in non-production.
    configure_metrics() must be called separately (after this) to set up the
    MeterProvider — both share the same Resource.
    """
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource, SERVICE_NAME, DEPLOYMENT_ENVIRONMENT
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        from services.api.core.config import get_settings
        cfg = get_settings()

        resource = Resource.create({
            SERVICE_NAME: service_name,
            DEPLOYMENT_ENVIRONMENT: cfg.environment,
        })

        provider = TracerProvider(resource=resource)
        provider.add_span_processor(PiiScrubSpanProcessor())

        endpoint = cfg.grafana_cloud_otlp_endpoint
        instance  = cfg.grafana_cloud_instance_id
        token     = cfg.grafana_cloud_token

        if endpoint and token:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            exporter = OTLPSpanExporter(
                endpoint=f"{endpoint.rstrip('/')}/v1/traces",
                headers={"Authorization": _grafana_auth_header(instance, token)},
            )
            provider.add_span_processor(BatchSpanProcessor(exporter))
            logging.getLogger(__name__).info(
                "otel_traces_otlp_enabled", extra={"endpoint": endpoint}
            )
        elif cfg.environment != "production":
            from opentelemetry.sdk.trace.export import ConsoleSpanExporter
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

        trace.set_tracer_provider(provider)

        # Auto-instrument — one trace spans FastAPI → SQLAlchemy → Redis → httpx(RunPod)
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
        from opentelemetry.instrumentation.redis import RedisInstrumentor
        FastAPIInstrumentor().instrument()
        SQLAlchemyInstrumentor().instrument()
        RedisInstrumentor().instrument()
        try:
            from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
            HTTPXClientInstrumentor().instrument()
        except ImportError:
            pass  # optional — install opentelemetry-instrumentation-httpx

    except ImportError as exc:
        logging.warning("OTel packages not installed; tracing disabled: %s", exc)


def configure_metrics(service_name: str = "medibox-api") -> None:
    """
    Initialise OTel MeterProvider and start a PeriodicExportingMetricReader
    that pushes to Grafana Cloud every 60 seconds over OTLP/HTTP.

    Must be called AFTER configure_telemetry() so the Resource is consistent.
    No-op if GRAFANA_CLOUD_OTLP_ENDPOINT / TOKEN are not set.

    We are NOT deploying prometheus_client or a /metrics scrape path.
    Grafana Cloud accepts OTLP metrics natively; PromQL queries still work.
    """
    try:
        from opentelemetry.metrics import set_meter_provider
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource, SERVICE_NAME, DEPLOYMENT_ENVIRONMENT

        from services.api.core.config import get_settings
        cfg = get_settings()

        resource = Resource.create({
            SERVICE_NAME: service_name,
            DEPLOYMENT_ENVIRONMENT: cfg.environment,
        })

        endpoint = cfg.grafana_cloud_otlp_endpoint
        instance  = cfg.grafana_cloud_instance_id
        token     = cfg.grafana_cloud_token

        readers = []
        if endpoint and token:
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
            exporter = OTLPMetricExporter(
                endpoint=f"{endpoint.rstrip('/')}/v1/metrics",
                headers={"Authorization": _grafana_auth_header(instance, token)},
            )
            # Push every 60 s — balances freshness vs. Grafana Cloud ingest rate
            readers.append(PeriodicExportingMetricReader(
                exporter, export_interval_millis=60_000
            ))
            logging.getLogger(__name__).info(
                "otel_metrics_otlp_enabled", extra={"endpoint": endpoint}
            )

        meter_provider = MeterProvider(resource=resource, metric_readers=readers)
        set_meter_provider(meter_provider)

    except ImportError as exc:
        logging.warning("OTel metrics packages not installed; metrics push disabled: %s", exc)
