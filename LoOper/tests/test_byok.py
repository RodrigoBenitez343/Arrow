"""BYOK provider runtime — manifest parity, settings, wires, retries, CLI.

Run:  python -m pytest LoOper/tests/test_byok.py
"""

import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from AI import byok  # noqa: E402


# --------------------------------------------------------------------------- helpers


class _Resp:
    def __init__(self, status_code=200, payload=None, text="", headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = headers or {}

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


# --------------------------------------------------------------------------- manifest


def test_manifest_covers_all_ts_providers():
    """The 15 provider ids from provider-manifest.ts are present, in order."""
    expected = [
        "anthropic", "openai", "google", "xai", "openrouter", "groq",
        "mistral", "deepseek", "deepinfra", "together", "fireworks",
        "ollama", "lm-studio", "codex-cli", "claude-cli", "pool",
    ]
    assert list(byok.PROVIDER_MANIFEST) == expected


def test_manifest_base_urls_and_auth_match_the_ts_table():
    assert byok.PROVIDER_MANIFEST["anthropic"]["base_url"] == \
        "https://api.anthropic.com/v1"
    assert byok.PROVIDER_MANIFEST["anthropic"]["auth"] == "anthropic-api-key"
    assert byok.PROVIDER_MANIFEST["openai"]["base_url"] == \
        "https://api.openai.com/v1"
    assert byok.PROVIDER_MANIFEST["google"]["base_url"] == \
        "https://generativelanguage.googleapis.com/v1beta/openai"
    assert byok.PROVIDER_MANIFEST["deepinfra"]["auth"] == "bearer"
    assert byok.PROVIDER_MANIFEST["openrouter"]["model_normalization"] == \
        "name-fallback"
    assert byok.PROVIDER_MANIFEST["together"]["model_listing_direct"] is True
    assert byok.PROVIDER_MANIFEST["pool"]["coming_soon"] is True


# --------------------------------------------------------------------------- settings


def test_settings_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(byok, "settings_path",
                        lambda: str(tmp_path / "byok.json"))
    assert byok.load_settings() == byok.default_settings()
    saved = byok.save_settings({
        "selected_provider": "openai",
        "providers": {"openai": {"api_key": "sk-1", "model": "gpt-4o-mini"}},
        "roles": {"chat": {"provider": "openai", "model": "gpt-4o-mini"}},
    })
    assert saved["selected_provider"] == "openai"
    back = byok.load_settings()
    assert back["providers"]["openai"]["api_key"] == "sk-1"
    assert back["roles"]["chat"] == \
        {"provider": "openai", "model": "gpt-4o-mini"}


def test_settings_parse_sanitizes_garbage(tmp_path, monkeypatch):
    path = tmp_path / "byok.json"
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(byok, "settings_path", lambda: str(path))
    assert byok.load_settings() == byok.default_settings()  # never raises

    clean = byok.parse_settings({
        "selected_provider": "bogus",
        "providers": {"bogus": {"api_key": "x"}, "ollama": {"model": 7}},
        "roles": {"chat": {"provider": "bogus"}, "nope": {}},
        "verification": {"ollama": {"ok": True, "tested_at": "t"}},
    })
    assert clean["selected_provider"] == "ollama"       # unknown -> default
    assert list(clean["providers"]) == ["ollama"]       # bogus dropped
    assert clean["providers"]["ollama"]["model"] == ""  # 7 -> ""
    assert clean["roles"]["chat"] == {"provider": "", "model": ""}
    assert clean["verification"]["ollama"]["ok"] is True


def test_prefixed_model_names_lists_ready_providers(tmp_path, monkeypatch):
    """The picker list only carries configured providers, never llamacpp."""
    monkeypatch.setattr(byok, "settings_path",
                        lambda: str(tmp_path / "byok.json"))
    settings = byok.parse_settings({
        "providers": {
            "openai": {"api_key": "sk", "model": "gpt-4o-mini",
                       "available_models": ["gpt-4o", "gpt-4o-mini"]},
            "groq": {},   # not configured → excluded
        },
        "roles": {"embeddings": {"provider": "llamacpp", "model": "e"},
                  "vision": {"provider": "openai", "model": "gpt-4o"}},
    })
    names = byok.prefixed_model_names(settings)
    assert names == ["openai:gpt-4o", "openai:gpt-4o-mini"]
    assert not any(n.startswith("llamacpp:") for n in names)


def test_prefixed_model_names_includes_role_models(tmp_path, monkeypatch):
    monkeypatch.setattr(byok, "settings_path",
                        lambda: str(tmp_path / "byok.json"))
    settings = byok.parse_settings({
        "roles": {"chat": {"provider": "groq", "model": "llama-3.3"}}})
    assert byok.prefixed_model_names(settings) == ["groq:llama-3.3"]


def test_enabled_flag_gates_prefixed_names(tmp_path, monkeypatch):
    """The explicit ``enabled`` switch wins; configs without it migrate to
    "looks configured" so a stored key keeps working (no forced re-toggle)."""
    monkeypatch.setattr(byok, "settings_path",
                        lambda: str(tmp_path / "byok.json"))
    settings = byok.parse_settings({"providers": {
        "openai": {"api_key": "k", "model": "m", "enabled": False},
        "groq": {"api_key": "k", "model": "m"},   # legacy: no flag
    }})
    assert byok.prefixed_model_names(settings) == ["groq:m"]


def test_role_and_model_resolution(tmp_path, monkeypatch):
    monkeypatch.setattr(byok, "settings_path",
                        lambda: str(tmp_path / "byok.json"))
    settings = byok.parse_settings({
        "providers": {"openai": {"model": "gpt-4o-mini"}},
        "roles": {"chat": {"provider": "openai", "model": "gpt-4o"}},
    })
    assert byok.get_role(settings, "chat")["provider"] == "openai"
    assert byok.resolve_model("openai", "", settings) == "gpt-4o-mini"
    assert byok.resolve_model("openai", "gpt-4o", settings) == "gpt-4o"
    # llamacpp + pool are valid role ids (resolvable), bogus is not.
    roles = byok.parse_settings({"roles": {"chat": {"provider": "llamacpp"},
                                           "vision": {"provider": "pool"},
                                           "embeddings": {"provider": "x"}}})
    assert roles["roles"]["chat"]["provider"] == "llamacpp"
    assert roles["roles"]["vision"]["provider"] == "pool"
    assert roles["roles"]["embeddings"]["provider"] == ""


def test_resolve_model_loads_settings_when_none(tmp_path, monkeypatch):
    """The gateway calls resolve_model(pid, '') with no settings — it must
    read the provider's stored model, never return '' (the 2026-10-09 bug:
    an empty chat-role model fell through to the local GGUF path)."""
    monkeypatch.setattr(byok, "settings_path",
                        lambda: str(tmp_path / "byok.json"))
    byok.save_settings(
        {"providers": {"deepseek": {"model": "deepseek-flash"}}})
    assert byok.resolve_model("deepseek", "") == "deepseek-flash"
    assert byok.resolve_model("deepseek", "explicit") == "explicit"


# --------------------------------------------------------------------------- errors


def test_describe_error_is_actionable():
    assert "API key" in byok._describe_error("OpenAI", "HTTP 401: bad key")
    assert "rate limit" in byok._describe_error("OpenAI", "HTTP 429")
    assert "Could not reach" in byok._describe_error(
        "OpenAI", "connection refused")
    assert "request failed" in byok._describe_error("OpenAI", "weird thing")


def test_retry_honors_retry_after_and_exhausts():
    calls = {"n": 0}
    seen_sleeps = []

    def _make():
        calls["n"] += 1
        if calls["n"] == 1:
            return _Resp(429, headers={"Retry-After": "0.05"})
        return _Resp(200, payload={"ok": True})

    import time as _time
    real_sleep = _time.sleep
    try:
        byok.time.sleep = lambda s: seen_sleeps.append(s)
        out = byok._request_with_retry(_make, "Groq")
    finally:
        byok.time.sleep = real_sleep
    assert out.status_code == 200
    assert calls["n"] == 2
    assert seen_sleeps == [0.05]

    def _always():
        return _Resp(429, headers={"Retry-After": "2"})

    try:
        byok.time.sleep = lambda s: None
        with pytest.raises(byok.ByokRateLimitError) as err:
            byok._request_with_retry(_always, "Groq")
    finally:
        byok.time.sleep = real_sleep
    assert err.value.retry_after_ms == 2000


# --------------------------------------------------------------------------- wires


def _no_key(monkeypatch):
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)


