"""Unit tests for AI/llama_cpp_engine.py.

All network and subprocess activity is faked: the ``requests`` module is
replaced by a recording fake (see conftest), subprocess.Popen/run are
monkeypatched, and server executables are materialized as temp files.
"""

import os
import subprocess
import sys
import types

import pytest

from AI import llama_cpp_engine as lce
from conftest import FakeResponse, FakeSession


import contextlib


@contextlib.contextmanager
def _patched_os_name(name):
    """Temporarily set the global ``os.name``, restoring it BEFORE the test's
    call phase ends.

    Patching ``os.name`` via monkeypatch is not enough: pytest generates the
    test's status line (verbose mode) right after the call phase, before
    fixture teardown restores the patch.  With ``os.name='posix'`` on Windows,
    pathlib raises ``NotImplementedError: cannot instantiate 'PosixPath'``
    while pytest computes the relative nodeid, crashing the whole session.
    """
    import os as _os

    old = _os.name
    _os.name = name
    try:
        yield
    finally:
        _os.name = old


# ---------------------------------------------------------------------------
# Response sanitisation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text,expected", [
    ("hello", "hello"),
    ("hello<|im_end|>", "hello"),
    ("hello <|im_end|>\n", "hello"),
    ("hello</s>", "hello"),
    ("hello<|endoftext|>", "hello"),
    ("hello<|eot_id|>", "hello"),
    ("hello<|end|>", "hello"),
    ("hello<|im_end|><|im_end|>", "hello"),  # repeated tokens stripped
    ("", ""),
    (None, None),
    ("a<|im_end|> b", "a<|im_end|> b"),  # only trailing tokens removed
])
def test_sanitize_response(text, expected):
    assert lce._sanitize_response(text) == expected


# ---------------------------------------------------------------------------
# Engine construction / config defaults
# ---------------------------------------------------------------------------


def test_default_config_values():
    engine = lce.LlamaCppEngine({})
    assert engine.server_port == 8081
    assert engine.gpu_layers == 0
    # CPU-first defaults: threads -1 = auto-detect, context 0 = model's
    # native training context, batch 2048 (llama.cpp's own default).
    assert engine.threads == -1
    assert engine.context_size == 0
    assert engine.batch_size == 2048
    assert engine.server_url == "http://127.0.0.1:8081"
    assert engine.is_running is False
    assert engine.capability is None


def test_config_overrides():
    engine = lce.LlamaCppEngine({
        "server_port": "9000",
        "gpu_layers": "12",
        "threads": "8",
        "context_size": "2048",
        "batch_size": "256",
        "model_path": "C:\\models\\x.gguf",
        "mmproj_path": "C:\\models\\mmproj.gguf",
    })
    assert engine.server_port == 9000
    assert engine.gpu_layers == 12
    assert engine.threads == 8
    assert engine.context_size == 2048
    assert engine.batch_size == 256
    assert engine.model_path == "C:\\models\\x.gguf"
    assert engine.mmproj_path == "C:\\models\\mmproj.gguf"
    assert engine._embedding_mode is False


def test_embedding_mode_flag():
    assert lce.LlamaCppEngine({})._embedding_mode is False
    assert lce.LlamaCppEngine({"embedding_mode": True})._embedding_mode is True


def test_embed_requires_running_server():
    engine = lce.LlamaCppEngine({})
    with pytest.raises(RuntimeError):
        engine.embed(["a"])


def test_embed_parses_response(monkeypatch):
    engine = lce.LlamaCppEngine({})
    engine.is_running = True

    class _Resp:
        status_code = 200

        def json(self):
            return {"embedding": [[0.1, 0.2], [0.3, 0.4]]}

    monkeypatch.setattr("requests.post", lambda *a, **k: _Resp())
    assert engine.embed(["a", "b"]) == [[0.1, 0.2], [0.3, 0.4]]


def test_embed_raises_on_count_mismatch(monkeypatch):
    engine = lce.LlamaCppEngine({})
    engine.is_running = True

    class _Resp:
        status_code = 200

        def json(self):
            return {"embedding": [[0.1]]}  # wrong count for 2 inputs

    monkeypatch.setattr("requests.post", lambda *a, **k: _Resp())
    with pytest.raises(RuntimeError):
        engine.embed(["a", "b"])


