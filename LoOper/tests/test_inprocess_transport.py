"""Unit tests for AI/inprocess_transport.py — the direct-engine seam used by
exported agents. All engine interaction is faked.
"""

import os
import sys
from types import SimpleNamespace

import pytest

from AI import inprocess_transport as it


@pytest.fixture(autouse=True)
def _clean_engine():
    it._engine = None
    yield
    it._engine = None


# ---------------------------------------------------------------------------
# Mode detection / path resolution
# ---------------------------------------------------------------------------


def test_is_direct_mode(monkeypatch):
    monkeypatch.delenv("ARROW_DIRECT_ENGINE", raising=False)
    assert it.is_direct_mode() is False
    monkeypatch.setenv("ARROW_DIRECT_ENGINE", "1")
    assert it.is_direct_mode() is True


def test_resolve_model_path_absolute(tmp_path):
    model = tmp_path / "m.gguf"
    model.write_bytes(b"GGUF")
    assert it._resolve_model_path(str(model)) == str(model)


def test_resolve_model_path_missing_returns_name():
    assert it._resolve_model_path("nonexistent.gguf") == "nonexistent.gguf"
    assert it._resolve_model_path("") == ""


def test_resolve_model_path_basename_in_models_dir(monkeypatch, tmp_path):
    models = tmp_path / "models"
    models.mkdir()
    model = models / "smollm.gguf"
    model.write_bytes(b"GGUF")

    import AI.config_loader as cl
    monkeypatch.setattr(cl, "get_models_dir", lambda: str(models))
    assert it._resolve_model_path("smollm.gguf") == str(model)


# ---------------------------------------------------------------------------
# Image / token helpers
# ---------------------------------------------------------------------------


def test_to_data_url_pass_through():
    assert it._to_data_url("") == ""
    assert it._to_data_url("data:image/png;base64,AAA") == "data:image/png;base64,AAA"


def test_to_data_url_from_file(tmp_path):
    img = tmp_path / "x.png"
    img.write_bytes(b"abc")
    url = it._to_data_url(str(img))
    assert url.startswith("data:image/png;base64,")
    assert url.endswith("YWJj")  # base64 of "abc"


def test_to_data_url_raw_base64():
    assert it._to_data_url("QUJD") == "data:image/png;base64,QUJD"


def test_clamp_max_tokens():
    engine = SimpleNamespace(context_size=4096)
    assert it._clamp_max_tokens(engine, None) is None
    assert it._clamp_max_tokens(engine, 100) == 100
    assert it._clamp_max_tokens(engine, 5000) == 4096 - 128
    assert it._clamp_max_tokens(engine, "not-int") == "not-int"


def test_build_messages_text_only():
    msgs = it._build_messages("hello", "", [])
    assert msgs == [{"role": "user", "content": "hello"}]


def test_build_messages_with_system():
    msgs = it._build_messages("q", "be brief", [])
    assert msgs[0] == {"role": "system", "content": "be brief"}
    assert msgs[1]["role"] == "user"


