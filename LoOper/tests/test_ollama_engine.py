"""Unit tests for AI/ollama_engine.py (async engine with faked aiohttp session).

The engine's HTTP session is replaced with a recording fake so no network
access is ever attempted; async tests run via pytest-asyncio (auto mode).
"""

import json
from types import SimpleNamespace

import pytest

from AI.ollama_engine import OllamaEngine, ModelConfig


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeResp:
    def __init__(self, status=200, json_data=None, lines=None, text=""):
        self.status = status
        self._json = json_data
        self.lines = lines or []
        self._text = text  # stored separately: 'text' must stay a method
        self.content = _AsyncLines(self.lines)

    async def json(self):
        return self._json

    async def text(self):
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _AsyncLines:
    def __init__(self, lines):
        self._lines = list(lines)

    def __aiter__(self):
        async def gen():
            for line in self._lines:
                yield line
        return gen()


class FakeSession:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    def _dispatch(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if callable(self.responses):
            return self.responses(method, path, **kwargs)
        for (m, p), resp in self.responses.items():
            if m == method and p in path:
                return resp
        return FakeResp(404, json_data={})

    def get(self, path, **kwargs):
        # NOTE: the engine always uses ``async with session.get(...)`` — the
        # session method must return an async context manager directly (not a
        # coroutine), which FakeResp provides via __aenter__/__aexit__.
        return self._dispatch("GET", path, **kwargs)

    def post(self, path, **kwargs):
        return self._dispatch("POST", path, **kwargs)

    async def close(self):
        pass


def make_settings(**overrides):
    from dataclasses import dataclass, field

    @dataclass
    class Settings:
        base_url: str = "http://localhost:11434"
        context_length: int = 4096
        temperature: float = 0.7
        top_p: float = 0.9
        top_k: int = 40
        repeat_penalty: float = 1.1
        timeout: int = 300
        default_models: list = field(default_factory=list)

    return Settings(**overrides)


def make_engine(settings=None, session=None):
    engine = OllamaEngine(settings or make_settings())
    engine._session = session or FakeSession()
    engine._initialized = True
    return engine


# ---------------------------------------------------------------------------
# _messages_to_prompt
# ---------------------------------------------------------------------------


def test_messages_to_prompt():
    engine = make_engine()
    prompt = engine._messages_to_prompt([
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "usr"},
        {"role": "assistant", "content": "ast"},
        {"role": "unknown", "content": "ignored"},
    ])
    assert "System: sys" in prompt
    assert "User: usr" in prompt
    assert "Assistant: ast" in prompt
    assert "ignored" not in prompt
    assert prompt.endswith("Assistant:")


# ---------------------------------------------------------------------------
# Embedding normalization
# ---------------------------------------------------------------------------


async def test_normalize_embeddings_batch_format():
    engine = make_engine()
    data = {"embeddings": [[1.0, 2.0], [3.0, 4.0]]}
    result = await engine._normalize_embeddings_response(2, data, ["a", "b"], "m")
    assert result == [[1.0, 2.0], [3.0, 4.0]]


async def test_normalize_embeddings_single_vector(monkeypatch):
    engine = make_engine()
    data = {"embedding": [1.0, 2.0]}
    result = await engine._normalize_embeddings_response(1, data, ["a"], "m")
    assert result == [[1.0, 2.0]]


async def test_normalize_embeddings_flat_list_single_input(monkeypatch):
    engine = make_engine()
    result = await engine._normalize_embeddings_response(1, [1.0, 2.0], ["a"], "m")
    assert result == [[1.0, 2.0]]


async def test_normalize_embeddings_list_of_lists():
    engine = make_engine()
    result = await engine._normalize_embeddings_response(2, [[1.0], [2.0]], ["a", "b"], "m")
    assert result == [[1.0], [2.0]]


async def test_normalize_embeddings_multi_input_single_vector_falls_back(monkeypatch):
    engine = make_engine()
    session = engine._session
    session.responses[("POST", "/api/embed")] = FakeResp(200, json_data={"embedding": [1.0]})

    async def fake_per_input(texts, model):
        return [[float(i)] for i in range(len(texts))]

    monkeypatch.setattr(engine, "_query_embeddings_per_input", fake_per_input)
    result = await engine._normalize_embeddings_response(2, {"embedding": [1.0]}, ["a", "b"], "m")
    assert len(result) == 2