def test_openai_wire_request_build(monkeypatch, tmp_path):
    _no_key(monkeypatch)
    monkeypatch.setattr(byok, "settings_path",
                        lambda: str(tmp_path / "byok.json"))
    captured = {}

    def _post(url, json=None, headers=None, timeout=None, **kw):
        captured.update(url=url, body=json, headers=headers)
        return _Resp(200, payload={
            "choices": [{"message": {"content": "hello"}}]})

    monkeypatch.setattr(byok.requests, "post", _post)
    settings = byok.parse_settings(
        {"providers": {"openai": {"api_key": "sk-1", "model": "gpt-4o"}}})
    out = byok.generate_text("openai", "", "Say hi", "Be terse",
                             settings=settings)
    assert out == "hello"
    assert captured["url"] == "https://api.openai.com/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer sk-1"
    assert captured["body"]["model"] == "gpt-4o"
    assert captured["body"]["messages"][0] == \
        {"role": "system", "content": "Be terse"}
    assert captured["body"]["messages"][1] == \
        {"role": "user", "content": "Say hi"}


def test_anthropic_wire_uses_api_key_headers(monkeypatch, tmp_path):
    _no_key(monkeypatch)
    monkeypatch.setattr(byok, "settings_path",
                        lambda: str(tmp_path / "byok.json"))
    captured = {}

    def _post(url, json=None, headers=None, timeout=None, **kw):
        captured.update(headers=headers)
        return _Resp(200, payload={
            "choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(byok.requests, "post", _post)
    settings = byok.parse_settings(
        {"providers": {"anthropic": {"api_key": "sk-ant", "model": "c"}}})
    assert byok.generate_text("anthropic", "", "hi", settings=settings) == "ok"
    assert captured["headers"]["x-api-key"] == "sk-ant"
    assert captured["headers"]["anthropic-version"] == "2023-06-01"
    assert "Authorization" not in captured["headers"]


def test_openai_images_become_content_parts(monkeypatch, tmp_path):
    monkeypatch.setattr(byok, "settings_path",
                        lambda: str(tmp_path / "byok.json"))
    captured = {}

    def _post(url, json=None, headers=None, timeout=None, **kw):
        captured.update(body=json)
        return _Resp(200, payload={"choices": [{"message": {"content": "v"}}]})

    monkeypatch.setattr(byok.requests, "post", _post)
    settings = byok.parse_settings(
        {"providers": {"openai": {"api_key": "k", "model": "gpt-4o"}}})
    byok.generate_text("openai", "", "look", images=["AAAA"],
                       settings=settings)
    parts = captured["body"]["messages"][0]["content"]
    assert parts[0] == {"type": "text", "text": "look"}
    assert parts[1]["image_url"]["url"] == "data:image/png;base64,AAAA"


def test_anthropic_refuses_images(monkeypatch, tmp_path):
    monkeypatch.setattr(byok, "settings_path",
                        lambda: str(tmp_path / "byok.json"))
    settings = byok.parse_settings(
        {"providers": {"anthropic": {"api_key": "k", "model": "c"}}})
    with pytest.raises(byok.ByokProviderError):
        byok.generate_text("anthropic", "", "look", images=["AAAA"],
                           settings=settings)


def test_ollama_wire_generate_and_list(monkeypatch, tmp_path):
    monkeypatch.setattr(byok, "settings_path",
                        lambda: str(tmp_path / "byok.json"))
    posts = {}

    def _post(url, json=None, **kw):
        posts.update(url=url, body=json)
        return _Resp(200, payload={"response": "local answer"})

    monkeypatch.setattr(byok.requests, "post", _post)
    settings = byok.parse_settings(
        {"providers": {"ollama": {"model": "llama3.2:1b"}}})
    out = byok.generate_text("ollama", "", "hi", "sys", settings=settings)
    assert out == "local answer"
    assert posts["url"].endswith("/api/generate")
    assert posts["body"]["system"] == "sys"

    monkeypatch.setattr(byok.requests, "get",
                        lambda url, timeout=None, **kw: _Resp(
                            200, payload={"models": [{"name": "a:1"},
                                                     {"name": "b:2"}]}))
    assert [m["id"] for m in byok.list_models("ollama", settings)] == \
        ["a:1", "b:2"]


def test_name_fallback_model_normalization(monkeypatch, tmp_path):
    monkeypatch.setattr(byok, "settings_path",
                        lambda: str(tmp_path / "byok.json"))
    monkeypatch.setattr(byok.requests, "get",
                        lambda url, headers=None, timeout=None, **kw: _Resp(
                            200, payload={"data": [
                                {"id": "x/y", "name": "Nice Name",
                                 "display_name": "Display"}]}))
    settings = byok.parse_settings(
        {"providers": {"openrouter": {"api_key": "k"}}})
    opts = byok.list_models("openrouter", settings)
    assert opts == [{"id": "x/y", "label": "Nice Name"}]  # name-fallback

    opts2 = byok.list_models("openai", byok.parse_settings(
        {"providers": {"openai": {"api_key": "k"}}}))
    assert opts2 == [{"id": "x/y", "label": "Display"}]   # display wins


def test_cloud_generate_without_key_raises_friendly(monkeypatch, tmp_path):
    _no_key(monkeypatch)
    monkeypatch.setattr(byok, "settings_path",
                        lambda: str(tmp_path / "byok.json"))
    settings = byok.parse_settings(
        {"providers": {"openai": {"model": "gpt-4o"}}})
    with pytest.raises(byok.ByokProviderError) as err:
        byok.generate_text("openai", "", "hi", settings=settings)
    assert "API key" in str(err.value)


# --------------------------------------------------------------------------- CLI


def test_claude_cli_args_and_output_extraction():
    args = byok._claude_cli_args("claude-3-5-sonnet", '{"a":1}', "be brief")
    assert "-p" in args and "--output-format" in args
    assert "--append-system-prompt" in args
    assert "--json-schema" in args
    assert args[args.index("--model") + 1] == "claude-3-5-sonnet"

    assert byok._claude_cli_output('{"result": "hello"}') == "hello"
    assert byok._claude_cli_output(
        '{"structured_output": {"a": 1}}') == '{"a": 1}'
    assert byok._claude_cli_output('"{\\"ok\\": true}"') == '{"ok": true}'
    assert byok._claude_cli_output("plain text") == "plain text"
    assert byok._claude_cli_output("") == ""


def test_codex_cli_args_and_output_extraction():
    args = byok._codex_cli_args("gpt-5", "be brief")
    assert args[:1] == ["exec"] and "--json" in args
    assert args[args.index("--model") + 1] == "gpt-5"

    stdout = "\n".join([
        '{"type":"log","message":"working"}',
        '{"result": "the final answer"}',
    ])
    assert byok._codex_cli_output(stdout) == "the final answer"
    assert byok._codex_cli_output("raw text") == "raw text"


def test_run_cli_real_subprocess_roundtrip():
    out = byok._run_cli(
        sys.executable,
        ["-c", "import sys; sys.stdout.write(sys.stdin.read().upper())"],
        "hello", 60)
    assert out == "HELLO"


def test_run_cli_missing_command_is_friendly():
    with pytest.raises(byok.ByokProviderError) as err:
        byok._run_cli("definitely-not-a-real-cmd-xyz", [], "", 10)
    assert "was not found" in str(err.value)


# --------------------------------------------------------------------------- façade


def test_pool_is_coming_soon_everywhere(tmp_path, monkeypatch):
    monkeypatch.setattr(byok, "settings_path",
                        lambda: str(tmp_path / "byok.json"))
    for call in (
        lambda: byok.generate_text("pool", "m", "hi"),
        lambda: byok.list_models("pool"),
        lambda: list(byok.generate_stream("pool", "m", "hi")),
    ):
        with pytest.raises(byok.ByokProviderError) as err:
            call()
        assert "coming soon" in str(err.value).lower()
    assert byok.test_connection("pool")["ok"] is False
    assert "coming soon" in byok.test_connection("pool")["message"].lower()


def test_llamacpp_is_engine_owned(tmp_path, monkeypatch):
    monkeypatch.setattr(byok, "settings_path",
                        lambda: str(tmp_path / "byok.json"))
    with pytest.raises(byok.ByokProviderError):
        byok.generate_text("llamacpp", "m", "hi")


def test_provider_choices_mark_pool_disabled():
    choices = {c["id"]: c for c in byok.provider_choices()}
    assert choices["llamacpp"]["coming_soon"] is False
    assert choices["pool"]["coming_soon"] is True
    assert "pool" not in [c["id"] for c in byok.provider_choices(
        include_disabled=False)]


def test_test_connection_ollama_uses_live_list(monkeypatch, tmp_path):
    monkeypatch.setattr(byok, "settings_path",
                        lambda: str(tmp_path / "byok.json"))
    settings = byok.parse_settings(
        {"providers": {"ollama": {"model": "missing:1"}}})
    monkeypatch.setattr(byok, "list_models",
                        lambda pid, s=None: [{"id": "a:1", "label": "a:1"}])
    bad = byok.test_connection("ollama", settings)
    assert bad["ok"] is False and "ollama pull" in bad["message"]

    settings = byok.parse_settings(
        {"providers": {"ollama": {"model": "a:1"}}})
    good = byok.test_connection("ollama", settings)
    assert good["ok"] is True and good["models"] == ["a:1"]


def test_record_verification_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(byok, "settings_path",
                        lambda: str(tmp_path / "byok.json"))
    byok.record_verification("ollama", {"ok": True, "message": "fine"},
                             "a:1")
    stored = byok.load_settings()["verification"]["ollama"]
    assert stored["ok"] is True and stored["model_id"] == "a:1"
    assert stored["tested_at"]


# ------------------------------------------------- model ref helpers


def test_split_model_ref_only_known_prefixes():
    assert byok.split_model_ref("openai:gpt-4o-mini") == (
        "openai", "gpt-4o-mini")
    assert byok.split_model_ref("llamacpp:some.gguf") == (
        "llamacpp", "some.gguf")
    # Ollama tags and Windows paths are NOT prefixes.
    assert byok.split_model_ref("llama3.2:1b") == ("", "llama3.2:1b")
    assert byok.split_model_ref(r"D:\m\x.gguf") == ("", r"D:\m\x.gguf")
    assert byok.split_model_ref("") == ("", "")


def test_join_model_ref_is_idempotent_and_skips_legacy():
    assert byok.join_model_ref("openai", "gpt-4o") == "openai:gpt-4o"
    # Already prefixed (any provider) keeps its own prefix.
    assert byok.join_model_ref("openai", "groq:llama-3.3") == "groq:llama-3.3"
    # Legacy engines never prefix.
    assert byok.join_model_ref("", "m") == "m"
    assert byok.join_model_ref("llamacpp", "m.gguf") == "m.gguf"


def test_normalize_engine_model_follows_the_prefix():
    assert byok.normalize_engine_model("llamacpp", "openai:gpt-4o") == (
        "openai", "openai:gpt-4o")
    assert byok.normalize_engine_model("", "groq:k") == ("groq", "groq:k")
    # No prefix: engine untouched, model untouched.
    assert byok.normalize_engine_model("ollama", "llama3.2:1b") == (
        "ollama", "llama3.2:1b")
    assert byok.normalize_engine_model("LLAMACPP", r"D:\m.gguf") == (
        "llamacpp", r"D:\m.gguf")


def test_is_local_engine_spellings():
    for value in ("", "llamacpp", "llama.cpp", "llama_cpp", "LlamaCpp"):
        assert byok.is_local_engine(value), value
    for value in ("ollama", "openai", "lm-studio"):
        assert not byok.is_local_engine(value), value
