# Frontend Dockerfile — smallest image (~180MB)
FROM python:3.11-slim AS builder
WORKDIR /build
# Build context is repo root (context: .. in docker-compose.yml)
COPY apps/frontend-streamlit/requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

FROM python:3.11-slim AS runtime
RUN useradd -m -u 1000 appuser
WORKDIR /app
COPY --from=builder /root/.local /home/appuser/.local
COPY apps/frontend-streamlit/app.py /app/app.py

ENV PATH="/home/appuser/.local/bin:$PATH"
ENV SERVICE_NAME=frontend-streamlit
ENV ENVIRONMENT=production

USER appuser

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')"

EXPOSE 8501
# Use PORT env var (Cloud Run sets this); default to 8501 for local dev
CMD ["sh", "-c", "streamlit run app.py --server.port=${PORT:-8501} --server.address=0.0.0.0 --server.headless=true"]