def test_embed_raises_on_http_error(monkeypatch):
    engine = lce.LlamaCppEngine({})
    engine.is_running = True

    class _Resp:
        status_code = 500
        text = "boom"

    monkeypatch.setattr("requests.post", lambda *a, **k: _Resp())
    with pytest.raises(RuntimeError):
        engine.embed(["a"])


def _start_server_capture_cmd(tmp_path, monkeypatch, embedding_mode):
    """Run start_server(blocking=False) and return the recorded Popen cmd."""
    model_file = tmp_path / "model.gguf"
    model_file.write_bytes(b"fake-gguf")

    engine = lce.LlamaCppEngine({
        "model_path": str(model_file),
        "server_port": 8231,
        "context_size": 4096,
        "embedding_mode": embedding_mode,
    })
    engine.is_running = False
    # Deterministic context/architecture detection on the fake GGUF
    monkeypatch.setattr("AI.gguf_model_info.read_model_context_size", lambda p: 4096)
    monkeypatch.setattr("AI.gguf_model_info.read_model_kv_params", lambda p: None)
    monkeypatch.setattr("AI.gguf_model_info.is_moe_model", lambda p: False)
    monkeypatch.setattr(engine, "_kill_process_on_port", lambda: None)
    monkeypatch.setattr(engine, "_ensure_port_free", lambda: True)
    monkeypatch.setattr(
        engine, "_find_server_executable",
        lambda: str(tmp_path / "llama-server.exe"),
    )
    recorded = {}
    monkeypatch.setattr(
        "subprocess.Popen",
        lambda cmd, **kw: recorded.update(cmd=cmd, kwargs=kw) or object(),
    )
    assert engine.start_server(blocking=False) is True
    return recorded["cmd"]


def test_start_server_default_cmd_has_no_embeddings(tmp_path, monkeypatch):
    cmd = _start_server_capture_cmd(tmp_path, monkeypatch, embedding_mode=False)
    assert "--embeddings" not in cmd
    assert cmd[0] == str(tmp_path / "llama-server.exe")
    assert "--model" in cmd
    assert "--ctx-size" in cmd


def test_start_server_embedding_mode_appends_flag(tmp_path, monkeypatch):
    cmd = _start_server_capture_cmd(tmp_path, monkeypatch, embedding_mode=True)
    assert "--embeddings" in cmd
    assert "--model" in cmd
    assert "--ctx-size" in cmd


# ---------------------------------------------------------------------------
# Executable discovery
# ---------------------------------------------------------------------------


def test_find_server_executable_in_build_dir(tmp_path, monkeypatch):
    exe = tmp_path / "build" / "bin" / "Release" / "llama-server.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    monkeypatch.setattr(lce.sys, "frozen", False, raising=False)
    # Isolate LOCALAPPDATA so a real WinGet llama.cpp package on the test
    # machine does not out-rank the local build under test.
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "no_winget"))
    with _patched_os_name("nt"):
        engine = lce.LlamaCppEngine({"llama_cpp_path": str(tmp_path)})
        assert engine._find_server_executable() == str(exe)


def test_find_server_executable_prefers_winget_vulkan(tmp_path, monkeypatch):
    """A WinGet release (Vulkan) must out-rank the plain CPU fork build."""
    exe = tmp_path / "build" / "bin" / "Release" / "llama-server.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    winget_exe = (
        tmp_path / "winget" / "Microsoft" / "WinGet" / "Packages"
        / "ggml.llamacpp_1" / "llama-server.exe"
    )
    winget_exe.parent.mkdir(parents=True)
    winget_exe.write_bytes(b"MZ")
    monkeypatch.setattr(lce.sys, "frozen", False, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "winget"))
    with _patched_os_name("nt"):
        engine = lce.LlamaCppEngine({"llama_cpp_path": str(tmp_path)})
        assert engine._find_server_executable() == str(winget_exe)


def test_find_server_executable_none_when_missing(tmp_path):
    with _patched_os_name("posix"):
        engine = lce.LlamaCppEngine({"llama_cpp_path": str(tmp_path)})
        assert engine._find_server_executable() is None


