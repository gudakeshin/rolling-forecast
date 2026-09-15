# ──────────────────────────────────────────────────
# Rolling Forecast Backend — Python / FastAPI
# ──────────────────────────────────────────────────

FROM python:3.11-slim AS base

# System dependencies for Prophet, numpy, etc.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies from the hash-pinned lock so image builds are
# reproducible — a floating requirements.txt lets prophet/numpy move between
# builds and silently change forecast output.
COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

# Create non-root user
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser

# Pre-warm the all-MiniLM-L6-v2 ONNX model into the image. Chroma otherwise
# fetches ~79 MB from a CDN on the first embedding call, which makes the first
# request slow, re-downloads on every fresh container, and fails outright on an
# air-gapped network. Baked in as appuser so the runtime cache path matches.
USER appuser
RUN python -c "from chromadb.utils import embedding_functions as ef; ef.ONNXMiniLM_L6_V2()(['warmup'])"
USER root

# Copy application code
COPY . .

# Create required directories and hand off ownership
RUN mkdir -p uploads seed_data data exports \
    && chown -R appuser:appuser /app

USER appuser

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Run with uvicorn. --proxy-headers parses X-Forwarded-*; which peers are
# trusted is controlled by FORWARDED_ALLOW_IPS (uvicorn default: 127.0.0.1),
# so this is inert unless a deployment opts in.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers"]
