"""Unit tests for AI/model_cache.py (ModelCache) and AI/consult.py (OllamaClient).

Network access is replaced by faked sessions/clients.
"""

import sys

import pytest

from AI.model_cache import ModelCache
from conftest import FakeSession, FakeResponse, build_fake_requests_module


# ---------------------------------------------------------------------------
# ModelCache
# ---------------------------------------------------------------------------


def test_default_api_url_from_env(monkeypatch):
    monkeypatch.setenv("API_PORT", "7777")
    cache = ModelCache()
    assert cache.get_default_api_url() == "http://localhost:7777"


def test_default_api_url_empty_env(monkeypatch):
    monkeypatch.setenv("API_PORT", "")
    cache = ModelCache()
    assert cache.get_default_api_url() == "http://localhost:8000"


def test_fetch_models_parses_dict_and_str(monkeypatch):
    cache = ModelCache()

    class FakeClient:
        def __init__(self, url):
            self.url = url

        def list_models(self):
            return [{"name": "llama3"}, "mistral", {"name": "phi3"}]

    monkeypatch.setattr("AI.model_cache.OllamaClient", FakeClient)
    cache._fetch_models("http://x:8000")
    assert cache._models == ["llama3", "mistral", "phi3"]
    assert cache.is_loaded() is True
    assert cache.is_using_fallback() is False


def test_fetch_models_fallback_on_exception(monkeypatch):
    cache = ModelCache()

    class BrokenClient:
        def __init__(self, url):
            pass

        def list_models(self):
            raise RuntimeError("connection refused")

    monkeypatch.setattr("AI.model_cache.OllamaClient", BrokenClient)
    cache._fetch_models("http://x:8000")
    assert cache.is_loaded() is True
    assert cache.is_using_fallback() is True
    assert cache._models == cache._fallback_models


def test_fetch_models_fallback_on_empty(monkeypatch):
    cache = ModelCache()

    class EmptyClient:
        def __init__(self, url):
            pass

        def list_models(self):
            return []

    monkeypatch.setattr("AI.model_cache.OllamaClient", EmptyClient)
    cache._fetch_models("http://x:8000")
    assert cache.is_using_fallback() is True


def test_get_models_before_load_returns_fallback():
    cache = ModelCache()
    assert cache.get_models(timeout=0.01) == cache._fallback_models


def test_get_models_after_load(monkeypatch):
    cache = ModelCache()
    monkeypatch.setattr(cache, "_fetch_models", lambda url: None)
    cache._models = ["a", "b"]
    cache._loaded = True
    assert cache.get_models() == ["a", "b"]
    assert cache.get_models()[0] == "a"  # copy isolation
    cache.get_models()[0] = "mutated"
    assert cache._models[0] == "a"


def test_load_models_async_noop_when_loading():
    cache = ModelCache()
    cache._loading = True
    cache.load_models_async("http://x")
    assert cache._loading is True  # not restarted


def test_refresh_models_with_fake_client(monkeypatch):
    cache = ModelCache()

    class FakeClient:
        def __init__(self, url):
            pass

        def list_models(self):
            return [{"name": "fresh"}]

    monkeypatch.setattr("AI.model_cache.OllamaClient", FakeClient)
    cache.refresh_models("http://x:8000")
    assert cache._models == ["fresh"]
    assert cache.is_loaded() is True
    assert cache.is_using_fallback() is False


def test_get_real_models_empty_when_fallback(monkeypatch):
    cache = ModelCache()
    cache._models = cache._fallback_models.copy()
    cache._loaded = True
    cache._using_fallback = True
    assert cache.get_real_models(timeout=0.01) == []


def test_is_loading_flag():
    cache = ModelCache()
    assert cache.is_loading() is False


# ---------------------------------------------------------------------------
# OllamaClient (AI/consult.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch):
    from AI.consult import OllamaClient

    session = FakeSession()
    monkeypatch.setattr("AI.consult.OllamaClient", lambda *a, **k: None)  # ensure module imports
    cli = OllamaClient.__new__(OllamaClient)
    cli.base_url = "http://localhost:8000"
    cli.debug = False
    cli.timeout = None
    cli.session = session
    cli.context_data = []
    cli._fail_counts = 0
    cli._breaker_until = None
    return cli, session


def test_health_check_ok(client):
    cli, session = client
    session.responses[("GET", "http://localhost:8000/health")] = FakeResponse(
        200, json_data={"status": "ok"})
    assert cli.health_check() == {"status": "ok"}


def test_health_check_error(client):
    cli, session = client
    import requests as real_requests

    class Boom:
        def get(self, *a, **k):
            raise real_requests.exceptions.ConnectionError("refused")

    cli.session = Boom()
    result = cli.health_check()
    assert result["status"] == "error"


def test_list_models_gateway_first(client):
    cli, session = client
    session.responses[("GET", "http://localhost:8000/models")] = FakeResponse(
        200, json_data={"models": [{"name": "gw-model"}]})
    assert cli.list_models() == [{"name": "gw-model"}]


def test_list_models_falls_back_to_local_ollama(client, monkeypatch):
    cli, session = client
    session.responses[("GET", "http://localhost:8000/models")] = FakeResponse(
        404, json_data={})
    session.responses[("GET", "http://localhost:11434/api/tags")] = FakeResponse(
        200, json_data={"models": [{"name": "local-model"}]})
    monkeypatch.setenv("OLLAMA_HOST", "localhost")
    monkeypatch.setenv("OLLAMA_PORT", "11434")
    assert cli.list_models() == ["local-model"]


def test_list_models_empty_on_failure(client, monkeypatch):
    cli, session = client
    session.responses[("GET", "http://localhost:8000/models")] = FakeResponse(500)
    session.responses[("GET", "http://localhost:11434/api/tags")] = FakeResponse(500)
    monkeypatch.setenv("OLLAMA_HOST", "localhost")
    monkeypatch.setenv("OLLAMA_PORT", "11434")
    assert cli.list_models() == []


def test_client_default_base_url_from_env(monkeypatch):
    from AI.consult import OllamaClient

    monkeypatch.setenv("API_PORT", "8080")
    monkeypatch.delenv("OVERWATCH_API_URL", raising=False)
    cli = OllamaClient()
    assert cli.base_url == "http://localhost:8080"


def test_client_overwatch_url_preferred(monkeypatch):
    from AI.consult import OllamaClient

    monkeypatch.setenv("OVERWATCH_API_URL", "http://remote:9000/")
    cli = OllamaClient()
    assert cli.base_url == "http://remote:9000"


def test_client_strips_trailing_slash():
    from AI.consult import OllamaClient

    cli = OllamaClient("http://host:8000/")
    assert cli.base_url == "http://host:8000"


def test_chat_builds_payload(client):
    from AI.consult import OllamaClient

    cli, session = client
    session.responses[("POST", "http://localhost:8000/generate")] = FakeResponse(
        200, json_data={"response": "hi"})
    result = cli.chat("llama3", "hello")
    assert result == {"response": "hi"}
    method, url, kwargs = session.calls[0]
    assert kwargs["json"]["model"] == "llama3"
    assert kwargs["json"]["prompt"] == "hello"
