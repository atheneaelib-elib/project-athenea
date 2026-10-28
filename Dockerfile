# ==============================================================================
# Athenea - Production Dockerfile
# Modern, High-Performance Self-Hosted Digital Library & Neural Audiobook Platform
# ==============================================================================

FROM python:3.12-slim-bookworm AS base

# Prevent Python from writing .pyc files and buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000 \
    APP_HOME=/app

WORKDIR ${APP_HOME}

# Install essential runtime system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libpq5 \
    ca-certificates \
    calibre \
    ffmpeg \
    p7zip-full \
    libarchive-tools \
    djvulibre-bin \
    pigz \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Builder stage for dependency compilation
FROM base AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Final lean runtime image
FROM base AS runner

# Copy installed python packages from builder
COPY --from=builder /root/.local /home/athenea/.local
ENV PATH=/home/athenea/.local/bin:$PATH

# Create unprivileged system user & persistent directories
RUN groupadd -g 1000 athenea && \
    useradd -u 1000 -g athenea -s /bin/bash -m athenea && \
    mkdir -p /data/books /data/thumbnails /app && \
    chown -R athenea:athenea /data /app /home/athenea

# Copy application source code
COPY --chown=athenea:athenea . ${APP_HOME}

# Default library paths
ENV LIBRARY_ROOT=/data/books \
    THUMBNAIL_DIR=/data/thumbnails

USER athenea

EXPOSE 8000

# Container Healthcheck
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Launch FastAPI with 4 high-concurrency workers
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4", "--proxy-headers", "--forwarded-allow-ips=*"]
