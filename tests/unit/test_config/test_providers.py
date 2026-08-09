"""
Unit tests for the LLM provider registry and the provider-aware model routing
in shared-config (packages/shared-config/providers.py, config.py).

Run: python -m pytest tests/unit/test_config/ -v
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../packages/shared-config"))

import pytest

from providers import (
    PROVIDERS,
    DEFAULT_PROVIDER,
    MissingAPIKey,
    UnknownProvider,
    get_provider,
    configured_providers,
)
from config import Settings, get_settings


class TestProviderRegistry:
    def test_known_providers(self):
        assert set(PROVIDERS) == {"openai", "google_ai_studio", "vertex_ai"}

    def test_get_provider_default(self):
        assert get_provider(None).name == DEFAULT_PROVIDER
        assert get_provider("").name == DEFAULT_PROVIDER

    def test_get_provider_case_insensitive(self):
        assert get_provider("OpenAI").name == "openai"
        assert get_provider(" vertex_ai ").name == "vertex_ai"

    def test_unknown_provider_raises(self):
        with pytest.raises(UnknownProvider):
            get_provider("anthropic")

    def test_vertex_ai_has_no_key_env(self):
        assert PROVIDERS["vertex_ai"].api_key_env == ""

    def test_missing_api_key_message_names_env_var(self):
        err = MissingAPIKey(PROVIDERS["openai"])
        assert "OPENAI_API_KEY" in str(err)
        assert err.error_code == "openai_api_key_required"

    def test_missing_credentials_message_for_vertex(self):
        err = MissingAPIKey(PROVIDERS["vertex_ai"])
        assert "application-default credentials" in str(err)

    def test_configured_providers_reflects_env(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        names = configured_providers()
        assert "openai" not in names
        assert "google_ai_studio" not in names
        assert "vertex_ai" in names  # no key required

        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        assert "openai" in configured_providers()


class TestGetModelRouting:
    # _env_file=None: isolate from the repo's real .env, which carries keys
    # (e.g. VERTEX_LOCATION) that aren't declared Settings fields and would
    # otherwise fail these tests with extra_forbidden regardless of provider.
    def test_openai_tiers(self):
        settings = Settings(
            _env_file=None,
            LLM_PROVIDER="openai",
            OPENAI_FAST_MODEL="gpt-4o-mini",
            OPENAI_MAIN_MODEL="gpt-4o-mini",
            OPENAI_STRONG_MODEL="gpt-4o",
        )
        assert settings.get_model("fast") == "gpt-4o-mini"
        assert settings.get_model("strong") == "gpt-4o"

    def test_gemini_tiers_unaffected_by_openai_fields(self):
        settings = Settings(
            _env_file=None,
            LLM_PROVIDER="google_ai_studio",
            GEMINI_FAST_MODEL="gemini-3.1-flash-lite",
            GEMINI_STRONG_MODEL="gemini-3.1-pro-preview",
        )
        assert settings.get_model("fast") == "gemini-3.1-flash-lite"
        assert settings.get_model("strong") == "gemini-3.1-pro-preview"

    def test_unknown_tier_falls_back_to_main(self):
        settings = Settings(_env_file=None, LLM_PROVIDER="openai", OPENAI_MAIN_MODEL="gpt-4o-mini")
        assert settings.get_model("nonsense") == "gpt-4o-mini"

    def test_unknown_provider_falls_back_to_google_ai_studio_models(self):
        settings = Settings(
            _env_file=None, LLM_PROVIDER="anthropic", GEMINI_MAIN_MODEL="gemini-3.5-flash"
        )
        assert settings.get_model("main") == "gemini-3.5-flash"


class TestGetLlmOpenAIBranch:
    def test_raises_when_no_api_key(self, monkeypatch):
        import config as config_module

        # _env_file=None: isolate from the repo's real .env (see note above).
        no_key_settings = Settings(_env_file=None, LLM_PROVIDER="openai", OPENAI_API_KEY=None)
        monkeypatch.setattr(config_module, "get_settings", lambda: no_key_settings)

        with pytest.raises(ValueError, match="OPENAI_API_KEY"):
            config_module.get_llm("main")


class TestGetLlmLiteLLMRouting:
    """litellm_base_url takes priority over LLM_PROVIDER — one code path
    covers openai/google_ai_studio/vertex_ai once they're routed through the
    proxy (docker-compose sets LITELLM_BASE_URL by default for Lab 3)."""

    def test_routes_through_proxy_when_base_url_set(self, monkeypatch):
        import config as config_module

        proxied_settings = Settings(
            _env_file=None,
            LLM_PROVIDER="google_ai_studio",  # ignored once litellm_base_url is set
            GEMINI_MAIN_MODEL="gemini-3.5-flash",
            LITELLM_BASE_URL="http://litellm-proxy:4000",
            LITELLM_MASTER_KEY="sk-test-master",
        )
        monkeypatch.setattr(config_module, "get_settings", lambda: proxied_settings)

        llm = config_module.get_llm("main")

        assert llm.model == "openai/gemini-3.5-flash"
        assert llm.base_url == "http://litellm-proxy:4000"
        assert llm.api_key == "sk-test-master"

    def test_missing_master_key_falls_back_to_local_default(self, monkeypatch):
        import config as config_module

        proxied_settings = Settings(
            _env_file=None,
            LITELLM_BASE_URL="http://litellm-proxy:4000",
            LITELLM_MASTER_KEY=None,
        )
        monkeypatch.setattr(config_module, "get_settings", lambda: proxied_settings)

        llm = config_module.get_llm("main")
        assert llm.api_key == "sk-local-litellm-master"

    def test_vertex_ai_gets_distinct_model_name(self, monkeypatch):
        """vertex_ai must resolve to "vertex-<model>" in the proxy's
        model_list, not the bare name google_ai_studio also uses — otherwise
        a vertex_ai request would silently be served by the google_ai_studio
        backend registered under that same bare name."""
        import config as config_module

        proxied_settings = Settings(
            _env_file=None,
            LLM_PROVIDER="vertex_ai",
            GEMINI_MAIN_MODEL="gemini-3.5-flash",
            LITELLM_BASE_URL="http://litellm-proxy:4000",
            LITELLM_MASTER_KEY="sk-test-master",
        )
        monkeypatch.setattr(config_module, "get_settings", lambda: proxied_settings)

        llm = config_module.get_llm("main")

        assert llm.model.endswith("vertex-gemini-3.5-flash")


class TestGetLlmPerCallOverride:
    """provider=/api_key= let one call pick a different vendor+key than
    LLM_PROVIDER/.env without mutating process state — the per-request
    switcher a student uses from the Streamlit sidebar or the API body."""

    def test_provider_override_beats_llm_provider(self, monkeypatch):
        import config as config_module

        settings = Settings(
            _env_file=None,
            LLM_PROVIDER="google_ai_studio",
            GEMINI_API_KEY="gm-env-key",
            OPENAI_MAIN_MODEL="gpt-4o-mini",
        )
        monkeypatch.setattr(config_module, "get_settings", lambda: settings)

        llm = config_module.get_llm("main", provider="openai", api_key="sk-request-key")

        # CrewAI's LLM drops the "openai/" prefix internally (its litellm
        # default provider), so what we passed and what lands on the object
        # differ; the assertion that matters is the vendor+key actually used.
        assert llm.model.endswith("gpt-4o-mini")
        assert llm.api_key == "sk-request-key"

    def test_api_key_override_beats_env_key_same_provider(self, monkeypatch):
        import config as config_module

        settings = Settings(
            _env_file=None,
            LLM_PROVIDER="openai",
            OPENAI_API_KEY="sk-env-key",
            OPENAI_MAIN_MODEL="gpt-4o-mini",
        )
        monkeypatch.setattr(config_module, "get_settings", lambda: settings)

        llm = config_module.get_llm("main", api_key="sk-request-key")

        assert llm.api_key == "sk-request-key"

    def test_override_bypasses_litellm_proxy(self, monkeypatch):
        import config as config_module

        settings = Settings(
            _env_file=None,
            LITELLM_BASE_URL="http://litellm-proxy:4000",
            LITELLM_MASTER_KEY="sk-proxy-master",
            OPENAI_MAIN_MODEL="gpt-4o-mini",
        )
        monkeypatch.setattr(config_module, "get_settings", lambda: settings)

        llm = config_module.get_llm("main", provider="openai", api_key="sk-request-key")

        # A student's own key talks straight to the vendor, not the proxy —
        # the proxy's config.yaml has no idea about a key it never saw.
        assert llm.api_key == "sk-request-key"
        assert getattr(llm, "base_url", None) != "http://litellm-proxy:4000"

    def test_unknown_provider_override_raises(self, monkeypatch):
        import config as config_module

        settings = Settings(_env_file=None, LLM_PROVIDER="google_ai_studio")
        monkeypatch.setattr(config_module, "get_settings", lambda: settings)

        with pytest.raises(UnknownProvider):
            config_module.get_llm("main", provider="anthropic")

    def test_no_override_unchanged_behavior(self, monkeypatch):
        """Baseline: omitting provider/api_key resolves exactly as before."""
        import config as config_module

        settings = Settings(
            _env_file=None,
            LLM_PROVIDER="openai",
            OPENAI_API_KEY="sk-env-key",
            OPENAI_MAIN_MODEL="gpt-4o-mini",
        )
        monkeypatch.setattr(config_module, "get_settings", lambda: settings)

        llm = config_module.get_llm("main")
        assert llm.api_key == "sk-env-key"
