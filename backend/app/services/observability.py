"""Observability setup — OpenTelemetry + structured logging hooks."""

from __future__ import annotations

import logging

from fastapi import FastAPI

from app.config import settings

logger = logging.getLogger(__name__)


def setup_observability(app: FastAPI) -> None:
    """Instrument FastAPI with OpenTelemetry when OTEL_ENABLED=true."""
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        resource = Resource.create({"service.name": settings.otel_service_name})
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        trace.set_tracer_provider(provider)
        FastAPIInstrumentor.instrument_app(app)
        logger.info("OpenTelemetry instrumentation enabled for %s", settings.otel_service_name)
    except ImportError:
        logger.warning(
            "OTEL_ENABLED but opentelemetry packages not installed; skipping instrumentation"
        )

    if settings.langfuse_public_key and settings.langfuse_secret_key:
        logger.info("Langfuse keys configured (host=%s)", settings.langfuse_host)
