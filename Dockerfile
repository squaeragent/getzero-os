# ZERO OS Trading Engine
# Multi-stage build for production deployment

FROM python:3.14-slim AS base

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy scanner code
COPY scanner/ scanner/

# Create bus and data directories
RUN mkdir -p scanner/v6/bus scanner/v6/data

# ── Test stage ──
FROM base AS test
RUN pip install --no-cache-dir pytest
COPY scanner/tests/ scanner/tests/
RUN python -m pytest scanner/tests/ -v --tb=short

# ── Production stage ──
FROM base AS production

# Non-root user
RUN useradd -m -s /bin/bash zero
USER zero

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import json; print(json.dumps({'status': 'ok'}))"

# Environment
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Default: run the evaluator
CMD ["python", "-m", "scanner.v6.local_evaluator", "--loop"]
