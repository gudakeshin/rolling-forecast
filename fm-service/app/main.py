"""fm-service: self-hosted Chronos-2 inference for Rolling Forecast.

Endpoints
* ``GET  /healthz``      — liveness + readiness (model loaded)
* ``GET  /v1/info``      — model id, pinned weight revision, service version
* ``POST /v1/forecast``  — batch of series -> P10/P50/P90 per horizon step

The weights are baked into the image at build time (see Dockerfile) at a
pinned Hugging Face revision; nothing is downloaded at runtime.
"""

from __future__ import annotations

import logging
import os
import threading
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException

from app.forecaster import load_pipeline, run_batch
from app.schemas import (
    QUANTILE_LEVELS,
    SERVICE_VERSION,
    ForecastRequest,
    ForecastResponse,
    ModelInfo,
)

logger = logging.getLogger("fm-service")

MODEL_ID = os.environ.get("FM_MODEL_ID", "amazon/chronos-2")
MODEL_REVISION = os.environ.get("FM_MODEL_REVISION", "unknown")
MODEL_DIR = os.environ.get("FM_MODEL_DIR", "/opt/models/chronos-2")
BATCH_SIZE = int(os.environ.get("FM_BATCH_SIZE", "256"))

_state: dict[str, Any] = {"pipeline": None}
# CPU inference is compute-bound; serialising requests keeps memory bounded
# and makes batch composition (hence output) independent of concurrency.
_lock = threading.Lock()


def _library_version() -> str:
    try:
        from importlib.metadata import version

        return version("chronos-forecasting")
    except Exception:
        return "unknown"


def model_info() -> ModelInfo:
    return ModelInfo(
        model_id=MODEL_ID,
        model_revision=MODEL_REVISION,
        service_version=SERVICE_VERSION,
        library_version=_library_version(),
        quantiles=list(QUANTILE_LEVELS),
    )


@asynccontextmanager
async def lifespan(_app: FastAPI):  # type: ignore[no-untyped-def]
    if os.environ.get("FM_SKIP_MODEL_LOAD") != "1":
        _state["pipeline"] = load_pipeline(MODEL_DIR)
        logger.info("Loaded %s@%s from %s", MODEL_ID, MODEL_REVISION, MODEL_DIR)
    yield
    _state["pipeline"] = None


app = FastAPI(title="fm-service", version=SERVICE_VERSION, lifespan=lifespan)


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    ready = _state["pipeline"] is not None
    if not ready:
        raise HTTPException(status_code=503, detail="model not loaded")
    return {"status": "ok", **model_info().model_dump()}


@app.get("/v1/info", response_model=ModelInfo)
def info() -> ModelInfo:
    return model_info()


@app.post("/v1/forecast", response_model=ForecastResponse)
def forecast(req: ForecastRequest) -> ForecastResponse:
    # Sync handler: FastAPI runs it in its threadpool, so the event loop keeps
    # answering /healthz while a large batch is being computed.
    pipeline = _state["pipeline"]
    if pipeline is None:
        raise HTTPException(status_code=503, detail="model not loaded")
    import torch

    with _lock, torch.inference_mode():
        try:
            forecasts = run_batch(req.series, pipeline.predict_quantiles, batch_size=BATCH_SIZE)
        except (RuntimeError, ValueError) as e:
            logger.exception("forecast failed")
            raise HTTPException(status_code=422, detail=str(e)[:500]) from e
    return ForecastResponse(model=model_info(), forecasts=forecasts)
