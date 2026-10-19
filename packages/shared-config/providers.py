"""
shared-config — provider registry.

Teaching note:
  A small, explicit table of "which vendor, which env var" so config.py's
  get_llm() doesn't need per-provider special-casing sprinkled through it,
  and so a missing-key error can say exactly which variable to set.

  This does not replace the existing LLM_PROVIDER / 3-tier model routing in
  config.py — it just gives that logic a shared vocabulary (provider name,
  label, key env var) instead of hardcoding "openai" / "gemini" strings in
  more than one place.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class Provider:
    name: str  # value of LLM_PROVIDER
    label: str  # human-readable, for UI/error messages
    api_key_env: str  # env var the vendor SDK reads; "" if none needed (Vertex ADC)
    error_code: str  # in-band error code when the key/credential is missing


PROVIDERS: Dict[str, Provider] = {
    "openai": Provider(
        name="openai",
        label="OpenAI",
        api_key_env="OPENAI_API_KEY",
        error_code="openai_api_key_required",
    ),
    "google_ai_studio": Provider(
        name="google_ai_studio",
        label="Google Gemini (AI Studio)",
        api_key_env="GEMINI_API_KEY",
        error_code="gemini_api_key_required",
    ),
    "vertex_ai": Provider(
        name="vertex_ai",
        label="Google Gemini (Vertex AI)",
        # Workload Identity / application-default credentials, not an API key.
        api_key_env="",
        error_code="vertex_ai_credentials_required",
    ),
}

DEFAULT_PROVIDER = "vertex_ai"


class MissingAPIKey(ValueError):
    """No usable key/credential for the chosen provider.

    Subclasses ValueError (not just Exception) so it's catchable the same
    way as the "GEMINI_API_KEY is required" ValueErrors get_llm() already
    raises elsewhere in config.py — one exception family for "config says
    to use a provider that isn't actually usable right now."
    """

    def __init__(self, provider: Provider):
        self.provider = provider
        self.error_code = provider.error_code
        if provider.api_key_env:
            message = (
                f"{provider.label} API key is required. Set {provider.api_key_env} "
                "in .env."
            )
        else:
            message = (
                f"{provider.label} requires application-default credentials "
                "(Workload Identity on GKE, or `gcloud auth application-default login` "
                "locally)."
            )
        super().__init__(message)


class UnknownProvider(ValueError):
    """A provider name that is not in PROVIDERS."""

    error_code = "unknown_provider"


def get_provider(name: Optional[str]) -> Provider:
    """Look up a provider by name, case- and whitespace-insensitively."""
    key = (name or "").strip().lower()
    try:
        return PROVIDERS[key or DEFAULT_PROVIDER]
    except KeyError:
        raise UnknownProvider(
            f"Unknown provider {name!r}. Known providers: {sorted(PROVIDERS)}"
        ) from None


def _is_real_key(value: Optional[str]) -> bool:
    """False for empty values and the placeholders .env.example ships with
    ("your-gemini-api-key-here"), which are not credentials."""
    v = (value or "").strip().lower()
    return bool(v) and not v.startswith("your-") and not v.endswith("-here")


def configured_providers() -> List[str]:
    """Providers with a key/credential available right now.

    Vertex AI has no key env var (Workload Identity/ADC), so it always
    counts as "configured" here — whether the credentials actually work is
    a runtime question this function does not answer.
    """
    return [
        p.name
        for p in PROVIDERS.values()
        if not p.api_key_env or _is_real_key(os.getenv(p.api_key_env))
    ]
