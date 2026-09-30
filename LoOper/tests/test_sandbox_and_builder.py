"""Unit tests for:
- sandbox_agent.py (AgentHandler HTTP endpoint with mocked pyautogui)
- builder/agent_standalone_entry.py (bootstrap_direct_engine/shutdown_engine)
- NGUI/graph_elements/export_utils.py (batch/python content generation)
"""

import io
import json
import os

import pytest

import sandbox_agent
from builder import agent_standalone_entry as ase
from NGUI.graph_elements.export_utils import ExportUtils


# ---------------------------------------------------------------------------
# sandbox_agent.AgentHandler
# ---------------------------------------------------------------------------


class FakeSocket:
    def __init__(self, body):
        self._body = body
        self._written = b""

    def read(self, n):
        return self._body

    def write(self, data):
        self._written += data

    def flush(self):
        pass

    def makefile(self, *a, **k):
        return io.BytesIO(self._body)


def make_handler(body, path="/action"):
    h = sandbox_agent.AgentHandler.__new__(sandbox_agent.AgentHandler)
    h.path = path
    h.command = "POST"
    h.requestline = f"POST {path} HTTP/1.1"
    h.protocol_version = "HTTP/1.0"
    h.request_version = "HTTP/1.0"
    h.headers = {"Content-Length": str(len(body))}
    h.rfile = io.BytesIO(body)
    h.wfile = FakeSocket(body)
    h.request = None
    h.server = None
    return h


def _invoke(action_payload, monkeypatch):
    return _invoke_path(action_payload, "/action", monkeypatch)


def _invoke_path(action_payload, path, monkeypatch):
    calls = []
    monkeypatch.setattr(sandbox_agent.pyautogui, "moveTo",
                        lambda *a, **k: calls.append(("moveTo", a, k)))
    monkeypatch.setattr(sandbox_agent.pyautogui, "moveRel",
                        lambda *a, **k: calls.append(("moveRel", a, k)))
    monkeypatch.setattr(sandbox_agent.pyautogui, "position",
                        lambda: (0, 0))
    monkeypatch.setattr(sandbox_agent.pyautogui, "write",
                        lambda *a, **k: calls.append(("write", a, k)))
    monkeypatch.setattr(sandbox_agent.pyautogui, "scroll",
                        lambda *a, **k: calls.append(("scroll", a, k)))
    monkeypatch.setattr(sandbox_agent.pyautogui, "keyDown",
                        lambda *a, **k: calls.append(("keyDown", a, k)))
    monkeypatch.setattr(sandbox_agent.pyautogui, "keyUp",
                        lambda *a, **k: calls.append(("keyUp", a, k)))
    monkeypatch.setattr(sandbox_agent.pyautogui, "mouseDown",
                        lambda *a, **k: calls.append(("mouseDown", a, k)))
    monkeypatch.setattr(sandbox_agent.pyautogui, "mouseUp",
                        lambda *a, **k: calls.append(("mouseUp", a, k)))
    monkeypatch.setattr(sandbox_agent.pyautogui, "hotkey",
                        lambda *a, **k: calls.append(("hotkey", a, k)))
    # Avoid real sleeps / cursor movement inside the handler during tests
    monkeypatch.setattr(sandbox_agent.time, "sleep", lambda *a, **k: None)

    body = json.dumps(action_payload).encode()
    h = make_handler(body)
    h.do_POST()
    # send_response prepends the HTTP status headers; the JSON payload is
    # everything after the blank line.
    raw = h.wfile._written.decode("utf-8", errors="replace")
    response = json.loads(raw.split("\r\n\r\n", 1)[-1])
    return response, calls


def test_agent_click_action(monkeypatch):
    result, calls = _invoke({"action": "click", "x": 10, "y": 20}, monkeypatch)
    assert result["status"] == "success"
    kinds = [c[0] for c in calls]
    assert "mouseDown" in kinds and "mouseUp" in kinds
    assert ("moveTo", (10, 20), {"duration": 0.0}) in calls