def test_find_mtmd_cli_path_fallback(tmp_path, monkeypatch):
    exe = tmp_path / "llama-mtmd-cli.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.setattr(lce.sys, "frozen", False, raising=False)
    with _patched_os_name("nt"):
        engine = lce.LlamaCppEngine({"llama_cpp_path": str(tmp_path)})
        assert engine._find_mtmd_cli() == str(exe)


# ---------------------------------------------------------------------------
# generate() — HTTP completion path
# ---------------------------------------------------------------------------


def _install_fake_requests(monkeypatch, session):
    import conftest
    mod = conftest.build_fake_requests_module(session)
    monkeypatch.setitem(sys.modules, "requests", mod)
    return mod


def _sequenced(seq):
    """Return a FakeSession responder that hands out responses in order.

    Each POST consumes the next item; beyond the end the last item repeats.
    """
    import itertools

    counter = itertools.count()

    def _responder(method, url, **kwargs):
        idx = next(counter)
        return seq[min(idx, len(seq) - 1)]

    return _responder


# ---------------------------------------------------------------------------
# Reasoning-model support — thinking blocks / truncated generations
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text,expected", [
    ("<think>deep thought</think>Answer", "Answer"),
    ("<think>thought</think> Answer", "Answer"),
    ("<|start_think|>thinking</|end_think|> answer", "answer"),
    ("[THINK]t[/THINK]done", "done"),
    ("Answer", "Answer"),
    ("<think>only thinking</think>", ""),
    ("", ""),
    (None, None),
])
def test_strip_reasoning_blocks(text, expected):
    assert lce._strip_reasoning_blocks(text) == expected


def test_generate_requires_running_server(monkeypatch):
    engine = lce.LlamaCppEngine({})
    engine.is_running = False
    result = engine.generate("hello")
    assert result["error"] == "Server not running"
    assert result["response"] == ""


def test_generate_detects_dead_process(monkeypatch, tmp_path):
    engine = lce.LlamaCppEngine({"model_path": str(tmp_path / "m.gguf")})
    engine.is_running = True
    engine.server_process = types.SimpleNamespace(poll=lambda: 1)
    result = engine.generate("hello")
    assert "terminated" in result["error"]
    assert engine.is_running is False


def test_final_answer_budget_leaves_reasoning_room():
    """``max_tokens`` is the FINAL-ANSWER budget: the server cap must be wider
    so a thinking model cannot spend the answer's allowance on its chain of
    thought.  Unlimited (``-1`` / ``0`` / ``None``) passes through untouched."""
    assert lce._budget_for_final_answer(256) == 256 + lce._REASONING_HEADROOM
    assert lce._budget_for_final_answer(4096) == 8192
    assert lce._budget_for_final_answer("512") == 512 + lce._REASONING_HEADROOM
    assert lce._budget_for_final_answer(-1) == -1
    assert lce._budget_for_final_answer(0) == 0
    assert lce._budget_for_final_answer(None) is None


def test_generate_success_parses_response(monkeypatch):
    session = FakeSession({
        ("POST", "http://127.0.0.1:8081/completion"): FakeResponse(
            200, json_data={
                "content": "Hello there<|im_end|>",
                "tokens_evaluated": 7,
                "tokens_predicted": 2,
            }),
    })
    _install_fake_requests(monkeypatch, session)
    engine = lce.LlamaCppEngine({"model_path": "m.gguf"})
    engine.is_running = True
    engine.server_process = types.SimpleNamespace(poll=lambda: None)

    result = engine.generate("hi", max_tokens=64, temperature=0.5, stop=["END"])
    assert result["response"] == "Hello there"
    assert result["usage"]["prompt_tokens"] == 7
    assert result["usage"]["completion_tokens"] == 2
    assert result["done"] is True

    method, url, kwargs = session.calls[0]
    payload = kwargs["json"]
    assert payload["prompt"] == "hi"
    # The caller's number budgets the ANSWER; the cap must leave the chain of
    # thought its own room so the answer is not truncated mid-sentence.
    assert payload["n_predict"] >= 64 + 1024
    assert payload["temperature"] == 0.5
    assert payload["stop"] == ["END"]