def test_build_messages_with_images():
    msgs = it._build_messages("look", "", ["data:image/png;base64,AAA"])
    content = msgs[0]["content"]
    assert content[0] == {"type": "text", "text": "look"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"] == "data:image/png;base64,AAA"


# ---------------------------------------------------------------------------
# generate / chat with fake engine
# ---------------------------------------------------------------------------


class FakeEngine:
    def __init__(self, result=None, error=None):
        self.result = result or {"response": "ok", "model": "m", "done": True, "usage": {}}
        self.error = error
        self.is_running = True
        self.context_size = 4096
        self.model_path = "m.gguf"
        self.server_port = 8081
        self.chat_calls = []
        self.started = 0
        self.stopped = 0

    def chat(self, messages, **kwargs):
        self.chat_calls.append((messages, kwargs))
        if self.error:
            return {"error": self.error, "response": ""}
        return self.result

    def generate(self, prompt, **kwargs):
        if self.error:
            return {"error": self.error, "response": ""}
        return self.result

    def start_server(self, blocking=False):
        self.started += 1
        self.is_running = True
        return True

    def stop_server(self):
        self.stopped += 1
        self.is_running = False

    def generate_stream(self, prompt, **kwargs):
        yield "chunk"


def test_generate_uses_built_messages(monkeypatch):
    engine = FakeEngine()
    monkeypatch.setattr(it, "get_engine", lambda: engine)
    monkeypatch.setattr(it, "_ensure_engine_running", lambda: None)
    result = it.generate("hello", system="sys")
    assert result["response"] == "ok"
    assert result["_debug"]["direct_engine"] is True
    msgs = engine.chat_calls[0][0]
    assert msgs[0]["content"] == "sys"
    assert msgs[1]["content"] == "hello"


def test_generate_raises_on_engine_error(monkeypatch):
    engine = FakeEngine(error="model load failed")
    monkeypatch.setattr(it, "get_engine", lambda: engine)
    monkeypatch.setattr(it, "_ensure_engine_running", lambda: None)
    with pytest.raises(it.LLMTransportError):
        it.generate("hi")


def test_chat_raises_on_engine_error(monkeypatch):
    engine = FakeEngine(error="boom")
    monkeypatch.setattr(it, "get_engine", lambda: engine)
    monkeypatch.setattr(it, "_ensure_engine_running", lambda: None)
    with pytest.raises(it.LLMTransportError):
        it.chat([{"role": "user", "content": "hi"}])


def test_ensure_engine_running_starts_when_not_running(monkeypatch):
    engine = FakeEngine()
    engine.is_running = False
    monkeypatch.setattr(it, "get_engine", lambda: engine)
    it._ensure_engine_running()
    assert engine.started == 1


def test_ensure_engine_running_raises_on_failure(monkeypatch):
    engine = FakeEngine()
    engine.is_running = False
    engine.start_server = lambda blocking=False: False
    monkeypatch.setattr(it, "get_engine", lambda: engine)
    with pytest.raises(it.LLMTransportError):
        it._ensure_engine_running()


def test_probe_not_running_raises(monkeypatch):
    engine = FakeEngine()
    engine.is_running = False
    monkeypatch.setattr(it, "get_engine", lambda: engine)
    with pytest.raises(it.LLMTransportError):
        it.probe()


def test_probe_engine_error_raises(monkeypatch):
    engine = FakeEngine(error="probe failed")
    monkeypatch.setattr(it, "get_engine", lambda: engine)
    with pytest.raises(it.LLMTransportError):
        it.probe()


def test_ensure_started_success(monkeypatch):
    engine = FakeEngine()
    engine.is_running = False
    monkeypatch.setattr(it, "get_engine", lambda: engine)
    it.ensure_started()
    assert engine.started == 1


def test_ensure_started_restarts_on_probe_failure(monkeypatch):
    engine = FakeEngine()
    engine.is_running = False
    monkeypatch.setattr(it, "get_engine", lambda: engine)
    calls = {"n": 0}

    def failing_probe():
        calls["n"] += 1
        if calls["n"] <= 2:
            raise it.LLMTransportError("probe failed")

    monkeypatch.setattr(it, "probe", failing_probe)
    it.ensure_started(max_restarts=3)
    assert engine.started == 3  # initial + 2 restarts
    assert engine.stopped == 2


def test_ensure_started_gives_up(monkeypatch):
    engine = FakeEngine()
    monkeypatch.setattr(it, "get_engine", lambda: engine)
    monkeypatch.setattr(it, "probe",
                        lambda: (_ for _ in ()).throw(it.LLMTransportError("always fails")))
    with pytest.raises(it.LLMTransportError):
        it.ensure_started(max_restarts=1)
    assert engine.stopped == 1


def test_shutdown_stops_engine(monkeypatch):
    engine = FakeEngine()
    it._engine = engine
    it.shutdown()
    assert engine.stopped == 1
    assert it._engine is None


def test_generate_stream_delegates(monkeypatch):
    engine = FakeEngine()
    monkeypatch.setattr(it, "get_engine", lambda: engine)
    monkeypatch.setattr(it, "_ensure_engine_running", lambda: None)
    chunks = list(it.generate_stream("hi"))
    assert chunks == ["chunk"]


def test_get_engine_creates_once(monkeypatch):
    from AI.llama_cpp_engine import LlamaCppEngine
    import AI.config_loader as cl

    monkeypatch.setattr(cl, "get_llamacpp_config", lambda: {
        "model_path": "m.gguf", "server_port": 9000, "threads": 2})
    monkeypatch.setattr(it, "_resolve_model_path", lambda p: p or "")
    created = []
    real_init = LlamaCppEngine.__init__

    def spy_init(self, config=None):
        created.append(config)
        real_init(self, config)

    monkeypatch.setattr(LlamaCppEngine, "__init__", spy_init)
    e1 = it.get_engine()
    e2 = it.get_engine()
    assert e1 is e2
    assert len(created) == 1
    assert created[0]["server_port"] == 9000
