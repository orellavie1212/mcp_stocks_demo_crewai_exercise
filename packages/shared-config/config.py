"""
shared-config — Centralised configuration management.

Teaching note:
  This module shows the production pattern for configuration:
  1. Read from environment variables (injected by Docker/K8s)
  2. In production on GCP: values come from Secret Manager
  3. Never hardcode secrets in source code or Docker images

  The LlmConfig class implements the model-routing strategy:
  cheap model for guardrails, main model for tasks, strong for synthesis.
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """
    All service configuration loaded from environment variables.

    Pydantic automatically reads from env vars — no manual os.getenv() needed.
    In production, env vars are injected by Cloud Run / GKE from Secret Manager.
    """

    # -------------------------------------------------------------------------
    # GCP
    # -------------------------------------------------------------------------
    gcp_project: str = Field(default="local-project", alias="GCP_PROJECT")
    gcp_region: str = Field(default="us-central1", alias="GCP_REGION")

    # -------------------------------------------------------------------------
    # LLM Provider
    # Teaching note: switch between local dev (API key) and production (Vertex AI)
    # -------------------------------------------------------------------------
    llm_provider: str = Field(default="google_ai_studio", alias="LLM_PROVIDER")
    gemini_api_key: Optional[str] = Field(default=None, alias="GEMINI_API_KEY")

    # Model routing — three tiers for cost control
    gemini_fast_model: str = Field(
        default="gemini-2.5-flash-lite", alias="GEMINI_FAST_MODEL",
        description="Cheap model for guardrails and routing (~$0.00 on free tier)"
    )
    gemini_main_model: str = Field(
        default="gemini-2.5-flash", alias="GEMINI_MAIN_MODEL",
        description="Main model for agent tasks (research, technical, sector)"
    )
    gemini_strong_model: str = Field(
        default="gemini-2.5-pro", alias="GEMINI_STRONG_MODEL",
        description="Powerful model for final synthesis (used sparingly)"
    )

    # LLM limits
    llm_max_tokens: int = Field(default=4096, alias="LLM_MAX_TOKENS")
    llm_timeout_seconds: int = Field(default=120, alias="LLM_TIMEOUT_SECONDS")

    # -------------------------------------------------------------------------
    # Service URLs
    # -------------------------------------------------------------------------
    mcp_server_url: str = Field(
        default="http://localhost:8001", alias="MCP_SERVER_URL"
    )
    job_api_url: str = Field(
        default="http://localhost:8000", alias="JOB_API_URL"
    )
    agent_runtime_url: str = Field(
        default="http://localhost:8002", alias="AGENT_RUNTIME_URL"
    )

    # -------------------------------------------------------------------------
    # Redis
    # -------------------------------------------------------------------------
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    redis_cache_ttl_seconds: int = Field(
        default=3600, alias="REDIS_CACHE_TTL_SECONDS",
        description="1 hour default cache for stock analysis results"
    )

    # -------------------------------------------------------------------------
    # Pub/Sub
    # -------------------------------------------------------------------------
    pubsub_emulator_host: Optional[str] = Field(
        default=None, alias="PUBSUB_EMULATOR_HOST"
    )
    pubsub_project_id: str = Field(
        default="local-project", alias="PUBSUB_PROJECT_ID"
    )
    pubsub_topic_requests: str = Field(
        default="analysis-requests", alias="PUBSUB_TOPIC_ANALYSIS_REQUESTS"
    )
    pubsub_topic_dlq: str = Field(
        default="analysis-dlq", alias="PUBSUB_TOPIC_ANALYSIS_DLQ"
    )
    pubsub_subscription: str = Field(
        default="agent-worker-sub", alias="PUBSUB_SUBSCRIPTION_AGENT_WORKER"
    )
    pubsub_max_retries: int = Field(default=3, alias="PUBSUB_MAX_RETRIES")

    # -------------------------------------------------------------------------
    # Firestore
    # -------------------------------------------------------------------------
    firestore_project_id: str = Field(
        default="local-project", alias="FIRESTORE_PROJECT_ID"
    )
    firestore_emulator_host: Optional[str] = Field(
        default=None, alias="FIRESTORE_EMULATOR_HOST"
    )

    # -------------------------------------------------------------------------
    # Langfuse (LLM observability)
    # -------------------------------------------------------------------------
    langfuse_host: str = Field(
        default="http://localhost:3000", alias="LANGFUSE_HOST"
    )
    langfuse_public_key: Optional[str] = Field(
        default=None, alias="LANGFUSE_PUBLIC_KEY"
    )
    langfuse_secret_key: Optional[str] = Field(
        default=None, alias="LANGFUSE_SECRET_KEY"
    )
    langfuse_enabled: bool = Field(default=True, alias="LANGFUSE_ENABLED")

    # -------------------------------------------------------------------------
    # Security
    # -------------------------------------------------------------------------
    internal_api_token: str = Field(
        default="dev-token-change-in-production",
        alias="INTERNAL_API_TOKEN"
    )
    api_auth_enabled: bool = Field(default=False, alias="API_AUTH_ENABLED")

    # -------------------------------------------------------------------------
    # Guardrails
    # -------------------------------------------------------------------------
    guardrail_max_input_length: int = Field(
        default=2000, alias="GUARDRAIL_MAX_INPUT_LENGTH"
    )
    guardrail_max_tool_calls: int = Field(
        default=30, alias="GUARDRAIL_MAX_TOOL_CALLS"
    )
    guardrail_injection_detection: bool = Field(
        default=True, alias="GUARDRAIL_INJECTION_DETECTION"
    )

    # -------------------------------------------------------------------------
    # Rate limiting
    # -------------------------------------------------------------------------
    rate_limit_rpm: int = Field(
        default=10, alias="RATE_LIMIT_RPM",
        description="Requests per minute per user"
    )
    rate_limit_tokens_per_day: int = Field(
        default=100000, alias="RATE_LIMIT_TOKENS_PER_DAY"
    )

    # -------------------------------------------------------------------------
    # Observability
    # -------------------------------------------------------------------------
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_format: str = Field(
        default="json", alias="LOG_FORMAT",
        description="'json' for production, 'text' for local readability"
    )
    environment: str = Field(default="local", alias="ENVIRONMENT")
    service_name: str = Field(default="stock-agent", alias="SERVICE_NAME")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        populate_by_name = True

    # -------------------------------------------------------------------------
    # Derived properties
    # -------------------------------------------------------------------------

    @property
    def is_local(self) -> bool:
        return self.environment == "local"

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def use_vertex_ai(self) -> bool:
        """True when running on GCP with Workload Identity (no API key needed)."""
        return self.llm_provider == "vertex_ai"

    @property
    def use_pubsub_emulator(self) -> bool:
        return bool(self.pubsub_emulator_host)

    @property
    def use_firestore_emulator(self) -> bool:
        return bool(self.firestore_emulator_host)

    def get_model(self, tier: str) -> str:
        """
        Return the configured model name for a given cost tier.

        Usage:
            model = settings.get_model("fast")   # For guardrail checks
            model = settings.get_model("main")   # For agent tasks
            model = settings.get_model("strong") # For synthesis
        """
        mapping = {
            "fast": self.gemini_fast_model,
            "main": self.gemini_main_model,
            "strong": self.gemini_strong_model,
        }
        return mapping.get(tier, self.gemini_main_model)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return a singleton Settings instance.

    Cached with lru_cache so the env vars are read only once.
    In tests, call get_settings.cache_clear() to reload.
    """
    return Settings()


