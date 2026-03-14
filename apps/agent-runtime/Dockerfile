# Agent Runtime Dockerfile
# Largest image (~800MB) because it includes CrewAI + LangChain
FROM python:3.11-slim AS builder
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends gcc g++ && rm -rf /var/lib/apt/lists/*
# Build context is repo root (context: .. in docker-compose.yml)
COPY apps/agent-runtime/requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

FROM python:3.11-slim AS runtime
RUN useradd -m -u 1000 appuser
WORKDIR /app
COPY --from=builder /root/.local /home/appuser/.local
# Fix ownership — builder runs as root, runtime runs as appuser
RUN chown -R appuser:appuser /home/appuser/.local && \
    mkdir -p /home/appuser/.local/share/crewai && \
    chown -R appuser:appuser /home/appuser

# Shared packages (paths relative to repo root build context)
COPY packages/shared-config /app/packages/shared-config
COPY packages/shared-observability /app/packages/shared-observability
COPY packages/shared-models /app/packages/shared-models
COPY packages/shared-guardrails /app/packages/shared-guardrails

# Copy datasource (shared business logic)
COPY datasource.py /app/datasource.py

COPY apps/agent-runtime/worker.py /app/worker.py

ENV PYTHONPATH="/app:/app/packages/shared-config:/app/packages/shared-observability:/app/packages/shared-models:/app/packages/shared-guardrails"
ENV PATH="/home/appuser/.local/bin:$PATH"
ENV SERVICE_NAME=agent-runtime
ENV LOG_FORMAT=json
ENV ENVIRONMENT=production
# pubsub = production Pub/Sub worker; http = sync HTTP for local dev
ENV WORKER_MODE=pubsub
# Direct CrewAI/platformdirs to /tmp so ChromaDB can always write
ENV XDG_DATA_HOME=/tmp
ENV CREWAI_STORAGE_DIR=/tmp/crewai

USER appuser

HEALTHCHECK --interval=60s --timeout=10s --retries=3 \
    CMD python -c "print('worker healthy')"

# Run in Pub/Sub mode by default (switch to http for local sync dev)
CMD ["python", "worker.py", "--mode", "pubsub"]