def test_agent_click_with_modifiers_and_points(monkeypatch):
    result, calls = _invoke({
        "action": "click", "x": 50, "y": 60, "button": "left",
        "modifiers": ["shift"], "points": [[10, 10], [20, 20]],
    }, monkeypatch)
    assert result["status"] == "success"
    kinds = [c[0] for c in calls]
    assert "mouseDown" in kinds and "mouseUp" in kinds
    assert ("keyDown", ("shift",), {}) in calls
    assert ("keyUp", ("shift",), {}) in calls
    assert ("moveTo", (10.0, 10.0), {"duration": 0.001}) in calls


def test_agent_type_action(monkeypatch):
    result, calls = _invoke({"action": "type", "text": "hello"}, monkeypatch)
    assert result["status"] == "success"
    assert calls[0][0] == "write"
    assert calls[0][1][0] == "hello"


def test_agent_hotkey_action(monkeypatch):
    result, calls = _invoke({"action": "hotkey", "keys": ["ctrl", "c"]}, monkeypatch)
    assert result["status"] == "success"
    # keyDown for each key, then keyUp reversed
    assert [c[0] for c in calls] == ["keyDown", "keyDown", "keyUp", "keyUp"]


def test_agent_scroll_action(monkeypatch):
    result, calls = _invoke({"action": "scroll", "amount": -3}, monkeypatch)
    assert result["status"] == "success"
    assert calls[0] == ("scroll", (-3,), {})


def test_agent_drag_drop_action(monkeypatch):
    result, calls = _invoke(
        {"action": "drag_drop", "start_x": 0, "start_y": 0, "end_x": 5, "end_y": 5},
        monkeypatch)
    assert result["status"] == "success"
    kinds = [c[0] for c in calls]
    assert "mouseDown" in kinds and "mouseUp" in kinds


def test_agent_unknown_action_returns_error(monkeypatch):
    result, _ = _invoke({"action": "teleport"}, monkeypatch)
    assert result["status"] == "error"
    assert "Unknown action" in result["message"]


def test_agent_exception_returns_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("hardware failure")

    # Patch manually (not via _invoke, which would override mouseDown)
    monkeypatch.setattr(sandbox_agent.pyautogui, "mouseDown", boom)
    monkeypatch.setattr(sandbox_agent.pyautogui, "moveTo", lambda *a, **k: None)
    monkeypatch.setattr(sandbox_agent.pyautogui, "moveRel", lambda *a, **k: None)
    monkeypatch.setattr(sandbox_agent.time, "sleep", lambda *a, **k: None)
    body = json.dumps({"action": "click", "x": 1, "y": 1}).encode()
    h = make_handler(body)
    h.do_POST()
    raw = h.wfile._written.decode("utf-8", errors="replace")
    response = json.loads(raw.split("\r\n\r\n", 1)[-1])
    assert response["status"] == "error"
    assert "hardware failure" in response["message"]


def test_agent_any_path_is_forgiving(monkeypatch):
    result, _ = _invoke_path(
        {"action": "move", "x": 1, "y": 1}, "/unexpected/path", monkeypatch)
    assert result["status"] == "success"


def test_log_message_suppressed(monkeypatch):
    h = make_handler(b"{}")
    h.log_message("noise %s", "x")  # must not raise
    assert True


def test_agent_exec_code_success(monkeypatch):
    result, _ = _invoke(
        {"action": "exec_code", "code": "result = 6 * 7", "timeout": 5},
        monkeypatch)
    assert result["status"] == "success"
    assert result["ok"] is True
    assert result["result"] == 42


def test_agent_exec_code_captures_stdout(monkeypatch):
    result, _ = _invoke(
        {"action": "exec_code", "code": "print('hello sandbox')", "timeout": 5},
        monkeypatch)
    assert result["ok"] is True
    assert "hello sandbox" in result["stdout"]


def test_agent_exec_code_custom_output_variable(monkeypatch):
    result, _ = _invoke(
        {"action": "exec_code", "code": "my_answer = 7", "output_variable": "my_answer", "timeout": 5},
        monkeypatch)
    assert result["ok"] is True
    assert result["result"] == 7


