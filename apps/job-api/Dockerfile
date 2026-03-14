# Job API Dockerfile — lightest of all services
FROM python:3.11-slim AS builder
WORKDIR /build
# Build context is repo root (context: .. in docker-compose.yml)
COPY apps/job-api/requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

FROM python:3.11-slim AS runtime
RUN useradd -m -u 1000 appuser
WORKDIR /app

COPY --from=builder /root/.local /home/appuser/.local

# Shared packages (paths relative to repo root build context)
COPY packages/shared-config /app/packages/shared-config
COPY packages/shared-observability /app/packages/shared-observability
COPY packages/shared-models /app/packages/shared-models
COPY packages/shared-guardrails /app/packages/shared-guardrails

COPY apps/job-api/main.py /app/main.py

ENV PYTHONPATH="/app:/app/packages/shared-config:/app/packages/shared-observability:/app/packages/shared-models:/app/packages/shared-guardrails"
ENV PATH="/home/appuser/.local/bin:$PATH"
ENV SERVICE_NAME=job-api
ENV LOG_FORMAT=json
ENV ENVIRONMENT=production

USER appuser

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

EXPOSE 8000
# Single worker for local dev (in-memory store is per-process).
# In production (Cloud Run / GKE), set WORKERS env var or use Firestore.
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${UVICORN_WORKERS:-1} --log-level warning"]
