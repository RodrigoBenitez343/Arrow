"""Unit tests for AI/api.py pure helpers (no HTTP server startup):
response sanitisation, base-URL resolution, image processing, vision-model
detection, model bootstrap helpers, and runner cleanup.
"""

import base64
import sys

import pytest

from AI import api
from conftest import FakeResponse, FakeSession, build_fake_requests_module


# ---------------------------------------------------------------------------
# _sanitize_llamacpp_response
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text,expected", [
    ("hello<|im_end|>", "hello"),
    ("hello</s>", "hello"),
    ("plain", "plain"),
    ("", ""),
])
def test_sanitize_llamacpp_response(text, expected):
    assert api._sanitize_llamacpp_response(text) == expected


# ---------------------------------------------------------------------------
# get_ollama_base_url
# ---------------------------------------------------------------------------


def test_get_ollama_base_url_env(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "10.0.0.2")
    monkeypatch.setenv("OLLAMA_PORT", "11435")
    assert api.get_ollama_base_url() == "http://10.0.0.2:11435"


def test_get_ollama_base_url_default(monkeypatch):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.delenv("OLLAMA_PORT", raising=False)
    assert api.get_ollama_base_url() == "http://127.0.0.1:11434"


# ---------------------------------------------------------------------------
# process_images
# ---------------------------------------------------------------------------


def test_process_images_file_path(tmp_path):
    img = tmp_path / "a.png"
    img.write_bytes(b"\x89PNGdata")
    out = api.process_images([str(img)])
    assert out == [base64.b64encode(b"\x89PNGdata").decode()]


def test_process_images_data_url():
    raw = base64.b64encode(b"img").decode()
    out = api.process_images([f"data:image/png;base64,{raw}"])
    assert out == [raw]


def test_process_images_raw_base64():
    raw = base64.b64encode(b"img").decode()
    out = api.process_images([raw])
    assert out == [raw]


def test_process_images_missing_file_passes_through():
    # A non-existent path is NOT a file, so it is treated as raw base64
    # (permissive by design — the upstream server decides).
    out = api.process_images(["C:\\does\\not\\exist.png"])
    assert out == ["C:\\does\\not\\exist.png"]


# ---------------------------------------------------------------------------
# Vision model detection
# ---------------------------------------------------------------------------


def test_is_vision_model_static():
    assert api._is_vision_model_static("llava:13b") is True
    assert api._is_vision_model_static("minicpm-v:latest") is True
    assert api._is_vision_model_static("llama3.2:1b") is False


def test_is_vision_model_cached(monkeypatch):
    api.VISION_CAPABLE_CACHE["cached_model"] = True
    try:
        assert api.is_vision_model("cached_model") is True
    finally:
        api.VISION_CAPABLE_CACHE.pop("cached_model", None)


def test_is_vision_model_uses_show_api(monkeypatch):
    session = FakeSession({
        ("GET", "http://127.0.0.1:11434/api/show"): FakeResponse(
            200, json_data={"template": "<image>..."}),
    })
    mod = build_fake_requests_module(session)
    monkeypatch.setitem(sys.modules, "requests", mod)
    monkeypatch.setattr(api, "requests", mod)  # api.py holds its own reference
    # Import-time env syncing may have changed OLLAMA_HOST — pin the base URL
    monkeypatch.setattr(api, "get_ollama_base_url", lambda: "http://127.0.0.1:11434")
    monkeypatch.setattr(api, "VISION_CAPABLE_CACHE", {})
    assert api.is_vision_model("some-model") is True


def test_is_vision_model_falls_back_static_on_error(monkeypatch):
    class Down:
        def get(self, *a, **k):
            raise RuntimeError("no server")

        def post(self, *a, **k):
            raise RuntimeError("no server")

    monkeypatch.setattr(api, "requests", Down())
    monkeypatch.setattr(api, "VISION_CAPABLE_CACHE", {})
    assert api.is_vision_model("llava") is True
    assert api.is_vision_model("unknown-model") is False


# ---------------------------------------------------------------------------
# Model bootstrap helpers
# ---------------------------------------------------------------------------


def test_parse_required_models_env(monkeypatch):
    monkeypatch.delenv("TEST_MODELS", raising=False)
    assert api._parse_required_models_env("TEST_MODELS", ["a"]) == ["a"]
    monkeypatch.setenv("TEST_MODELS", " a , b , ")
    assert api._parse_required_models_env("TEST_MODELS", ["a"]) == ["a", "b"]


