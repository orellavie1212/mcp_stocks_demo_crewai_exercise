"""Test isolation from a developer's local .env.

A checkout that has a real .env (copied from .env.example, with a project id,
placeholder keys, and Docker-only hostnames such as REDIS_URL) must not change
what the unit tests see:

* PYTHON_DOTENV_DISABLED stops python-dotenv loading .env into os.environ when
  modules such as stocks_crew.api are imported during collection.
* Each test runs from an empty directory, so pydantic-settings does not find a
  .env through its relative path either. Tests that need a setting set it with
  monkeypatch.
"""
import os

import pytest


def pytest_configure(config):
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"


def _clear_settings_cache():
    try:
        import config
    except ImportError:
        return
    # Some tests replace get_settings with a plain function; only clear a real
    # lru_cache.
    clear = getattr(getattr(config, "get_settings", None), "cache_clear", None)
    if clear is not None:
        clear()


@pytest.fixture(autouse=True)
def _isolated_from_dotenv(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _clear_settings_cache()
    yield
    _clear_settings_cache()
