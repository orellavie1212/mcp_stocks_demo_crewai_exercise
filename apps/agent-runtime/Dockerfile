# Agent Runtime Dockerfile
# Largest image (~800MB) because it includes CrewAI + LangChain
FROM python:3.11-slim AS builder
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends gcc g++ && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

FROM python:3.11-slim AS runtime
RUN useradd -m -u 1000 appuser
WORKDIR /app
COPY --from=builder /root/.local /home/appuser/.local

# Shared packages
COPY ../../packages/shared-config /app/packages/shared-config
COPY ../../packages/shared-observability /app/packages/shared-observability
COPY ../../packages/shared-models /app/packages/shared-models
COPY ../../packages/shared-guardrails /app/packages/shared-guardrails

# Copy datasource (shared business logic)
COPY ../../datasource.py /app/datasource.py

COPY worker.py /app/worker.py

ENV PYTHONPATH="/app:/app/packages/shared-config:/app/packages/shared-observability:/app/packages/shared-models:/app/packages/shared-guardrails"
ENV PATH="/home/appuser/.local/bin:$PATH"
ENV SERVICE_NAME=agent-runtime
ENV LOG_FORMAT=json
ENV ENVIRONMENT=production
# pubsub = production Pub/Sub worker; http = sync HTTP for local dev
ENV WORKER_MODE=pubsub

USER appuser

HEALTHCHECK --interval=60s --timeout=10s --retries=3 \
    CMD python -c "print('worker healthy')"

# Run in Pub/Sub mode by default (switch to http for local sync dev)
CMD ["python", "worker.py", "--mode", "pubsub"]