def test_collect_required_models_dedupes():
    api.HARDCODED_REQUIRED_EMBED_MODELS = ["nomic-embed-text"]
    api.HARDCODED_REQUIRED_LLM_MODELS = ["llama3", "LLAMA3"]
    api.HARDCODED_REQUIRED_VISION_MODELS = ["minicpm-v"]
    try:
        models = api._collect_required_models()
        # Dedup is case-insensitive: "llama3" and "LLAMA3" collapse
        assert models == ["nomic-embed-text", "llama3", "minicpm-v"]
        assert len(models) == 3
    finally:
        api.HARDCODED_REQUIRED_EMBED_MODELS = []
        api.HARDCODED_REQUIRED_LLM_MODELS = []
        api.HARDCODED_REQUIRED_VISION_MODELS = []


def test_refresh_missing(monkeypatch):
    monkeypatch.setattr(api, "_list_ollama_models", lambda: {"llama3", "phi3"})
    assert api._refresh_missing(["llama3", "ghost"]) == ["ghost"]


def test_ensure_model_present_present(monkeypatch):
    pulled = []
    monkeypatch.setattr(api, "_list_ollama_models", lambda: {"llama3:8b"})
    monkeypatch.setattr(api, "_pull_ollama_model",
                        lambda name: pulled.append(name) or True)
    api.ensure_model_present("llama3")
    assert pulled == []


def test_ensure_model_present_missing_pulls(monkeypatch):
    pulled = []
    monkeypatch.setattr(api, "_list_ollama_models", lambda: set())
    monkeypatch.setattr(api, "_pull_ollama_model",
                        lambda name: pulled.append(name) or True)
    api.ensure_model_present("llama3")
    assert pulled == ["llama3"]


def test_bootstrap_required_models_no_required(monkeypatch):
    monkeypatch.setattr(api, "_collect_required_models", lambda: [])
    api.bootstrap_required_models()
    assert api.BOOTSTRAP_STATE["completed"] is True


def test_bootstrap_required_models_pulls_missing(monkeypatch):
    monkeypatch.setattr(api, "_collect_required_models", lambda: ["m1", "m2"])
    monkeypatch.setattr(api, "ensure_model_present", lambda name: None)
    monkeypatch.setattr(api, "_refresh_missing", lambda required: [] if required == ["m1", "m2"] else [])
    api.bootstrap_required_models()
    assert api.BOOTSTRAP_STATE["in_progress"] is False
    assert api.BOOTSTRAP_STATE["completed"] is True


def test_pull_ollama_model_success(monkeypatch):
    session = FakeSession({
        ("POST", "http://127.0.0.1:11434/api/pull"): FakeResponse(
            200, json_data={}, iter_lines=[b'{"status":"pulling manifest"}']),
    })
    monkeypatch.setattr(api, "requests", session)
    monkeypatch.setattr(api, "get_ollama_base_url", lambda: "http://127.0.0.1:11434")
    monkeypatch.setattr(api, "_list_ollama_models", lambda: {"target-model"})
    assert api._pull_ollama_model("target-model") is True


def test_pull_ollama_model_failure(monkeypatch):
    class Down:
        def post(self, *a, **k):
            return FakeResponse(500)

        def get(self, *a, **k):
            return FakeResponse(500)

    monkeypatch.setattr(api, "requests", Down())
    monkeypatch.setattr(api, "get_ollama_base_url", lambda: "http://x")
    assert api._pull_ollama_model("target-model") is False


# ---------------------------------------------------------------------------
# _cleanup_ollama_runners
# ---------------------------------------------------------------------------


def test_cleanup_runners_skips_target(monkeypatch):
    session = FakeSession({
        ("GET", "http://127.0.0.1:11434/api/ps"): FakeResponse(
            200, json_data={"models": [
                {"id": 1, "model": "target"},
                {"id": 2, "model": "other"},
            ]}),
    })
    monkeypatch.setattr(api, "requests", session)
    monkeypatch.setattr(api, "get_ollama_base_url", lambda: "http://127.0.0.1:11434")
    api._cleanup_ollama_runners("target")
    posts = [c for c in session.calls if c[0] == "POST"]
    assert len(posts) == 1
    assert posts[0][2]["json"]["model"] == "other"


def test_cleanup_runners_empty_models(monkeypatch):
    session = FakeSession({
        ("GET", "http://127.0.0.1:11434/api/ps"): FakeResponse(
            200, json_data={"models": []}),
    })
    monkeypatch.setattr(api, "requests", session)
    monkeypatch.setattr(api, "get_ollama_base_url", lambda: "http://127.0.0.1:11434")
    api._cleanup_ollama_runners(None)  # no crash
