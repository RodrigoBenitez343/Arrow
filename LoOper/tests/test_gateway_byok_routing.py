"""Gateway provider routing — resolution order and dispatch across routes.

Run:  python -m pytest LoOper/tests/test_gateway_byok_routing.py
"""

import asyncio
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from fastapi import BackgroundTasks  # noqa: E402

from AI import api  # noqa: E402
from AI import byok  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


def _role_chat(provider="openai", model="gpt-4o"):
    """Patch byok.load_settings to report a chat role (+ default fallbacks)."""
    settings = byok.parse_settings({
        "providers": {"openai": {"model": model}},
        "roles": {"chat": {"provider": provider, "model": model}},
    })
    return settings


# --------------------------------------------------------------------------- order


def test_resolve_provider_order(monkeypatch):
    settings = _role_chat()
    monkeypatch.setattr(byok, "load_settings", lambda: settings)

    # 1. explicit wins over everything
    assert api._resolve_provider("groq", "openai:gpt-4o", "chat") == \
        ("groq", "gpt-4o")
    # 2. model prefix
    assert api._resolve_provider(None, "openai:gpt-4o", "chat") == \
        ("openai", "gpt-4o")
    # 3. role fills provider AND model when nothing is named
    assert api._resolve_provider(None, "", "chat") == ("openai", "gpt-4o")
    # a prefix names its own provider+model
    assert api._resolve_provider(None, "openai:other", "chat") == \
        ("openai", "other")
    # ...and a PASSED model is never hijacked by the role: a local GGUF
    # path or an Ollama tag already picked where this call runs.
    assert api._resolve_provider(None, "llama3.2:1b", "chat") == \
        ("", "llama3.2:1b")
    assert api._resolve_provider(None, r"D:\models\qwen.gguf", "chat") == \
        ("", r"D:\models\qwen.gguf")


def test_local_model_is_never_hijacked_by_the_chat_role(monkeypatch):
    """The 2026-10-09 bug: an orchestrator set to a local llama.cpp model
    must run locally even when the chat role points at a cloud provider
    AND the role has no model of its own (its model comes from the
    provider's stored model only when the call names nothing)."""
    settings = byok.parse_settings({
        "providers": {"deepseek": {"api_key": "k",
                                     "model": "deepseek-flash"}},
        "roles": {"chat": {"provider": "deepseek", "model": ""}},
    })
    monkeypatch.setattr(byok, "load_settings", lambda: settings)
    gguf = r"D:\LoOperV2\LoOper\AI\models\Qwen3.6-35B-A3B-UD-Q2_K_XL.gguf"
    assert api._resolve_provider(None, gguf, "chat") == ("", gguf)
    assert api._resolve_provider(None, "SmolLM3-Q4_K_M.gguf", "chat") == \
        ("", "SmolLM3-Q4_K_M.gguf")
    # Nothing named at all -> the role's fallback fills provider+model
    # (the model resolves from the provider's stored model).
    assert api._resolve_provider(None, "", "chat") == \
        ("deepseek", "deepseek-flash")


def test_resolve_provider_prefix_safety(monkeypatch):
    monkeypatch.setattr(byok, "load_settings", byok.default_settings)
    # Ollama tags and Windows paths must never be treated as prefixes.
    assert api._resolve_provider(None, "llama3.2:1b", "chat") == \
        ("", "llama3.2:1b")
    assert api._resolve_provider(None, r"D:\models\x.gguf", "chat") == \
        ("", r"D:\models\x.gguf")
    assert api._resolve_provider(None, "", "chat") == ("", "")


def test_llamacpp_prefix_is_resolvable(monkeypatch):
    monkeypatch.setattr(byok, "load_settings", byok.default_settings)
    assert api._resolve_provider(None, "llamacpp:SmolLM3.gguf", "chat") == \
        ("llamacpp", "SmolLM3.gguf")


def test_role_key_is_vision_for_image_requests():
    plain = api.ChatRequest(model="m", prompt="x")
    vision = api.ChatRequest(model="m", prompt="x", images=["AAAA"])
    assert api._role_key_for(plain) == "chat"
    assert api._role_key_for(vision) == "vision"


