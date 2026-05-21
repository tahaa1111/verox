"""
OpenTelemetry setup for Cloud Trace + Cloud Monitoring.
Call configure_telemetry() once at app startup.
"""

from __future__ import annotations

import logging

from services.api.core.config import get_settings

settings = get_settings()


def configure_telemetry(service_name: str = "medibox-api") -> None:
    if settings.environment == "development":
        return  # skip in local dev — avoids GCP credential requirements

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
        from opentelemetry.instrumentation.redis import RedisInstrumentor

        provider = TracerProvider()
        provider.add_span_processor(
            BatchSpanProcessor(CloudTraceSpanExporter(project_id=settings.gcp_project_id))
        )
        trace.set_tracer_provider(provider)

        FastAPIInstrumentor().instrument()
        SQLAlchemyInstrumentor().instrument()
        RedisInstrumentor().instrument()

    except ImportError:
        logging.warning("OpenTelemetry packages not installed; tracing disabled")


def configure_metrics(service_name: str = "medibox-api") -> None:
    """Publish custom metrics via OpenTelemetry → Cloud Monitoring."""
    if settings.environment == "development":
        return
    try:
        from opentelemetry import metrics
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.exporter.cloud_monitoring import CloudMonitoringMetricsExporter
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

        reader = PeriodicExportingMetricReader(
            CloudMonitoringMetricsExporter(project_id=settings.gcp_project_id),
            export_interval_millis=60_000,
        )
        provider = MeterProvider(metric_readers=[reader])
        metrics.set_meter_provider(provider)

    except ImportError:
        logging.warning("OpenTelemetry metrics packages not installed; custom metrics disabled")
