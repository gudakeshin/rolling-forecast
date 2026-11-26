# ──────────────────────────────────────────────────
# fm-service — Chronos-2 inference sidecar (CPU torch)
# ──────────────────────────────────────────────────
# Torch lives only in this image; the backend image stays torch-free.
# The weights are fetched ONCE, at build time, at a pinned Hugging Face
# revision and checked against a pinned sha256 — the running container never
# reaches the Hub (HF_HUB_OFFLINE=1), so air-gapped deployments work and a
# given image always serves the same weights.

FROM python:3.11-slim

ARG FM_MODEL_ID=amazon/chronos-2
ARG FM_MODEL_REVISION=29ec3766d36d6f73f0696f85560a422f50e8498c
ARG FM_WEIGHTS_SHA256=ddcda3c7508bf2528087723e98a20707cc04b7f370ae275a9fd88078ddba4f42

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FM_MODEL_ID=${FM_MODEL_ID} \
    FM_MODEL_REVISION=${FM_MODEL_REVISION} \
    FM_MODEL_DIR=/opt/models/chronos-2

WORKDIR /srv

COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

RUN python -c "import os; from huggingface_hub import snapshot_download; \
snapshot_download(os.environ['FM_MODEL_ID'], revision=os.environ['FM_MODEL_REVISION'], \
local_dir=os.environ['FM_MODEL_DIR'], allow_patterns=['config.json', 'model.safetensors'])" \
    && echo "${FM_WEIGHTS_SHA256}  ${FM_MODEL_DIR}/model.safetensors" | sha256sum -c - \
    && rm -rf /root/.cache/huggingface

ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

RUN useradd --create-home --uid 10002 --shell /usr/sbin/nologin fmuser
COPY app ./app
COPY tests ./tests
RUN chown -R fmuser:fmuser /srv
USER fmuser

EXPOSE 8100
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8100/healthz')" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100", "--workers", "1"]
