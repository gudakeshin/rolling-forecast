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

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Create non-root user
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser

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

# Run with uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
