"""Global middleware and exception handlers for the FastAPI application."""

import logging
import time
from typing import Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log all requests with timing and error information."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start_time = time.time()
        request_id = f"{int(start_time * 1000) % 100000:05d}"

        # Skip logging for health checks
        if request.url.path == "/health":
            return await call_next(request)

        logger.info(
            f"[{request_id}] {request.method} {request.url.path} "
            f"(client: {request.client.host if request.client else 'unknown'})"
        )

        try:
            response = await call_next(request)
            elapsed = (time.time() - start_time) * 1000

            log_level = logging.WARNING if response.status_code >= 400 else logging.INFO
            logger.log(
                log_level,
                f"[{request_id}] {request.method} {request.url.path} -> "
                f"{response.status_code} ({elapsed:.0f}ms)"
            )

            # Add timing header
            response.headers["X-Request-Time-Ms"] = f"{elapsed:.0f}"
            return response

        except Exception as e:
            elapsed = (time.time() - start_time) * 1000
            logger.error(
                f"[{request_id}] {request.method} {request.url.path} -> "
                f"UNHANDLED ERROR ({elapsed:.0f}ms): {e}",
                exc_info=True,
            )
            return JSONResponse(
                status_code=500,
                content={
                    "detail": "An internal error occurred. Please try again.",
                    "request_id": request_id,
                },
            )


def register_exception_handlers(app: FastAPI) -> None:
    """Register global exception handlers."""

    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError):
        logger.warning(f"ValueError: {exc}")
        return JSONResponse(
            status_code=400,
            content={"detail": str(exc)},
        )

    @app.exception_handler(PermissionError)
    async def permission_error_handler(request: Request, exc: PermissionError):
        logger.warning(f"PermissionError: {exc}")
        return JSONResponse(
            status_code=403,
            content={"detail": str(exc)},
        )

    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception):
        logger.error(f"Unhandled exception: {exc}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "detail": "An unexpected error occurred. Please try again or contact support.",
            },
        )