def test_generate_retries_then_clears_running(monkeypatch):
    class AlwaysDown(FakeSession):
        def _dispatch(self, method, url, **kwargs):
            raise RuntimeError("boom")

    _install_fake_requests(monkeypatch, AlwaysDown())
    engine = lce.LlamaCppEngine({"model_path": "m.gguf"})
    engine.is_running = True
    engine.server_process = types.SimpleNamespace(poll=lambda: None)

    result = engine.generate("hi")
    assert result["error"] == "boom"
    assert result["response"] == ""
    assert engine.is_running is False  # stale flag cleared after exhaustion


def test_generate_continues_truncated_completion(monkeypatch):
    """generate() must continue a truncated /completion instead of returning
    the half-written response."""
    round1 = FakeResponse(200, json_data={
        "content": "Part one.",
        "tokens_evaluated": 5,
        "tokens_predicted": 64,
        "truncated": True,
    })
    round2 = FakeResponse(200, json_data={
        "content": " Part two.",
        "tokens_evaluated": 69,
        "tokens_predicted": 10,
        "truncated": False,
    })
    session = FakeSession(_sequenced([round1, round2]))
    _install_fake_requests(monkeypatch, session)
    engine = lce.LlamaCppEngine({"model_path": "m.gguf"})
    engine.is_running = True
    engine.server_process = types.SimpleNamespace(poll=lambda: None)

    result = engine.generate("hi", max_tokens=64)
    assert result["response"] == "Part one. Part two."
    assert result["truncated"] is False
    # Continuation prompt must carry the accumulated output
    _, _, kw2 = session.calls[1]
    assert "Part one." in kw2["json"]["prompt"]


def test_generate_strips_thinking_blocks(monkeypatch):
    """Raw /completion does not parse reasoning — thinking blocks must be
    stripped so the final answer is what the caller receives."""
    session = FakeSession({
        ("POST", "http://127.0.0.1:8081/completion"): FakeResponse(
            200, json_data={
                "content": "<think>hmm, let me reason</think>\nThe final answer.",
                "tokens_evaluated": 5,
                "tokens_predicted": 9,
            }),
    })
    _install_fake_requests(monkeypatch, session)
    engine = lce.LlamaCppEngine({"model_path": "m.gguf"})
    engine.is_running = True
    engine.server_process = types.SimpleNamespace(poll=lambda: None)

    result = engine.generate("hi")
    assert result["response"] == "The final answer."


# ---------------------------------------------------------------------------
# chat() — text-only and multimodal paths
# ---------------------------------------------------------------------------