def test_agent_exec_code_error(monkeypatch):
    result, _ = _invoke(
        {"action": "exec_code", "code": "raise ValueError('boom')", "timeout": 5},
        monkeypatch)
    assert result["status"] == "success"
    assert result["ok"] is False
    assert "boom" in result["error"]


def test_agent_exec_code_non_json_result_uses_repr(monkeypatch):
    result, _ = _invoke(
        {"action": "exec_code", "code": "class X: pass\nresult = X()", "timeout": 5},
        monkeypatch)
    assert result["ok"] is True
    assert isinstance(result["result"], str)


def test_agent_get_position_action(monkeypatch):
    result, _ = _invoke({"action": "get_position"}, monkeypatch)
    assert result["status"] == "success"
    assert result["x"] == 0 and result["y"] == 0  # position() mocked to (0,0)


def test_agent_key_down_up_actions(monkeypatch):
    result, calls = _invoke({"action": "key_down", "key": "ctrl"}, monkeypatch)
    assert result["status"] == "success"
    assert ("keyDown", ("ctrl",), {}) in calls
    result, calls = _invoke({"action": "key_up", "key": "ctrl"}, monkeypatch)
    assert result["status"] == "success"
    assert ("keyUp", ("ctrl",), {}) in calls


def test_agent_release_all_keys(monkeypatch):
    result, calls = _invoke({"action": "release_all_keys"}, monkeypatch)
    assert result["status"] == "success"


def test_agent_key_name_mapping(monkeypatch):
    # Canonical 'cmd' (macOS-style) must map to pyautogui 'win' on Windows
    result, calls = _invoke({"action": "hotkey", "keys": ["cmd", "r"]}, monkeypatch)
    assert result["status"] == "success"
    assert ("keyDown", ("win",), {}) in calls


def test_agent_clipboard_paste_action(monkeypatch):
    monkeypatch.setattr(sandbox_agent, "_set_clipboard_text", lambda t: None)
    result, calls = _invoke(
        {"action": "clipboard_paste", "text": "héllo wörld"}, monkeypatch)
    assert result["status"] == "success"
    assert ("hotkey", ("ctrl", "v"), {}) in calls


def test_agent_screenshot_includes_screen_size(monkeypatch):
    class FakeImg:
        width = 800
        height = 600
        def save(self, buffered, format="PNG"):
            buffered.write(b"png")
    monkeypatch.setattr(sandbox_agent.pyautogui, "screenshot", lambda: FakeImg())
    monkeypatch.setattr(sandbox_agent.pyautogui, "size", lambda: (800, 600))
    result, _ = _invoke({"action": "screenshot"}, monkeypatch)
    assert result["status"] == "success"
    assert result["width"] == 800
    assert result["screen_width"] == 800
    assert result["screen_height"] == 600


def test_agent_rejects_bad_content_length(monkeypatch):
    h = make_handler(b"{}")
    h.headers = {"Content-Length": "0"}
    h.do_POST()
    assert b"400" in h.wfile._written

    h2 = make_handler(b"{}")
    h2.headers = {"Content-Length": "99999999999"}
    h2.do_POST()
    assert b"400" in h2.wfile._written


# ---------------------------------------------------------------------------
# agent_standalone_entry
# ---------------------------------------------------------------------------


def test_bootstrap_direct_engine_success(monkeypatch):
    import sys as _sys
    import AI as _ai_pkg

    class FakeEngine:
        model_path = "m.gguf"
        server_port = 8081

        def stop_server(self):
            pass

    fake_transport = type("T", (), {
        "ensure_started": lambda max_restarts=2: None,
        "get_engine": lambda: FakeEngine(),
    })
    # Patch BOTH resolution paths: the AI package attribute (used by
    # ``from AI import inprocess_transport`` once the module is imported)
    # and sys.modules (used before the first import).
    monkeypatch.setattr(_ai_pkg, "inprocess_transport", fake_transport)
    monkeypatch.setitem(_sys.modules, "AI.inprocess_transport", fake_transport)
    monkeypatch.setattr(ase, "_engine", None)
    monkeypatch.setattr(ase, "_engine_bootstrap_error", "")

    error = ase.bootstrap_direct_engine()
    assert error == ""
    assert ase._engine is not None
    assert __import__("os").environ.get("ARROW_DIRECT_ENGINE") == "1"


