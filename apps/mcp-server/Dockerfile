# =============================================================================
# MCP Server Dockerfile
# =============================================================================
# Teaching note:
#   Multi-stage build keeps the final image small.
#   Stage 1 (builder): installs dependencies
#   Stage 2 (runtime): copies only what's needed to run
#
#   Result: ~400MB image vs ~800MB for agent-runtime
#   This matters because: smaller images = faster cold starts on Cloud Run
# =============================================================================

# Stage 1: Build environment
FROM python:3.11-slim AS builder

WORKDIR /build

# Install system dependencies for pandas/numpy
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ \
    && rm -rf /var/lib/apt/lists/*

# Build context is repo root (context: .. in docker-compose.yml)
COPY apps/mcp-server/requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Stage 2: Runtime image
FROM python:3.11-slim AS runtime

# Security: run as non-root user
RUN useradd -m -u 1000 appuser

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /root/.local /home/appuser/.local

# Copy shared packages (paths relative to repo root build context)
COPY packages/shared-config /app/packages/shared-config
COPY packages/shared-observability /app/packages/shared-observability

# Copy original datasource (unchanged business logic)
COPY datasource.py /app/datasource.py

# Copy service code
COPY apps/mcp-server/server.py /app/server.py

# Set Python path to include shared packages
ENV PYTHONPATH="/app:/app/packages/shared-config:/app/packages/shared-observability"
ENV PATH="/home/appuser/.local/bin:$PATH"

# Default environment (overridden at runtime via Cloud Run / K8s env vars)
ENV SERVICE_NAME=mcp-server
ENV LOG_FORMAT=json
ENV ENVIRONMENT=production

USER appuser

# Health check (GCP load balancer uses this)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/health')"

EXPOSE 8001

# Use PORT env var from Cloud Run (defaults to 8001)
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT:-8001} --workers 2 --log-level warning"]