async def test_normalize_embeddings_invalid_format_raises():
    engine = make_engine()
    with pytest.raises(RuntimeError):
        await engine._normalize_embeddings_response(1, {"unexpected": 1}, ["a"], "m")


# ---------------------------------------------------------------------------
# embeddings() flow
# ---------------------------------------------------------------------------


async def test_embeddings_empty_inputs_returns_empty(monkeypatch):
    engine = make_engine()
    engine._session.responses[("GET", "/api/ps")] = FakeResp(200, json_data={"models": []})
    assert await engine.embeddings("m", []) == []
    assert await engine.embeddings("m", ["", "  "]) == []


async def test_embeddings_success(monkeypatch):
    engine = make_engine()
    session = engine._session
    session.responses[("GET", "/api/ps")] = FakeResp(200, json_data={"models": []})
    session.responses[("POST", "/api/embed")] = FakeResp(
        200, json_data={"embeddings": [[1.0], [2.0]]},
        text=json.dumps({"embeddings": [[1.0], [2.0]]}))
    result = await engine.embeddings("m", ["a", "b"])
    assert result == [[1.0], [2.0]]
    # keep_alive must be 0 for ephemeral-style calls
    payload = session.calls[-1][2]["json"]
    assert payload["keep_alive"] == 0


async def test_embeddings_legacy_endpoint_fallback(monkeypatch):
    engine = make_engine()
    session = engine._session
    # NOTE: key order matters — the fake session matches by substring, and
    # "/api/embed" is a prefix of "/api/embeddings".
    session.responses = {
        ("GET", "/api/ps"): FakeResp(200, json_data={"models": []}),
        ("POST", "/api/embeddings"): FakeResp(200, json_data={"embedding": [1.0, 2.0]}),
        ("POST", "/api/embed"): FakeResp(404, json_data={}),
    }
    result = await engine.embeddings("m", ["a"])
    assert result == [[1.0, 2.0]]


async def test_embeddings_error_raises(monkeypatch):
    engine = make_engine()
    session = engine._session
    session.responses[("GET", "/api/ps")] = FakeResp(200, json_data={"models": []})
    session.responses[("POST", "/api/embed")] = FakeResp(500, json_data={})
    with pytest.raises(RuntimeError):
        await engine.embeddings("m", ["a"])


# ---------------------------------------------------------------------------
# load / unload / generate
# ---------------------------------------------------------------------------


async def test_load_model_already_loaded(monkeypatch):
    engine = make_engine()
    engine.loaded_models["m"] = ModelConfig(name="m", model_path="m")
    assert await engine.load_model("m") is True
    assert engine._session.calls == []  # no network


async def test_unload_model(monkeypatch):
    engine = make_engine()
    engine.loaded_models["m"] = ModelConfig(name="m", model_path="m")
    assert await engine.unload_model("m") is True
    assert await engine.unload_model("m") is False  # already gone


async def test_initialize_neither_preloads_nor_pulls_models(monkeypatch):
    """Engine startup must NOT load models and must NOT pull them.  The lifecycle
    is strictly per node (load -> use -> unload), so a chain that never reaches
    an LLM node must not hold a model in memory/VRAM - nor kick off a multi-GB
    download for models the run may never touch.  Covers the gateway's live
    engine AND its twin."""
    import importlib

    for module_name in ("AI.OverWatch.api.ollama_engine", "AI.ollama_engine"):
        engine_mod = importlib.import_module(module_name)
        monkeypatch.setattr(engine_mod.aiohttp, "ClientSession",
                            lambda *a, **k: FakeSession())
        engine = engine_mod.OllamaEngine(
            make_settings(default_models=["llama3.2:1b", "llama3.2:3b"]))
        loaded = []
        pulled = []

        async def _noop(*args, **kwargs):
            return None

        async def fake_load(model_name, config=None):
            loaded.append(model_name)
            return True

        async def fake_pull(model_name):
            pulled.append(model_name)

        monkeypatch.setattr(engine, "_check_ollama_status", _noop)
        monkeypatch.setattr(engine, "load_model", fake_load)
        monkeypatch.setattr(engine, "_pull_model", fake_pull)

        await engine.initialize()

        assert loaded == [], f"{module_name} preloaded {loaded}"
        assert pulled == [], f"{module_name} auto-pulled {pulled}"
        assert engine._initialized is True