def test_bootstrap_direct_engine_failure_sets_error(monkeypatch):
    import sys as _sys
    import AI as _ai_pkg

    class FailingTransport:
        @staticmethod
        def ensure_started(max_restarts=2):
            raise RuntimeError("no model file")

        @staticmethod
        def get_engine():
            raise RuntimeError("no model file")

    monkeypatch.setattr(_ai_pkg, "inprocess_transport", FailingTransport)
    monkeypatch.setitem(_sys.modules, "AI.inprocess_transport", FailingTransport)
    monkeypatch.setattr(ase, "_engine", None)
    monkeypatch.setattr(ase, "_engine_bootstrap_error", "")
    error = ase.bootstrap_direct_engine()
    assert "LLM engine failed to start" in error


def test_shutdown_engine(monkeypatch):
    stopped = []

    class FakeEngine:
        def stop_server(self):
            stopped.append(1)

    monkeypatch.setattr(ase, "_engine", FakeEngine())
    ase.shutdown_engine()
    assert stopped == [1]
    assert ase._engine is None


def test_resolve_dir_source_mode(monkeypatch):
    monkeypatch.setattr(ase.sys, "frozen", False, raising=False)
    result = ase._resolve_dir("chains")
    assert result.endswith("chains")
    assert os.path.isdir(result) or os.path.isabs(result)


# ---------------------------------------------------------------------------
# ExportUtils content generation
# ---------------------------------------------------------------------------


def _make_export_utils():
    eu = ExportUtils.__new__(ExportUtils)
    eu.parent_widget = None
    return eu


def test_batch_content_header_and_structure():
    eu = _make_export_utils()
    content = eu._generate_batch_content_from_config({
        "sequences": [{"name": "s1.json"}],
        "conditional_nodes": [{"condition_type": "presence", "image_path": "a.png"}],
        "llm_nodes": [{"prompt": "hello", "model": "llama3"}],
    })
    assert content.startswith("@echo off")
    assert "s1.json" in content
    assert "--wait-for-image" in content
    assert "--llm-prompt" in content
    assert content.endswith("pause")


def test_batch_content_loop_condition(monkeypatch):
    eu = _make_export_utils()
    monkeypatch.setattr(ExportUtils, "_get_main_script_path", lambda self: "main.py")
    content = eu._generate_batch_content_from_config({
        "conditional_nodes": [{"condition_type": "loop", "max_loops": 5}],
    })
    assert "--loop 5" in content


def test_batch_content_wait_condition():
    eu = _make_export_utils()
    content = eu._generate_batch_content_from_config({
        "conditional_nodes": [{"condition_type": "wait", "wait_time": 7}],
    })
    assert "timeout /t 7 /nobreak" in content


def test_python_content_generation():
    eu = _make_export_utils()
    content = eu._generate_python_content({"sequences": [{"name": "s1.json"}]})
    assert content.startswith("#!/usr/bin/env python3")
    assert "execute_sequence('s1.json')" in content
    assert "def main():" in content


def test_export_formats():
    eu = _make_export_utils()
    formats = eu.get_export_formats()
    assert set(formats) == {"batch", "python", "shell"}


def test_export_chain_writes_file(tmp_path, monkeypatch):
    eu = _make_export_utils()
    monkeypatch.setattr(ExportUtils, "_get_main_script_path", lambda self: "main.py")
    out = tmp_path / "chain.bat"
    assert eu.export_chain({"sequences": []}, str(out)) is True
    assert out.exists()
    assert "Auto-generated LoOper" in out.read_text(encoding="utf-8")