# =============================================================================
# LLM Factory
# =============================================================================

def get_llm(tier: str = "main"):
    """
    Factory function that returns the appropriate LLM instance.

    Teaching note:
      This is the model routing pattern.
      - "fast"   → gemini-2.5-flash-lite (guardrail checks, intent routing)
      - "main"   → gemini-2.5-flash      (all 4 agent tasks)
      - "strong" → gemini-2.5-pro        (final synthesis only)

      In production (Vertex AI), no API key is needed — Workload Identity
      grants the GKE pod permission to call Vertex AI directly.
    """
    settings = get_settings()
    model_name = settings.get_model(tier)

    if settings.use_vertex_ai:
        # Production: uses GCP Workload Identity — no key in the container
        try:
            from langchain_google_vertexai import ChatVertexAI
            return ChatVertexAI(
                model_name=model_name,
                project=settings.gcp_project,
                location=settings.gcp_region,
                max_output_tokens=settings.llm_max_tokens,
                temperature=0.1 if tier == "fast" else 0.2,
            )
        except ImportError:
            raise ImportError(
                "langchain-google-vertexai not installed. "
                "Run: pip install langchain-google-vertexai"
            )
    else:
        # Local dev: uses Google AI Studio API key
        if not settings.gemini_api_key:
            raise ValueError(
                "GEMINI_API_KEY is required when LLM_PROVIDER=google_ai_studio. "
                "Get a free key at: https://aistudio.google.com/apikey"
            )
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
            return ChatGoogleGenerativeAI(
                model=model_name,
                google_api_key=settings.gemini_api_key,
                max_output_tokens=settings.llm_max_tokens,
                temperature=0.1 if tier == "fast" else 0.2,
            )
        except ImportError:
            raise ImportError(
                "langchain-google-genai not installed. "
                "Run: pip install langchain-google-genai"
            )
