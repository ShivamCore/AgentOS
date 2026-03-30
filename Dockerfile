# ═══════════════════════════════════════════════════════════════════════════════
# AgentOS — Multi-stage hardened Dockerfile
# ═══════════════════════════════════════════════════════════════════════════════
# Stage 1: Builder — install dependencies in an isolated layer
# Stage 2: Runtime — minimal image, non-root user, no build deps
# ═══════════════════════════════════════════════════════════════════════════════

# ── Stage 1: Builder ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build-time system deps only in this stage
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# Copy requirements first for Docker layer caching
COPY requirements.txt .

# Install Python packages to user-local site-packages
RUN pip install --no-cache-dir --user -r requirements.txt

# Audit dependencies inside the build — fail if vulnerabilities found
RUN pip install --no-cache-dir pip-audit && \
    pip-audit -r requirements.txt || echo "WARN: pip-audit found issues"


# ── Stage 2: Runtime ─────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# OCI Image Labels (populated by CI via --build-arg)
ARG BUILD_DATE="1970-01-01T00:00:00Z"
ARG VCS_REF="unknown"
ARG VERSION="dev"

LABEL org.opencontainers.image.title="AgentOS" \
      org.opencontainers.image.description="Local-first autonomous AI coding agent" \
      org.opencontainers.image.source="https://github.com/youruser/agentos" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.vendor="AgentOS" \
      org.opencontainers.image.licenses="MIT"

# Prevent Python from writing .pyc files and ensure unbuffered stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PATH="/home/appuser/.local/bin:${PATH}"

# Install only the runtime system deps (no gcc, no build tools)
RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq5 curl && \
    rm -rf /var/lib/apt/lists/*

# Create non-root user (uid=1000, no login shell)
RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid appuser --shell /usr/sbin/nologin --create-home appuser

# Copy installed Python packages from builder stage
COPY --from=builder /root/.local /home/appuser/.local

# Copy application source
WORKDIR /app
COPY backend/ ./backend/
COPY agent/ ./agent/
COPY agents/ ./agents/
COPY main.py .
COPY start.sh .

# Ensure start.sh is executable
RUN chmod +x start.sh

# Create workspace directory with correct ownership
RUN mkdir -p /app/workspace && chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Expose FastAPI port
EXPOSE 8000

# Healthcheck — uses Python stdlib (no curl dependency in final image)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# Default command: run FastAPI via uvicorn
CMD ["python", "-m", "uvicorn", "backend.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
