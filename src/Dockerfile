# ═══════════════════════════════════════════════════════════════════════════════
# GitHub Backup - Dockerfile
# ═══════════════════════════════════════════════════════════════════════════════
# Multi-stage build for minimal production image
# ═══════════════════════════════════════════════════════════════════════════════

# ───────────────────────────────────────────────────────────────────────────────
# Build Stage
# ───────────────────────────────────────────────────────────────────────────────
FROM python:3.13-alpine AS builder

RUN apk add --no-cache \
    gcc \
    musl-dev \
    libffi-dev

WORKDIR /build

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ───────────────────────────────────────────────────────────────────────────────
# Production Stage
# ───────────────────────────────────────────────────────────────────────────────
FROM python:3.13-alpine

# OCI Image Metadata
LABEL org.opencontainers.image.title="GitHub Backup"
LABEL org.opencontainers.image.description="Automated GitHub repository backup to S3-compatible storage"
LABEL org.opencontainers.image.vendor="BAUER GROUP"
LABEL org.opencontainers.image.source="https://github.com/bauer-group/github-backup"

# Runtime dependencies
RUN apk add --no-cache \
    git \
    tzdata \
    tini \
    && rm -rf /var/cache/apk/*

# Python packages from builder
COPY --from=builder /install /usr/local

# Non-root user
RUN addgroup -g 1000 backup \
    && adduser -u 1000 -G backup -h /app -D backup

WORKDIR /app

# Application code
COPY --chown=backup:backup . .

# Data directory
RUN mkdir -p /data && chown backup:backup /data

USER backup

VOLUME ["/data"]

# Health check
HEALTHCHECK --interval=60s --timeout=10s --start-period=5s --retries=3 \
    CMD pgrep -f "python main.py" || exit 1

# Tini as init process for proper signal handling and zombie reaping
ENTRYPOINT ["/sbin/tini", "--", "python", "main.py"]

# ───────────────────────────────────────────────────────────────────────────────
# Usage
# ───────────────────────────────────────────────────────────────────────────────
# Scheduler mode (default):  docker run ... github-backup
# Immediate backup:          docker run ... github-backup --now
# CLI commands:              docker run ... github-backup cli <command>
