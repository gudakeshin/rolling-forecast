"""Global middleware and exception handlers for the FastAPI application."""

from __future__ import annotations

import logging
import time
import uuid
from contextvars import ContextVar
from typing import Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

# Propagated into audit + structured logs for the request lifetime
request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)


def get_request_id() -> str | None:
    return request_id_ctx.get()


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log all requests with timing, uuid4 request id, and error information."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start_time = time.time()
        incoming = request.headers.get("X-Request-ID")
        request_id = incoming if incoming and len(incoming) <= 64 else str(uuid.uuid4())
        token = request_id_ctx.set(request_id)
        request.state.request_id = request_id

        # Skip logging for health checks
        if request.url.path in ("/health", "/livez", "/readyz"):
            try:
                response = await call_next(request)
                response.headers["X-Request-ID"] = request_id
                return response
            finally:
                request_id_ctx.reset(token)

        logger.info(
            "request_start",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "client": request.client.host if request.client else "unknown",
            },
        )

        try:
            response = await call_next(request)
            elapsed = (time.time() - start_time) * 1000

            log_level = logging.WARNING if response.status_code >= 400 else logging.INFO
            logger.log(
                log_level,
                "request_end",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "elapsed_ms": round(elapsed),
                },
            )

            response.headers["X-Request-Time-Ms"] = f"{elapsed:.0f}"
            response.headers["X-Request-ID"] = request_id
            return response

        except Exception as e:
            elapsed = (time.time() - start_time) * 1000
            logger.error(
                "request_error",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "elapsed_ms": round(elapsed),
                    "error": str(e),
                },
                exc_info=True,
            )
            return JSONResponse(
                status_code=500,
                content={
                    "detail": "An internal error occurred. Please try again.",
                    "request_id": request_id,
                },
                headers={"X-Request-ID": request_id},
            )
        finally:
            request_id_ctx.reset(token)


def register_exception_handlers(app: FastAPI) -> None:
    """Register global exception handlers."""

    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError):
        logger.warning("ValueError: %s", exc)
        return JSONResponse(
            status_code=400,
            content={"detail": str(exc), "request_id": get_request_id()},
        )

    @app.exception_handler(PermissionError)
    async def permission_error_handler(request: Request, exc: PermissionError):
        logger.warning("PermissionError: %s", exc)
        return JSONResponse(
            status_code=403,
            content={"detail": str(exc), "request_id": get_request_id()},
        )

    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception):
        logger.error("Unhandled exception: %s", exc, exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "detail": "An unexpected error occurred. Please try again or contact support.",
                "request_id": get_request_id(),
            },
        )
