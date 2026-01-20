# ═══════════════════════════════════════════════════════════════════════════════
# GitHub Backup - Dockerfile
# ═══════════════════════════════════════════════════════════════════════════════
# Multi-stage build with integrated testing
#
# Stages:
#   1. builder  - Install Python dependencies
#   2. test     - Run pytest (build fails if tests fail)
#   3. prod     - Minimal production image
#
# Build without tests:  docker build --target prod -t github-backup .
# Build with tests:     docker build -t github-backup .
# ═══════════════════════════════════════════════════════════════════════════════

# ───────────────────────────────────────────────────────────────────────────────
# Build Stage - Install Dependencies
# ───────────────────────────────────────────────────────────────────────────────
FROM python:3.13-alpine AS builder

RUN apk add --no-cache \
    gcc \
    musl-dev \
    libffi-dev

WORKDIR /build

COPY src/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ───────────────────────────────────────────────────────────────────────────────
# Test Stage - Run Tests During Build
# ───────────────────────────────────────────────────────────────────────────────
FROM python:3.13-alpine AS test

# Build dependencies for test packages
RUN apk add --no-cache \
    gcc \
    musl-dev \
    libffi-dev \
    git

WORKDIR /app

# Install production + test dependencies
COPY src/requirements.txt src/requirements-test.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-test.txt

# Copy application code
COPY src/ .

# Run tests - build fails if tests fail
RUN pytest tests/ -v --tb=short \
    && echo "═══════════════════════════════════════════════════════════════════════" \
    && echo "✓ All tests passed successfully" \
    && echo "═══════════════════════════════════════════════════════════════════════"

# ───────────────────────────────────────────────────────────────────────────────
# Production Stage - Minimal Runtime Image
# ───────────────────────────────────────────────────────────────────────────────
FROM python:3.13-alpine AS prod

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

# Ensure test stage passed (creates dependency on test stage)
COPY --from=test /app/pytest.ini /tmp/test-passed
RUN rm /tmp/test-passed

# Non-root user
RUN addgroup -g 1000 backup \
    && adduser -u 1000 -G backup -h /app -D backup

WORKDIR /app

# Application code (exclude tests in production)
COPY --chown=backup:backup src/*.py ./
COPY --chown=backup:backup src/alerting/ ./alerting/

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
# Build (with tests):        docker build -t github-backup .
# Build (skip tests):        docker build --target prod -t github-backup .
# Run tests only:            docker build --target test -t github-backup-test .
#
# Scheduler mode (default):  docker run ... github-backup
# Immediate backup:          docker run ... github-backup --now
# CLI commands:              docker run ... github-backup cli <command>
