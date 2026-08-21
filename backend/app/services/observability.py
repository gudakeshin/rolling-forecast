"""Observability setup — OpenTelemetry (OTLP) + Langfuse hooks."""

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

        otlp_endpoint = getattr(settings, "otel_exporter_otlp_endpoint", "") or ""
        if otlp_endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

                provider.add_span_processor(
                    BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint))
                )
                logger.info("OTLP exporter configured → %s", otlp_endpoint)
            except ImportError:
                logger.warning("OTLP packages missing; falling back to console exporter")
                provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        else:
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

        trace.set_tracer_provider(provider)
        FastAPIInstrumentor.instrument_app(app)
        logger.info("OpenTelemetry instrumentation enabled for %s", settings.otel_service_name)
    except ImportError:
        logger.warning(
            "OTEL_ENABLED but opentelemetry packages not installed; skipping instrumentation"
        )

    if settings.langfuse_public_key and settings.langfuse_secret_key:
        logger.info(
            "Langfuse keys configured (host=%s) — attach via get_langfuse_callback()",
            settings.langfuse_host,
        )


def get_langfuse_callback():
    """Return a Langfuse LangChain callback handler when keys are configured, else None."""
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        return None
    try:
        from langfuse.callback import CallbackHandler

        return CallbackHandler(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
    except ImportError:
        logger.warning("langfuse package not installed; skipping LLM tracing callback")
        return None