async def test_generate_requires_initialized():
    engine = make_engine()
    engine._initialized = False
    with pytest.raises(RuntimeError):
        await engine.generate("hi", model="m")


async def test_generate_single_conversion():
    engine = make_engine()
    session = engine._session
    session.responses[("GET", "/api/ps")] = FakeResp(200, json_data={"models": []})
    session.responses[("POST", "/api/generate")] = FakeResp(200, json_data={
        "response": "text out", "done": True,
        "prompt_eval_count": 3, "eval_count": 5,
    })
    result = await engine._generate_single({
        "model": "m", "prompt": "hi", "stream": False})
    assert result["text"] == "text out"
    assert result["finish_reason"] == "stop"
    assert result["prompt_tokens"] == 3
    assert result["completion_tokens"] == 5
    assert result["total_tokens"] == 8


async def test_generate_stream_yields_accumulated_chunks():
    engine = make_engine()
    session = engine._session
    session.responses[("GET", "/api/ps")] = FakeResp(200, json_data={"models": []})
    lines = [
        json.dumps({"response": "Hel", "done": False}).encode(),
        json.dumps({"response": "lo", "done": True, "eval_count": 2}).encode(),
    ]
    session.responses[("POST", "/api/generate")] = FakeResp(
        200, json_data=None, lines=lines)
    chunks = []
    async for chunk in engine._generate_stream({"model": "m", "stream": True}):
        chunks.append(chunk)
    assert chunks[0]["text"] == "Hel"
    assert chunks[1]["text"] == "Hello"
    assert chunks[1]["finish_reason"] == "stop"


async def test_generate_stream_error_status_raises():
    engine = make_engine()
    session = engine._session
    session.responses[("GET", "/api/ps")] = FakeResp(200, json_data={"models": []})
    session.responses[("POST", "/api/generate")] = FakeResp(500)
    with pytest.raises(RuntimeError):
        async for _ in engine._generate_stream({"model": "m", "stream": True}):
            pass


async def test_chat_completions_openai_format():
    engine = make_engine()
    session = engine._session
    session.responses[("GET", "/api/ps")] = FakeResp(200, json_data={"models": []})
    session.responses[("POST", "/api/generate")] = FakeResp(200, json_data={
        "response": "answer", "done": True,
        "prompt_eval_count": 1, "eval_count": 2,
    })
    engine.loaded_models["m"] = ModelConfig(name="m", model_path="m")
    result = await engine.chat_completions([{"role": "user", "content": "hi"}], model="m")
    assert result["object"] == "chat.completion"
    assert result["choices"][0]["message"]["content"] == "answer"
    assert result["usage"]["total_tokens"] == 3


# ---------------------------------------------------------------------------
# misc state helpers
# ---------------------------------------------------------------------------


def test_list_models_and_get_loaded():
    import asyncio
    engine = make_engine()
    engine.loaded_models["a"] = ModelConfig(name="a", model_path="a")
    engine.loaded_models["b"] = ModelConfig(name="b", model_path="b")
    assert engine.get_loaded_models() == ["a", "b"]
    listed = asyncio.run(engine.list_models())
    assert listed[0]["name"] == "a"
    assert listed[0]["loaded"] is True


def test_is_ready():
    engine = make_engine()
    assert engine.is_ready() is True
    engine._initialized = False
    assert engine.is_ready() is False


def test_get_memory_usage_shape():
    engine = make_engine()
    info = engine.get_memory_usage()
    assert "system_memory" in info
    assert "loaded_models" in info


async def test_cleanup(monkeypatch):
    engine = make_engine()
    closed = []

    async def fake_close():
        closed.append(1)

    engine._session.close = fake_close
    await engine.cleanup()
    assert closed == [1]
    assert engine._session is None
    assert engine._initialized is False
