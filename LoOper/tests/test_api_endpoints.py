"""Unit tests for the AI API gateway endpoints via FastAPI TestClient.

The gateway's outbound requests to Ollama are faked by patching the
module-level ``requests`` object; model directories are faked through
``AI.config_loader.get_models_dir``.
"""

import sys

import pytest

from AI import api as api_mod
from conftest import FakeResponse, FakeSession, build_fake_requests_module


def _has_testclient():
    try:
        from fastapi.testclient import TestClient  # noqa: F401
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _has_testclient(),
    reason="fastapi TestClient requires httpx",
)


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    with TestClient(api_mod.app) as c:
        yield c


def test_root_endpoint(client):
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["message"] == "Ollama API Gateway"
    assert body["server_info"]["host"] == "localhost"
    assert body["server_info"]["port"] == "8000"


def test_health_endpoint_ollama_down(client, monkeypatch):
    mod = build_fake_requests_module()

    def down(method, url, **kwargs):
        raise mod.exceptions.ConnectionError("connection refused")

    mod.get = lambda url, **kw: down("GET", url, **kw)  # requests.get(url, ...)
    monkeypatch.setitem(sys.modules, "requests", mod)
    monkeypatch.setattr(api_mod, "requests", mod)
    monkeypatch.setattr(api_mod, "get_ollama_base_url", lambda: "http://127.0.0.1:11434")
    monkeypatch.setattr(api_mod, "bootstrap_required_models", lambda: None)
    # The health endpoint raises HTTPException(503) when Ollama is unreachable
    resp = client.get("/health")
    assert resp.status_code == 503


def test_health_endpoint_ollama_up(client, monkeypatch):
    session = FakeSession({
        ("GET", "http://127.0.0.1:11434/api/tags"): FakeResponse(
            200, json_data={"models": [{"name": "llama3"}]}),
    })
    mod = build_fake_requests_module(session)
    monkeypatch.setitem(sys.modules, "requests", mod)
    monkeypatch.setattr(api_mod, "requests", mod)
    monkeypatch.setattr(api_mod, "get_ollama_base_url", lambda: "http://127.0.0.1:11434")
    resp = client.get("/health")
    body = resp.json()
    assert body["ollama_server"] == "running"
    assert body["status"] == "healthy"


def test_models_endpoint(client, monkeypatch):
    session = FakeSession({
        ("GET", "http://127.0.0.1:11434/api/tags"): FakeResponse(
            200, json_data={"models": [
                {"name": "llama3", "size": 100, "modified_at": "x",
                 "details": {"parameter_size": "8B", "quantization_level": "Q4"}},
                {"name": "phi3"},
            ]}),
    })
    mod = build_fake_requests_module(session)
    monkeypatch.setitem(sys.modules, "requests", mod)
    monkeypatch.setattr(api_mod, "requests", mod)
    monkeypatch.setattr(api_mod, "get_ollama_base_url", lambda: "http://127.0.0.1:11434")
    resp = client.get("/models")
    assert resp.status_code == 200
    models = resp.json()
    assert isinstance(models, list)
    assert models[0]["name"] == "llama3"


def test_llamacpp_models_endpoint(client, monkeypatch, tmp_path):
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    (models_dir / "SmolLM3-Q4_K_M.gguf").write_bytes(b"GGUF")
    (models_dir / "not-a-model.txt").write_text("x", encoding="utf-8")
    (models_dir / "mmproj.bin").write_bytes(b"x")

    import AI.config_loader as cl
    monkeypatch.setattr(cl, "get_models_dir", lambda: str(models_dir))
    resp = client.get("/llamacpp/models")
    assert resp.status_code == 200
    body = resp.json()
    names = [m["name"] for m in body["models"]] if isinstance(body, dict) else body
    assert any("SmolLM3-Q4_K_M" in n for n in names)
    assert not any("not-a-model" in n for n in names)


def test_chat_endpoint_requires_model(client, monkeypatch):
    session = FakeSession({
        ("GET", "http://127.0.0.1:11434/api/ps"): FakeResponse(200, json_data={"models": []}),
        ("POST", "http://127.0.0.1:11434/api/generate"): FakeResponse(
            200, json_data={"response": "answer", "done": True}),
    })
    mod = build_fake_requests_module(session)
    monkeypatch.setitem(sys.modules, "requests", mod)
    monkeypatch.setattr(api_mod, "requests", mod)
    monkeypatch.setattr(api_mod, "get_ollama_base_url", lambda: "http://127.0.0.1:11434")
    resp = client.post("/chat", json={"model": "llama3", "prompt": "hi"})
    assert resp.status_code == 200
    assert resp.json()["response"] == "answer"


def test_chat_endpoint_without_prompt_ok(client, monkeypatch):
    # prompt is Optional — the endpoint handles a missing prompt gracefully
    session = FakeSession({
        ("GET", "http://127.0.0.1:11434/api/ps"): FakeResponse(200, json_data={"models": []}),
        ("POST", "http://127.0.0.1:11434/api/generate"): FakeResponse(
            200, json_data={"response": "", "done": True}),
    })
    mod = build_fake_requests_module(session)
    monkeypatch.setitem(sys.modules, "requests", mod)
    monkeypatch.setattr(api_mod, "requests", mod)
    monkeypatch.setattr(api_mod, "get_ollama_base_url", lambda: "http://127.0.0.1:11434")
    resp = client.post("/chat", json={"model": "llama3"})
    assert resp.status_code == 200


def test_embeddings_endpoint(client, monkeypatch):
    session = FakeSession({
        ("GET", "http://127.0.0.1:11434/api/ps"): FakeResponse(200, json_data={"models": []}),
        ("POST", "http://127.0.0.1:11434/api/embed"): FakeResponse(
            200, json_data={"embeddings": [[0.1, 0.2], [0.3, 0.4]]},
            text='{"embeddings": [[0.1, 0.2], [0.3, 0.4]]}'),
    })
    mod = build_fake_requests_module(session)
    monkeypatch.setitem(sys.modules, "requests", mod)
    monkeypatch.setattr(api_mod, "requests", mod)
    monkeypatch.setattr(api_mod, "get_ollama_base_url", lambda: "http://127.0.0.1:11434")
    resp = client.post("/embeddings", json={"model": "nomic-embed-text", "inputs": ["a", "b"]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["embeddings"] == [[0.1, 0.2], [0.3, 0.4]]


def test_cleanup_endpoint(client, monkeypatch):
    # The endpoint delegates to _cleanup_all_exact (process-level cleanup);
    # mock it so the test never touches real processes.
    calls = []

    def fake_cleanup(max_passes=3, wait_s=0.5):
        calls.append((max_passes, wait_s))
        return True

    monkeypatch.setattr(api_mod, "_cleanup_all_exact", fake_cleanup)
    resp = client.post("/cleanup", json={"all": True, "retries": 5, "wait": 0.1})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert calls == [(5, 0.1)]
