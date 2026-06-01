"""
OpenTelemetry setup — provider-agnostic instrumentation.
GCP Cloud Trace/Monitoring removed. Traces exported to stdout in dev.
"""

from __future__ import annotations

import logging

from services.api.core.config import get_settings

settings = get_settings()


def configure_telemetry(service_name: str = "medibox-api") -> None:
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
        from opentelemetry.instrumentation.redis import RedisInstrumentor

        provider = TracerProvider()
        if settings.environment != "production":
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        trace.set_tracer_provider(provider)

        FastAPIInstrumentor().instrument()
        SQLAlchemyInstrumentor().instrument()
        RedisInstrumentor().instrument()

    except ImportError:
        logging.warning("OpenTelemetry packages not installed; tracing disabled")


def configure_metrics(service_name: str = "medibox-api") -> None:
    pass
