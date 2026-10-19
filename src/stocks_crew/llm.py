# llm.py -- which model the crew talks to, and with whose credentials.
#
# Lab 1 does not carry its own provider logic. It uses the same switcher as
# Labs 2-4 (packages/shared-config): LLM_PROVIDER picks openai, google_ai_studio
# or vertex_ai, and get_llm() builds the CrewAI LLM for it. This module only
# adds what a crew entry point needs on top: resolving a provider name coming
# from a UI or CLI, and reporting what was chosen.
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional

from . import _paths  # noqa: F401  (puts packages/shared-* on sys.path)
from config import get_llm, get_settings  # noqa: E402
from providers import (  # noqa: E402
    PROVIDERS,
    MissingAPIKey,
    Provider,
    UnknownProvider,
    configured_providers,
    get_provider,
)

__all__ = [
    "PROVIDERS",
    "LLMConfig",
    "MissingGCPProject",
    "MissingAPIKey",
    "Provider",
    "UnknownProvider",
    "configured_providers",
    "default_provider",
    "model_for",
    "provider_names",
    "resolve",
]


#: What GCP_PROJECT holds before anyone has set it: the Makefile's default, the
#: .env.example value, and the settings default.
PLACEHOLDER_PROJECTS = {"", "your-gcp-project-id", "local-project"}


class MissingGCPProject(ValueError):
    """Vertex AI was chosen but GCP_PROJECT is still a placeholder.

    Without this, the first sign of the problem is a 403 from Google about a
    project called "your-gcp-project-id".
    """

    error_code = "gcp_project_required"

    def __init__(self, current: str):
        self.current = current
        super().__init__(
            "Vertex AI needs your Google Cloud project ID. Set GCP_PROJECT in .env "
            f"(it is {current!r}), then restart. Find it with: gcloud config get-value project"
        )


@dataclass(frozen=True)
class LLMConfig:
    """The model a run will use, already built."""

    provider: Provider
    model: str  # the bare model name, for display
    llm: Any  # a crewai.LLM


def provider_names() -> List[str]:
    return list(PROVIDERS)


def default_provider() -> str:
    """The provider used when nobody names one: LLM_PROVIDER, else the registry default."""
    return get_provider(get_settings().llm_provider).name


def model_for(provider: Optional[str] = None) -> str:
    """The bare model name the crew would use on `provider` (for display)."""
    name = get_provider(provider or get_settings().llm_provider).name
    return get_settings().get_model("main", provider=name)


def resolve(provider: Optional[str] = None, api_key: str = "") -> LLMConfig:
    """Build the LLM for a run.

    Raises UnknownProvider for a name that is not registered and MissingAPIKey
    when the chosen provider has no key. Both carry an `error_code` the caller
    reports in-band.

    With neither argument given this follows .env exactly like Labs 2-4,
    including routing through the LiteLLM proxy when LITELLM_BASE_URL is set.
    A named provider or a key is an explicit override and calls the vendor
    directly.
    """
    chosen = get_provider(provider or get_settings().llm_provider)
    if chosen.name == "vertex_ai" and (get_settings().gcp_project or "").strip() in PLACEHOLDER_PROJECTS:
        raise MissingGCPProject(get_settings().gcp_project)
    explicit = provider if provider and chosen.name != default_provider() else None
    llm = get_llm("main", provider=explicit, api_key=(api_key or "").strip())
    return LLMConfig(
        provider=chosen,
        model=get_settings().get_model("main", provider=chosen.name),
        llm=llm,
    )