def test_chat_text_only_via_chat_completions(monkeypatch):
    session = FakeSession({
        ("POST", "http://127.0.0.1:8081/chat/completions"): FakeResponse(
            200, json_data={
                "choices": [{"message": {"content": "chat answer"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }),
    })
    _install_fake_requests(monkeypatch, session)
    engine = lce.LlamaCppEngine({"model_path": "m.gguf"})
    engine.is_running = True

    result = engine.chat([{"role": "user", "content": "hi"}])
    assert result["response"] == "chat answer"
    assert result["done"] is True


def test_chat_falls_back_to_generate_on_chat_error(monkeypatch):
    session = FakeSession({
        ("POST", "http://127.0.0.1:8081/chat/completions"): FakeResponse(500),
        ("POST", "http://127.0.0.1:8081/completion"): FakeResponse(
            200, json_data={"content": "fallback answer"}),
    })
    _install_fake_requests(monkeypatch, session)
    engine = lce.LlamaCppEngine({"model_path": "m.gguf"})
    engine.is_running = True
    engine.server_process = types.SimpleNamespace(poll=lambda: None)

    result = engine.chat([{"role": "user", "content": "hi"}])
    assert result["response"] == "fallback answer"


def test_chat_multimodal_uses_completion_fallback(monkeypatch):
    session = FakeSession({
        ("POST", "http://127.0.0.1:8081/chat/completions"): FakeResponse(200, json_data={"choices": []}),
        ("POST", "http://127.0.0.1:8081/completion"): FakeResponse(
            200, json_data={"content": "vision answer", "tokens_evaluated": 3}),
    })
    _install_fake_requests(monkeypatch, session)
    engine = lce.LlamaCppEngine({"model_path": "m.gguf"})
    engine.is_running = True

    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": "what is this?"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        ],
    }]
    result = engine.chat(messages)
    assert result["response"] == "vision answer"
    # The image payload must be sent as image_data objects in Path C
    payload = session.calls[-1][2]["json"]
    assert payload["image_data"][0]["data"] == "AAAA"


def test_chat_prefers_content_over_reasoning(monkeypatch):
    """Thinking models return BOTH reasoning_content and content — the final
    answer (content) must be returned, never the chain of thought."""
    session = FakeSession({
        ("POST", "http://127.0.0.1:8081/chat/completions"): FakeResponse(
            200, json_data={
                "choices": [{
                    "message": {
                        "content": "The answer is 42.",
                        "reasoning_content": "lots of thinking...",
                    },
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2},
            }),
    })
    _install_fake_requests(monkeypatch, session)
    engine = lce.LlamaCppEngine({"model_path": "m.gguf"})
    engine.is_running = True

    result = engine.chat([{"role": "user", "content": "hi"}])
    assert result["response"] == "The answer is 42."


def test_chat_does_not_return_reasoning_as_response(monkeypatch):
    """When the model produced ONLY thinking (no final answer), the response
    must be empty — NOT the thinking process."""
    session = FakeSession({
        ("POST", "http://127.0.0.1:8081/chat/completions"): FakeResponse(
            200, json_data={
                "choices": [{
                    "message": {
                        "content": "",
                        "reasoning_content": "only thinking here",
                    },
                    "finish_reason": "stop",
                }],
                "usage": {},
            }),
    })
    _install_fake_requests(monkeypatch, session)
    engine = lce.LlamaCppEngine({"model_path": "m.gguf"})
    engine.is_running = True

    result = engine.chat([{"role": "user", "content": "hi"}])
    assert result["response"] == ""


def test_chat_continues_truncated_reasoning_generation(monkeypatch):
    """A model cut off mid-thinking (finish_reason=length) must be continued
    with continue_final_message until it produces a final answer."""
    round1 = FakeResponse(200, json_data={
        "choices": [{
            "message": {"content": "", "reasoning_content": "let me think..."},
            "finish_reason": "length",
        }],
        "usage": {"prompt_tokens": 10, "completion_tokens": 100},
    })
    round2 = FakeResponse(200, json_data={
        "choices": [{
            "message": {
                "content": "The answer is 42.",
                "reasoning_content": "let me think...",
            },
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 20, "completion_tokens": 50},
    })
    session = FakeSession(_sequenced([round1, round2]))
    _install_fake_requests(monkeypatch, session)
    engine = lce.LlamaCppEngine({"model_path": "m.gguf"})
    engine.is_running = True

    result = engine.chat([{"role": "user", "content": "question"}], max_tokens=64)
    assert result["response"] == "The answer is 42."
    assert result["truncated"] is False
    # Round 2 must be a vLLM-compatible continuation of the partial message
    assert len(session.calls) == 2
    _, _, kw2 = session.calls[1]
    assert kw2["json"]["continue_final_message"] is True
    cont_messages = kw2["json"]["messages"]
    assert cont_messages[-1]["role"] == "assistant"
    assert cont_messages[-1]["content"] == ""
    assert cont_messages[-1]["reasoning_content"] == "let me think..."


def test_messages_to_prompt_formatting():
    engine = lce.LlamaCppEngine({})
    prompt = engine._messages_to_prompt([
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ])
    assert "System: be brief" in prompt
    assert "User: hello" in prompt
    assert "Assistant: hi" in prompt
    assert prompt.endswith("Assistant: ")


# ---------------------------------------------------------------------------
# generate_mtmd — CLI subprocess path
# ---------------------------------------------------------------------------


def test_generate_mtmd_missing_cli(tmp_path, monkeypatch):
    monkeypatch.setattr(lce.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
    engine = lce.LlamaCppEngine({"model_path": "m.gguf"})
    monkeypatch.setattr(engine, "_find_mtmd_cli", lambda: None)
    result = engine.generate_mtmd("prompt", "img.png")
    assert "not found" in result["error"]


def test_generate_mtmd_missing_image(tmp_path, monkeypatch):
    engine = lce.LlamaCppEngine({"model_path": "m.gguf"})
    monkeypatch.setattr(engine, "_find_mtmd_cli", lambda: str(tmp_path / "cli"))
    result = engine.generate_mtmd("prompt", str(tmp_path / "missing.png"))
    assert "Image not found" in result["error"]


def test_generate_mtmd_success(monkeypatch, tmp_path):
    image = tmp_path / "img.png"
    image.write_bytes(b"PNG")
    engine = lce.LlamaCppEngine({"model_path": "m.gguf"})
    monkeypatch.setattr(engine, "_find_mtmd_cli", lambda: str(tmp_path / "cli"))

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return types.SimpleNamespace(stdout=" answer </s>\n", stderr="", returncode=0)

    monkeypatch.setattr(lce.subprocess, "run", fake_run)
    result = engine.generate_mtmd("prompt", str(image), temperature=0.3, max_tokens=64)
    assert result["response"] == "answer"
    assert result["done"] is True
    cmd = calls[0][0]
    assert "--model" in cmd and "--image" in cmd and "--temp" in cmd and "0.3" in cmd


def test_generate_mtmd_timeout(monkeypatch, tmp_path):
    image = tmp_path / "img.png"
    image.write_bytes(b"PNG")
    engine = lce.LlamaCppEngine({"model_path": "m.gguf"})
    monkeypatch.setattr(engine, "_find_mtmd_cli", lambda: str(tmp_path / "cli"))

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=10)

    monkeypatch.setattr(lce.subprocess, "run", fake_run)
    result = engine.generate_mtmd("prompt", str(image))
    assert "timed out" in result["error"]


# ---------------------------------------------------------------------------
# probe / get_server_info
# ---------------------------------------------------------------------------


def test_probe_not_running(monkeypatch):
    engine = lce.LlamaCppEngine({})
    engine.is_running = False
    assert engine.probe() is False


def test_probe_success(monkeypatch):
    session = FakeSession({
        ("POST", "http://127.0.0.1:8081/completion"): FakeResponse(
            200, json_data={"content": "pong"}),
    })
    _install_fake_requests(monkeypatch, session)
    engine = lce.LlamaCppEngine({"model_path": "m.gguf"})
    engine.is_running = True
    engine.server_process = types.SimpleNamespace(poll=lambda: None)
    assert engine.probe() is True


def test_get_server_info_running(monkeypatch):
    session = FakeSession({
        ("GET", "http://127.0.0.1:8081/health"): FakeResponse(200, json_data={"status": "ok"}),
    })
    _install_fake_requests(monkeypatch, session)
    engine = lce.LlamaCppEngine({"model_path": "m.gguf"})
    info = engine.get_server_info()
    assert info["status"] == "running"
    assert info["port"] == 8081


def test_get_server_info_stopped(monkeypatch):
    session = FakeSession({
        ("GET", "http://127.0.0.1:8081/health"): FakeResponse(500),
    })
    _install_fake_requests(monkeypatch, session)
    engine = lce.LlamaCppEngine({"model_path": "m.gguf"})
    info = engine.get_server_info()
    assert info["status"] == "error"


# ---------------------------------------------------------------------------
# Server lifecycle (start/stop) — subprocess fully faked
# ---------------------------------------------------------------------------


def test_start_server_missing_model(tmp_path):
    engine = lce.LlamaCppEngine({"model_path": str(tmp_path / "nope.gguf")})
    assert engine.start_server() is False


def test_start_server_nonblocking(monkeypatch, tmp_path):
    model = tmp_path / "model.gguf"
    model.write_bytes(b"GGUF")
    exe_dir = tmp_path / "build" / "bin"
    exe_dir.mkdir(parents=True)
    # posix discovery looks for the extensionless binary name
    exe = exe_dir / "llama-server"
    exe.write_bytes(b"ELF")

    popen_calls = []

    class FakePopen:
        def __init__(self, cmd, **kwargs):
            popen_calls.append((cmd, kwargs))
            self.pid = 1234

        def poll(self):
            return None

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

    with _patched_os_name("posix"):  # avoid taskkill/netstat paths
        monkeypatch.setattr(lce.subprocess, "Popen", FakePopen)
        engine = lce.LlamaCppEngine({
            "model_path": str(model),
            "llama_cpp_path": str(tmp_path),
        })
        # Never touch the real network: assume the port is free
        monkeypatch.setattr(engine, "_ensure_port_free", lambda: True)
        # Point _find_server_executable at the fake exe via llama_cpp_path layout
        assert engine.start_server(blocking=False) is True
        assert engine.is_running is True
        assert engine.server_process is not None
        cmd = popen_calls[0][0]
        assert cmd[0] == str(exe)
        assert "--model" in cmd and "--port" in cmd


def test_start_server_moe_capability_adds_cpu_moe(monkeypatch, tmp_path):
    """MoE capability + gpu_layers>0 must append --cpu-moe after --gpu-layers."""
    model = tmp_path / "model.gguf"
    model.write_bytes(b"GGUF")
    exe_dir = tmp_path / "build" / "bin"
    exe_dir.mkdir(parents=True)
    exe = exe_dir / "llama-server"
    exe.write_bytes(b"ELF")

    popen_calls = []

    class FakePopen:
        def __init__(self, cmd, **kwargs):
            popen_calls.append(cmd)

        def poll(self):
            return None

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

    with _patched_os_name("posix"):
        monkeypatch.setattr(lce.subprocess, "Popen", FakePopen)
        engine = lce.LlamaCppEngine({
            "model_path": str(model),
            "llama_cpp_path": str(tmp_path),
            "gpu_layers": 35,
        })
        monkeypatch.setattr(engine, "_ensure_port_free", lambda: True)
        engine.capability = types.SimpleNamespace(
            model_type="text", is_moe=True, needs_special_tokens=False,
        )
        assert engine.start_server(blocking=False) is True
        cmd = popen_calls[0]
        assert "--gpu-layers" in cmd
        assert cmd.index("--cpu-moe") > cmd.index("--gpu-layers")


def test_start_server_dense_no_cpu_moe(monkeypatch, tmp_path):
    """Dense capability must NOT append --cpu-moe even with gpu_layers>0."""
    model = tmp_path / "model.gguf"
    model.write_bytes(b"GGUF")
    exe_dir = tmp_path / "build" / "bin"
    exe_dir.mkdir(parents=True)
    exe = exe_dir / "llama-server"
    exe.write_bytes(b"ELF")

    popen_calls = []

    class FakePopen:
        def __init__(self, cmd, **kwargs):
            popen_calls.append(cmd)

        def poll(self):
            return None

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

    with _patched_os_name("posix"):
        monkeypatch.setattr(lce.subprocess, "Popen", FakePopen)
        engine = lce.LlamaCppEngine({
            "model_path": str(model),
            "llama_cpp_path": str(tmp_path),
            "gpu_layers": 35,
        })
        monkeypatch.setattr(engine, "_ensure_port_free", lambda: True)
        engine.capability = types.SimpleNamespace(
            model_type="text", is_moe=False, needs_special_tokens=False,
        )
        assert engine.start_server(blocking=False) is True
        assert "--cpu-moe" not in popen_calls[0]


def test_start_server_moe_file_fallback_adds_cpu_moe(monkeypatch, tmp_path):
    """With no capability set, a MoE GGUF file is detected directly and
    --cpu-moe is appended when gpu_layers>0."""
    from conftest import build_gguf
    model = tmp_path / "model.gguf"
    model.write_bytes(build_gguf(3, {
        "general.architecture": "qwen3moe",
        "qwen3moe.expert_count": 128,
    }))
    exe_dir = tmp_path / "build" / "bin"
    exe_dir.mkdir(parents=True)
    exe = exe_dir / "llama-server"
    exe.write_bytes(b"ELF")

    popen_calls = []

    class FakePopen:
        def __init__(self, cmd, **kwargs):
            popen_calls.append(cmd)

        def poll(self):
            return None

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

    with _patched_os_name("posix"):
        monkeypatch.setattr(lce.subprocess, "Popen", FakePopen)
        engine = lce.LlamaCppEngine({
            "model_path": str(model),
            "llama_cpp_path": str(tmp_path),
            "gpu_layers": 20,
        })
        monkeypatch.setattr(engine, "_ensure_port_free", lambda: True)
        assert engine.start_server(blocking=False) is True
        assert "--cpu-moe" in popen_calls[0]


def test_start_server_releases_laya_before_allocating(monkeypatch, tmp_path):
    """ONE model per memory pool: the Laya scorer is a burst resource too and
    must be unloaded before llama-server allocates its context/KV cache
    (measured 2026-09-23: a resident Laya starved this startup — native context
    collapsed 262144 -> 1024 tokens and the Vulkan KV cache OOM'd)."""
    import AI.laya_client as lc
    model = tmp_path / "model.gguf"
    model.write_bytes(b"GGUF")
    exe_dir = tmp_path / "build" / "bin"
    exe_dir.mkdir(parents=True)
    (exe_dir / "llama-server").write_bytes(b"ELF")
    released = []
    monkeypatch.setattr(lc, "release", lambda wait=5.0: released.append(wait) or True)

    class FakePopen:
        def __init__(self, cmd, **kwargs):
            pass

        def poll(self):
            return None

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

    with _patched_os_name("posix"):
        monkeypatch.setattr(lce.subprocess, "Popen", FakePopen)
        engine = lce.LlamaCppEngine({
            "model_path": str(model),
            "llama_cpp_path": str(tmp_path),
        })
        monkeypatch.setattr(engine, "_ensure_port_free", lambda: True)
        assert engine.start_server(blocking=False) is True

    assert released, "the start path must release Laya before allocating the model"


def test_stop_server_clears_state(monkeypatch, tmp_path):
    engine = lce.LlamaCppEngine({"model_path": str(tmp_path / "m.gguf")})
    engine.is_running = True

    class FakeProc:
        def __init__(self):
            self.terminated = False
            self.killed = False

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            return 0

        def kill(self):
            self.killed = True

    engine.server_process = FakeProc()
    with _patched_os_name("posix"):
        engine.stop_server()
    assert engine.server_process is None
    assert engine.is_running is False


def test_stop_server_no_process():
    with _patched_os_name("posix"):
        engine = lce.LlamaCppEngine({})
        engine.server_process = None
        engine.is_running = True
        engine.stop_server()
    assert engine.is_running is False


# ---------------------------------------------------------------------------
# LlamaCppManager
# ---------------------------------------------------------------------------


def test_manager_create_get_stop_all(monkeypatch):
    monkeypatch.setattr(lce.LlamaCppEngine, "stop_server", lambda self: None)
    mgr = lce.LlamaCppManager()
    e1 = mgr.create_engine("a", {})
    e2 = mgr.create_engine("a", {})  # same id -> same instance
    assert e1 is e2
    assert mgr.get_engine("b") is None
    e3 = mgr.create_engine("b", {})
    mgr.stop_all()
    assert mgr.engines == {}


def test_global_manager_instance_exists():
    assert isinstance(lce.llama_cpp_manager, lce.LlamaCppManager)


# ---------------------------------------------------------------------------
# KV-cache OOM detection (retry-ladder ctx halving)
# ---------------------------------------------------------------------------


def test_is_kv_cache_oom():
    """KV-cache/context-init allocation failures (CPU RAM) are detected so
    the retry ladder halves --ctx-size; other OOM/AV stderr is not misread."""
    assert lce._is_kv_cache_oom(
        "llama_init_from_model: failed to initialize the context: "
        "failed to allocate buffer for kv cache"
    )
    assert lce._is_kv_cache_oom(
        "common_init_result: failed to create context with model 'x.gguf'"
    )
    assert lce._is_kv_cache_oom(
        "llama_kv_cache_init: failed to allocate KV cache for layer 5"
    )
    # Device-memory (Vulkan/CUDA) OOM is a DIFFERENT failure class — it must
    # keep routing to the GPU-offload rung, not the ctx-halving rung.
    assert not lce._is_kv_cache_oom(
        "vk::Device::allocateMemory: ErrorOutOfDeviceMemory"
    )
    assert not lce._is_kv_cache_oom("")

