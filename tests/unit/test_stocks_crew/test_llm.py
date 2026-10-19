"""llm.py: provider resolution on top of packages/shared-config. No network, no tokens."""
import pytest

pytest.importorskip("crewai", reason="crewai not installed")

from stocks_crew import llm  # noqa: E402
from config import get_settings  # noqa: E402


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ("OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "LITELLM_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "google_ai_studio")
    monkeypatch.setenv("GCP_PROJECT", "demo-project")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_all_three_providers_are_offered():
    assert set(llm.provider_names()) == {"openai", "google_ai_studio", "vertex_ai"}


def test_default_provider_follows_llm_provider(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "vertex_ai")
    get_settings.cache_clear()
    assert llm.default_provider() == "vertex_ai"


def test_missing_gemini_key_raises_with_its_error_code():
    with pytest.raises(llm.MissingAPIKey) as exc:
        llm.resolve()
    assert exc.value.error_code == "gemini_api_key_required"


def test_missing_openai_key_raises_with_its_error_code():
    with pytest.raises(llm.MissingAPIKey) as exc:
        llm.resolve("openai")
    assert exc.value.error_code == "openai_api_key_required"


def test_unknown_provider_raises_with_its_error_code():
    with pytest.raises(llm.UnknownProvider) as exc:
        llm.resolve("llama-at-home")
    assert exc.value.error_code == "unknown_provider"


def test_per_request_key_beats_the_environment():
    cfg = llm.resolve("openai", api_key="sk-sidebar")
    assert cfg.provider.name == "openai"
    assert type(cfg.llm).__name__ == "OpenAICompletion"


def test_gemini_key_selects_the_gemini_client():
    cfg = llm.resolve("google_ai_studio", api_key="AIza-test")
    assert cfg.provider.name == "google_ai_studio"
    assert "Gemini" in type(cfg.llm).__name__


def test_vertex_needs_no_api_key():
    cfg = llm.resolve("vertex_ai")
    assert cfg.provider.name == "vertex_ai"
    assert cfg.model


def test_provider_names_are_case_insensitive():
    assert llm.resolve(" OpenAI ", api_key="sk-x").provider.name == "openai"


def test_model_for_reports_the_model_each_provider_uses():
    assert llm.model_for("openai") != llm.model_for("google_ai_studio")


def test_configured_providers_lists_only_those_with_a_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    configured = llm.configured_providers()
    assert "openai" in configured
    assert "vertex_ai" in configured  # ADC, no key to check
    assert "google_ai_studio" not in configured


def test_vertex_ai_is_the_default_provider_when_nothing_is_configured(monkeypatch):
    """No .env, no LLM_PROVIDER: a learner who skipped .env gets Vertex AI (their
    Google login), not a demand for a Gemini key."""
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    get_settings.cache_clear()
    assert llm.default_provider() == "vertex_ai"


@pytest.mark.parametrize("placeholder", ["your-gcp-project-id", "local-project", ""])
def test_vertex_with_a_placeholder_project_is_refused_with_a_clear_error(
    monkeypatch, placeholder
):
    monkeypatch.setenv("LLM_PROVIDER", "vertex_ai")
    monkeypatch.setenv("GCP_PROJECT", placeholder)
    get_settings.cache_clear()
    with pytest.raises(llm.MissingGCPProject) as exc:
        llm.resolve()
    assert exc.value.error_code == "gcp_project_required"
    assert "GCP_PROJECT" in str(exc.value)


def test_a_placeholder_project_does_not_block_the_other_providers(monkeypatch):
    monkeypatch.setenv("GCP_PROJECT", "your-gcp-project-id")
    get_settings.cache_clear()
    assert llm.resolve("openai", api_key="sk-x").provider.name == "openai"


@pytest.mark.parametrize("placeholder", ["your-openai-api-key-here", "your-gemini-api-key-here", ""])
def test_placeholder_keys_do_not_count_as_configured(monkeypatch, placeholder):
    monkeypatch.setenv("OPENAI_API_KEY", placeholder)
    monkeypatch.setenv("GEMINI_API_KEY", placeholder)
    configured = llm.configured_providers()
    assert "openai" not in configured
    assert "google_ai_studio" not in configured
    assert "vertex_ai" in configured
