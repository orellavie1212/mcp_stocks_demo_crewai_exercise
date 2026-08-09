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

    gcp_project: str = Field(default="local-project", alias="GCP_PROJECT")
    gcp_region: str = Field(default="us-central1", alias="GCP_REGION")

    llm_provider: str = Field(default="google_ai_studio", alias="LLM_PROVIDER")
    gemini_api_key: Optional[str] = Field(default=None, alias="GEMINI_API_KEY")

    openai_api_key: Optional[str] = Field(default=None, alias="OPENAI_API_KEY")
    openai_fast_model: str = Field(
        default="gpt-4o-mini", alias="OPENAI_FAST_MODEL",
        description="Cheap model for guardrails and routing"
    )
    openai_main_model: str = Field(
        default="gpt-4o-mini", alias="OPENAI_MAIN_MODEL",
        description="Main model for agent tasks (research, technical, sector)"
    )
    openai_strong_model: str = Field(
        default="gpt-4o", alias="OPENAI_STRONG_MODEL",
        description="Powerful model for final synthesis (used sparingly)"
    )

    gemini_fast_model: str = Field(
        default="gemini-3.1-flash-lite", alias="GEMINI_FAST_MODEL",
        description="Cheap model for guardrails and routing (~$0.00 on free tier)"
    )
    gemini_main_model: str = Field(
        default="gemini-3.5-flash", alias="GEMINI_MAIN_MODEL",
        description="Main model for agent tasks (research, technical, sector)"
    )
    gemini_strong_model: str = Field(
        default="gemini-3.1-pro-preview", alias="GEMINI_STRONG_MODEL",
        description="Powerful model for final synthesis (used sparingly)"
    )

    llm_max_tokens: int = Field(default=4096, alias="LLM_MAX_TOKENS")
    llm_timeout_seconds: int = Field(default=120, alias="LLM_TIMEOUT_SECONDS")

    mcp_server_url: str = Field(
        default="http://localhost:8001", alias="MCP_SERVER_URL"
    )
    job_api_url: str = Field(
        default="http://localhost:8000", alias="JOB_API_URL"
    )
    agent_runtime_url: str = Field(
        default="http://localhost:8002", alias="AGENT_RUNTIME_URL"
    )

    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    redis_cache_ttl_seconds: int = Field(
        default=3600, alias="REDIS_CACHE_TTL_SECONDS",
        description="1 hour default cache for stock analysis results"
    )

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

    firestore_project_id: str = Field(
        default="local-project", alias="FIRESTORE_PROJECT_ID"
    )
    firestore_emulator_host: Optional[str] = Field(
        default=None, alias="FIRESTORE_EMULATOR_HOST"
    )

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

    litellm_base_url: Optional[str] = Field(
        default=None, alias="LITELLM_BASE_URL",
        description="When set, get_llm() routes through this LiteLLM proxy "
                     "instead of calling the vendor SDK directly. Set by "
                     "default in docker-compose.yml (Lab 3); left unset for "
                     "Labs 1-2, which run without the proxy container."
    )
    litellm_api_key: Optional[str] = Field(
        default=None, alias="LITELLM_MASTER_KEY"
    )

    internal_api_token: str = Field(
        default="dev-token-change-in-production",
        alias="INTERNAL_API_TOKEN"
    )
    api_auth_enabled: bool = Field(default=False, alias="API_AUTH_ENABLED")

    guardrail_max_input_length: int = Field(
        default=2000, alias="GUARDRAIL_MAX_INPUT_LENGTH"
    )
    guardrail_max_tool_calls: int = Field(
        default=30, alias="GUARDRAIL_MAX_TOOL_CALLS"
    )
    guardrail_injection_detection: bool = Field(
        default=True, alias="GUARDRAIL_INJECTION_DETECTION"
    )

    rate_limit_rpm: int = Field(
        default=10, alias="RATE_LIMIT_RPM",
        description="Requests per minute per user"
    )
    rate_limit_tokens_per_day: int = Field(
        default=100000, alias="RATE_LIMIT_TOKENS_PER_DAY"
    )

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
        # This one .env is shared across every service (GCP, Redis, Pub/Sub,
        # Langfuse, guardrails, ...); no single Settings consumer declares
        # every key in it. Without "ignore", any key here that isn't a field
        # on *this* Settings subclass — e.g. VERTEX_LOCATION, which get_llm()
        # reads straight from os.environ rather than through a field — trips
        # pydantic-settings' default "forbid" and get_settings() raises for
        # every caller, not just the one that cares about that key.
        extra = "ignore"

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

    def get_model(self, tier: str, provider: Optional[str] = None) -> str:
        """
        Return the configured model name for a given cost tier.

        Defaults to whichever provider LLM_PROVIDER currently names; pass
        `provider` to resolve the model for a different provider without
        changing LLM_PROVIDER (used by get_llm()'s per-call override).

        Usage:
            model = settings.get_model("fast")   # For guardrail checks
            model = settings.get_model("main")   # For agent tasks
            model = settings.get_model("strong") # For synthesis
        """
        by_provider = {
            "openai": {
                "fast": self.openai_fast_model,
                "main": self.openai_main_model,
                "strong": self.openai_strong_model,
            },
            "vertex_ai": {
                "fast": self.gemini_fast_model,
                "main": self.gemini_main_model,
                "strong": self.gemini_strong_model,
            },
            "google_ai_studio": {
                "fast": self.gemini_fast_model,
                "main": self.gemini_main_model,
                "strong": self.gemini_strong_model,
            },
        }
        tiers = by_provider.get(provider or self.llm_provider, by_provider["google_ai_studio"])
        return tiers.get(tier, tiers["main"])


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return a singleton Settings instance.

    Cached with lru_cache so the env vars are read only once.
    In tests, call get_settings.cache_clear() to reload.
    """
    return Settings()


def _map_vertex_model_name(model_name: str) -> str:
    """Pass the model name through directly without any fallback mapping."""
    return model_name


def _crewai_llm_class(langchain_only: bool):
    """crewai's LLM class, or None to force the LangChain fallback below.

    get_llm() prefers crewai's own LLM class whenever crewai happens to be
    importable, because CrewAI's Agent(llm=...) validates strictly and
    rejects a raw LangChain chat model. But a caller that isn't building a
    CrewAI Agent — e.g. mcp-server's /explain route, which calls
    `.invoke()` with langchain_core message objects — needs the LangChain
    object specifically; crewai.LLM has no `.invoke()` (it has `.call()`,
    with a different message shape). Whether crewai is importable is an
    environment accident (a Docker image that deliberately omits it vs. a
    shared local venv that happens to have it for another app), not
    something callers like /explain should have to guess around — hence
    this explicit flag instead of relying on ImportError.
    """
    if langchain_only:
        return None
    try:
        from crewai import LLM as CrewLLM
        return CrewLLM
    except ImportError:
        return None


def get_llm(
    tier: str = "main",
    provider: Optional[str] = None,
    api_key: str = "",
    langchain_only: bool = False,
):
    """
    Factory function that returns the appropriate LLM instance.

    Teaching note:
      This is the model routing pattern.
      - "fast"   → cheap model   (guardrail checks, intent routing)
      - "main"   → main model    (all 4 agent tasks)
      - "strong" → strongest model (final synthesis only)

      Which vendor "fast"/"main"/"strong" map to defaults to LLM_PROVIDER:
      "openai", "google_ai_studio", or "vertex_ai". In production (Vertex AI),
      no API key is needed — Workload Identity grants the GKE pod permission
      to call Vertex AI directly.

      `provider` / `api_key` let one call pick a different vendor and bring
      its own key — e.g. a student pasting a personal OpenAI key into the
      Streamlit sidebar for a single run, without touching .env or the
      LLM_PROVIDER the rest of the process is using. Passed straight to the
      LLM constructor as `api_key=`, never through os.environ, so it can't
      leak into a concurrent request the way an env-var swap could.
      When neither is passed, behavior is unchanged from before this
      parameter existed (including the LiteLLM-proxy-first routing below,
      which only applies to the server-configured default path — an
      explicit override bypasses the proxy and calls the vendor directly,
      since the proxy's config.yaml only knows the server's own keys).

      `langchain_only=True` forces a LangChain chat model (`.invoke()`)
      even if crewai is importable in this process — see
      _crewai_llm_class()'s docstring. Only needed by callers that aren't
      handing this to a CrewAI Agent.
    """
    settings = get_settings()
    from providers import get_provider, MissingAPIKey, UnknownProvider  # noqa: F401

    override = provider is not None or bool(api_key)
    effective_provider = get_provider(provider or settings.llm_provider).name
    model_name = settings.get_model(tier, provider=effective_provider)
    temperature = 0.1 if tier == "fast" else 0.2

    if settings.litellm_base_url and not override:
        # Route through the LiteLLM proxy (Lab 3 docker-compose, Lab 4 GCP)
        # instead of calling the vendor SDK directly. LiteLLM's proxy always
        # exposes an OpenAI-compatible /chat/completions endpoint regardless
        # of the backing vendor, so "openai/<model>" + base_url is how any
        # provider gets called through it — the proxy's own model_list
        # (docker/litellm/config.yaml) is what maps model_name back to the
        # real vendor. Covers all three LLM_PROVIDER values with one path.
        #
        # vertex_ai gets a distinct "vertex-<model>" name in that model_list
        # (its litellm_params.model is "vertex_ai/<model>", not "gemini/...").
        # Without this, every provider would resolve to the *same* model_name
        # ("gemini-3.5-flash") and the proxy's config.yaml — which registers
        # "gemini-3.5-flash" once, against the google_ai_studio backend —
        # would silently serve Google AI Studio for a request that asked for
        # Vertex AI, using a GEMINI_API_KEY that may not even be set.
        litellm_model_name = (
            f"vertex-{model_name}" if effective_provider == "vertex_ai" else model_name
        )
        litellm_key = settings.litellm_api_key or "sk-local-litellm-master"
        CrewLLM = _crewai_llm_class(langchain_only)
        if CrewLLM is not None:
            return CrewLLM(
                model=f"openai/{litellm_model_name}",
                base_url=settings.litellm_base_url,
                api_key=litellm_key,
                max_tokens=settings.llm_max_tokens,
                temperature=temperature,
            )
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=litellm_model_name,
            base_url=settings.litellm_base_url,
            api_key=litellm_key,
            max_tokens=settings.llm_max_tokens,
            temperature=temperature,
        )

    if effective_provider == "openai":
        openai_key = api_key or settings.openai_api_key
        if not openai_key:
            raise MissingAPIKey(get_provider("openai"))
        CrewLLM = _crewai_llm_class(langchain_only)
        if CrewLLM is not None:
            return CrewLLM(
                model=f"openai/{model_name}",
                api_key=openai_key,
                max_tokens=settings.llm_max_tokens,
                temperature=temperature,
            )
        try:
            from langchain_openai import ChatOpenAI
            return ChatOpenAI(
                model=model_name,
                api_key=openai_key,
                max_tokens=settings.llm_max_tokens,
                temperature=temperature,
            )
        except ImportError:
            raise ImportError(
                "langchain-openai not installed. Run: pip install langchain-openai"
            )

    if effective_provider == "vertex_ai":
        model_name = _map_vertex_model_name(model_name)
        # Force system environment variables to global for LiteLLM and LangChain Vertex AI compatibility
        vertex_loc = os.environ.get("VERTEX_LOCATION", "global")
        os.environ["VERTEX_LOCATION"] = vertex_loc
        os.environ["VERTEX_AI_LOCATION"] = vertex_loc
        os.environ["VERTEX_API_LOCATION"] = vertex_loc

    is_vertex = effective_provider == "vertex_ai"
    gemini_key = api_key or settings.gemini_api_key

    # If CrewAI is installed (e.g. in agent-runtime worker), return its native LLM object
    # to avoid strict validation / unknown object type errors with raw LangChain objects.
    CrewLLM = _crewai_llm_class(langchain_only)
    if CrewLLM is not None:
        if is_vertex:
            return CrewLLM(
                model=f"vertex_ai/{model_name}",
                vertex_project=settings.gcp_project,
                vertex_location=os.environ.get("VERTEX_LOCATION", "global"),
                max_tokens=settings.llm_max_tokens,
                temperature=0.1 if tier == "fast" else 0.2,
            )
        else:
            if not gemini_key:
                raise MissingAPIKey(get_provider("google_ai_studio"))
            return CrewLLM(
                model=f"gemini/{model_name}",
                api_key=gemini_key,
                max_tokens=settings.llm_max_tokens,
                temperature=0.1 if tier == "fast" else 0.2,
            )
    else:
        # CrewAI is not available (e.g. minimal mcp-server container), or
        # langchain_only=True was requested — return a raw LangChain object.
        if is_vertex:
            try:
                from langchain_google_vertexai import ChatVertexAI
                return ChatVertexAI(
                    model_name=model_name,
                    project=settings.gcp_project,
                    location=os.environ.get("VERTEX_LOCATION", "global"),
                    max_output_tokens=settings.llm_max_tokens,
                    temperature=0.1 if tier == "fast" else 0.2,
                )
            except ImportError:
                raise ImportError(
                    "langchain-google-vertexai not installed. "
                    "Run: pip install langchain-google-vertexai"
                )
        else:
            if not gemini_key:
                raise MissingAPIKey(get_provider("google_ai_studio"))
            try:
                from langchain_google_genai import ChatGoogleGenerativeAI
                return ChatGoogleGenerativeAI(
                    model=model_name,
                    google_api_key=gemini_key,
                    max_output_tokens=settings.llm_max_tokens,
                    temperature=0.1 if tier == "fast" else 0.2,
                )
            except ImportError:
                raise ImportError(
                    "langchain-google-genai not installed. "
                    "Run: pip install langchain-google-genai"
                )