def test_messages_to_prompt_flattens():
    instructions, prompt = api._messages_to_prompt(
        [{"role": "system", "content": "be brief"},
         {"role": "user", "content": "hello"},
         {"role": "assistant", "content": "hi"},
         {"role": "user", "content": [
             {"type": "text", "text": "again"}]}],
        "")
    assert instructions == "be brief"
    assert "hello" in prompt and "again" in prompt


# --------------------------------------------------------------------------- dispatch


def test_byok_generate_returns_legacy_shape(monkeypatch):
    monkeypatch.setattr(byok, "generate_text",
                        lambda *a, **k: "cloud answer")
    out = api._byok_generate(
        api.ChatRequest(model="gpt-4o", prompt="hi"), "openai", "gpt-4o")
    assert out["response"] == "cloud answer"
    assert out["done"] is True and out["model"]


def test_byok_generate_pool_and_unknown_are_400(monkeypatch):
    with pytest.raises(api.HTTPException) as err:
        api._byok_generate(api.ChatRequest(model="m", prompt="x"),
                           "pool", "m")
    assert err.value.status_code == 400
    assert "coming soon" in err.value.detail.lower()

    with pytest.raises(api.HTTPException) as err:
        api._byok_generate(api.ChatRequest(model="m", prompt="x"),
                           "nope", "m")
    assert err.value.status_code == 400


def test_chat_route_dispatches_to_role_provider(monkeypatch):
    monkeypatch.setattr(byok, "load_settings", _role_chat)
    monkeypatch.setattr(byok, "generate_text",
                        lambda *a, **k: "cloud answer")
    out = _run(api.chat_with_model(
        api.ChatRequest(model="", prompt="hi"), BackgroundTasks()))
    assert out["response"] == "cloud answer"


def test_llamacpp_route_reroutes_to_byok_role(monkeypatch):
    """A node hitting /llamacpp/generate follows the chat role when set."""
    monkeypatch.setattr(byok, "load_settings", _role_chat)
    monkeypatch.setattr(byok, "generate_text", lambda *a, **k: "rerouted")
    out = _run(api._llamacpp_generate_impl(
        api.ChatRequest(model="", prompt="hi"), BackgroundTasks(),
        unload_after=True))
    assert out["response"] == "rerouted"


def test_llamacpp_route_stays_local_without_roles(monkeypatch):
    """No roles → a GGUF request keeps its legacy engine route."""
    monkeypatch.setattr(byok, "load_settings", byok.default_settings)
    provider, model = api._resolve_provider(None, "SmolLM3.gguf", "chat")
    assert (provider, model) == ("", "SmolLM3.gguf")


# --------------------------------------------------------------------------- embeddings


def test_embeddings_role_llamacpp_uses_local_server(monkeypatch):
    calls = {}

    def _fake_local(inputs):
        calls["inputs"] = inputs
        return [[1.0, 2.0], [3.0, 4.0]]

    import AI.embedding_server as emb
    monkeypatch.setattr(emb, "embed_texts", _fake_local)
    out = _run(api._remote_embeddings(
        api.EmbeddingsRequest(model="", inputs=["a", "b"]), "llamacpp", ""))
    assert out == {"embeddings": [[1.0, 2.0], [3.0, 4.0]]}
    assert calls["inputs"] == ["a", "b"]


def test_embeddings_local_shortfall_is_503(monkeypatch):
    import AI.embedding_server as emb
    monkeypatch.setattr(emb, "embed_texts", lambda inputs: [[1.0]])
    with pytest.raises(api.HTTPException) as err:
        _run(api._remote_embeddings(
            api.EmbeddingsRequest(model="", inputs=["a", "b"]),
            "llamacpp", ""))
    assert err.value.status_code == 503


def test_embeddings_byok_provider(monkeypatch):
    monkeypatch.setattr(byok, "embed_texts",
                        lambda *a, **k: [[0.5, 0.25]])
    out = _run(api._remote_embeddings(
        api.EmbeddingsRequest(model="text-embedding-3-small", inputs=["a"]),
        "openai", "text-embedding-3-small"))
    assert out == {"embeddings": [[0.5, 0.25]]}

    with pytest.raises(api.HTTPException) as err:
        _run(api._remote_embeddings(
            api.EmbeddingsRequest(model="m", inputs=["a"]), "pool", "m"))
    assert err.value.status_code == 400


