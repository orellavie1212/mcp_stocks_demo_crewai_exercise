# LiteLLM proxy — config baked in at build time, no bootstrap script.
#
# Teaching note:
#   Every LLM call in Lab 3 (Docker Compose) and Lab 4 (GCP) routes through
#   this proxy instead of talking to OpenAI/Gemini/Vertex directly. It gives
#   one place to see routing, basic per-key/per-model request limits, and
#   Langfuse tracing (via litellm's own success_callback — no app code).
#
#   Pulled from GHCR, not Docker Hub: Docker Hub's berriai/litellm images are
#   amd64-only; GHCR's are multi-arch (amd64 + arm64), which matters for
#   Apple Silicon Macs. Pinned to a specific stable tag rather than the
#   floating "main-stable" so this lab's behavior doesn't drift under you —
#   bump the pin deliberately when you want a newer litellm.
FROM ghcr.io/berriai/litellm:v1.83.7-stable

COPY config.yaml /app/config.yaml

EXPOSE 4000

CMD ["--config", "/app/config.yaml", "--port", "4000"]