def test_embeddings_route_dispatches_by_role(monkeypatch):
    """The /embeddings route consults the embeddings role end-to-end."""
    settings = byok.parse_settings({
        "roles": {"embeddings": {"provider": "llamacpp"}}})
    monkeypatch.setattr(byok, "load_settings", lambda: settings)
    import AI.embedding_server as emb
    monkeypatch.setattr(emb, "embed_texts", lambda inputs: [[9.0]])
    out = _run(api.get_embeddings(
        api.EmbeddingsRequest(model="", inputs=["x"]), BackgroundTasks()))
    assert out == {"embeddings": [[9.0]]}


def test_embeddings_route_legacy_untouched(monkeypatch):
    """No provider, no role → the classic Ollama payload is still built."""
    monkeypatch.setattr(byok, "load_settings", byok.default_settings)
    provider, model = api._resolve_provider(None, "nomic-embed-text", "embeddings")
    assert (provider, model) == ("", "nomic-embed-text")


# --------------------------------------------------------------------------- /models


def test_models_endpoint_merges_byok_entries(monkeypatch):
    """GUI pickers read /models: cloud models arrive as provider:model."""
    class _Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"models": [{"name": "llama3.2:1b"}]}

    monkeypatch.setattr(api.requests, "get", lambda *a, **k: _Resp())
    monkeypatch.setattr(byok, "prefixed_model_names",
                        lambda settings=None: ["openai:gpt-4o-mini"])
    out = _run(api.list_models())
    assert [m.name for m in out] == ["llama3.2:1b", "openai:gpt-4o-mini"]


def test_models_endpoint_survives_ollama_down_with_byok(monkeypatch):
    import requests as rq

    def _boom(*a, **k):
        raise rq.exceptions.ConnectionError("refused")

    monkeypatch.setattr(api.requests, "get", _boom)
    monkeypatch.setattr(byok, "prefixed_model_names",
                        lambda settings=None: ["openai:gpt-4o-mini"])
    out = _run(api.list_models())
    assert [m.name for m in out] == ["openai:gpt-4o-mini"]


def test_models_endpoint_503_when_nothing_available(monkeypatch):
    import requests as rq

    def _boom(*a, **k):
        raise rq.exceptions.ConnectionError("refused")

    monkeypatch.setattr(api.requests, "get", _boom)
    monkeypatch.setattr(byok, "prefixed_model_names",
                        lambda settings=None: [])
    with pytest.raises(api.HTTPException) as err:
        _run(api.list_models())
    assert err.value.status_code == 503


# --------------------------------------------------------------------------- /stop


def test_stop_endpoint_is_provider_agnostic(monkeypatch):
    """POST /stop ends generation for llama.cpp AND Ollama + burst engines —
    a stop is a stop for all providers, never Ollama-only."""
    calls = {"engine": 0}

    class _Eng:
        def stop_server(self):
            calls["engine"] += 1

    monkeypatch.setattr(api, "LLAMACPP_ENGINE", _Eng())
    monkeypatch.setattr(api, "_list_http", lambda: [{"name": "llama3", "id": None}])
    monkeypatch.setattr(api, "_list_cli", lambda: [{"name": "gemma"}])
    http_stopped, cli_stopped = [], []
    monkeypatch.setattr(api, "_stop_http", lambda n, i: http_stopped.append(n))
    monkeypatch.setattr(api, "_stop_cli", lambda n: cli_stopped.append(n))

    import AI.embedding_server as emb
    import AI.laya_client as lc
    released = []
    monkeypatch.setattr(emb, "release", lambda wait=0.0: released.append("embed"))
    monkeypatch.setattr(lc, "release", lambda wait=0.0: released.append("laya"))

    out = api.stop_all_inference()
    assert out["llamacpp"] == "stopped"
    assert out["ollama"] is True
    assert calls["engine"] == 1
    assert http_stopped == ["llama3"]
    assert cli_stopped == ["gemma"]
    assert released == ["embed", "laya"]
