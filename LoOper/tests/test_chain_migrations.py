"""Tests for the TTS → Output node merge.

Covers the load-time migration module, the shared rich-render helpers, the
audio-mode Output runtime, and the agent reply hygiene that keeps non-text
outputs out of the plain-text response.
"""

import pytest

from player.chain_migrations import migrate_legacy_tts
from NGUI.widgets.rich_render import md_to_html, sanitize_html
from player.multi_sequence.worflow_interpreter_modules.executor_modules.output_ops import OutputMixin
from player.multi_sequence.worflow_interpreter_modules.executor_modules.input_ops import InputMixin
from player.agentic_ops.chain_executor import _build_context_response


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def _legacy_tts(text="hello world", node_id="n1"):
    return {
        "type": "tts",
        "text": text,
        "language": "es",
        "voice_model": "es_ES-carlfm-x_low",
        "speed": 0.8,
        "speaker_id": 2,
        "node_id": node_id,
        "position": [10.0, 20.0],
        "connections": [
            {"output_port": "output", "target_node_id": "n2", "input_port": "input"}
        ],
    }


def test_migrate_top_level_tts_to_audio_output():
    config = {"tts_nodes": [_legacy_tts()]}
    assert migrate_legacy_tts(config) == 1
    assert "tts_nodes" not in config
    out = config["output_nodes"][0]
    assert out["type"] == "output"
    assert out["render_mode"] == "audio"
    assert out["tts_enabled"] is True
    assert out["tts_text"] == "hello world"
    assert out["tts_language"] == "es"
    assert out["tts_voice_model"] == "es_ES-carlfm-x_low"
    assert out["tts_speed"] == 0.8
    assert out["tts_speaker_id"] == 2
    assert out["tts_wait"] is True  # preserve sequential greeting behavior
    assert out["agent_visible"] is False
    assert out["popup_on_finish"] is False
    assert out["node_id"] == "n1"
    assert out["position"] == [10.0, 20.0]
    assert out["connections"][0]["target_node_id"] == "n2"


def test_migrate_is_idempotent():
    config = {"tts_nodes": [_legacy_tts()]}
    migrate_legacy_tts(config)
    assert migrate_legacy_tts(config) == 0
    assert "tts_nodes" not in config
    assert len(config["output_nodes"]) == 1


def test_migrate_recurses_into_chain_import_embedded_config():
    config = {
        "chain_import_nodes": [
            {
                "type": "chain_import",
                "chain_file_path": "demo.json",
                "tts_nodes": [_legacy_tts(text="embedded greeting", node_id="e1")],
                "chain_import_nodes": [],
            }
        ]
    }
    assert migrate_legacy_tts(config) == 1
    embedded = config["chain_import_nodes"][0]
    assert "tts_nodes" not in embedded
    assert embedded["output_nodes"][0]["tts_text"] == "embedded greeting"


def test_migrate_noop_for_empty_and_non_dict():
    assert migrate_legacy_tts({}) == 0
    assert migrate_legacy_tts({"tts_nodes": []}) == 0
    assert migrate_legacy_tts(None) == 0
    assert migrate_legacy_tts(["legacy", "list"]) == 0


# ---------------------------------------------------------------------------
# rich_render
# ---------------------------------------------------------------------------


def test_md_to_html_escapes_raw_html():
    out = md_to_html("<script>alert(1)</script>")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_md_to_html_basic_elements():
    out = md_to_html("# Title\n\n**bold** and `code`\n\n- a\n- b")
    assert "<h1>Title</h1>" in out
    assert "<strong>bold</strong>" in out
    assert "<code>code</code>" in out
    assert "<ul>" in out and "<li>a</li>" in out


def test_sanitize_html_strips_script_and_handlers():
    out = sanitize_html('<p onclick="x()">hi</p><script>bad()</script><img src="data:image/png;base64,AAA" onerror="y()">')
    assert "script" not in out
    assert "onclick" not in out and "onerror" not in out
    assert "<p>hi</p>" in out
    assert 'src="data:image/png;base64,AAA"' in out


# ---------------------------------------------------------------------------
# Output runtime (audio mode) with mocked tts_service
# ---------------------------------------------------------------------------


class _FakePortStore:
    def __init__(self):
        self.data = {}

    def get_output(self, chain_id, node_id, port):
        return self.data.get((chain_id, node_id, port))

    def set_output(self, chain_id, node_id, port, value):
        self.data[(chain_id, node_id, port)] = value


class _FakeLLMExecutor:
    def __init__(self):
        self.vars = {}

    def get_variable(self, name):
        return self.vars.get(name)

    def set_variable(self, name, value):
        self.vars[name] = value


class _FakeOutput(OutputMixin):
    def __init__(self):
        self.port_store = _FakePortStore()
        self.chain_id = "test"
        self.llm_executor = _FakeLLMExecutor()
        self.workflow_graph = {}
        self._on_output_ready = None

    def _get_output_next_node(self, node, port):
        return "__done__"


def test_output_audio_mode_submits_tts(monkeypatch):
    submitted = {}

    def _fake_submit(text, **kwargs):
        submitted["text"] = text
        submitted.update(kwargs)
        return None

    import player.tts_service
    monkeypatch.setattr(player.tts_service, "submit", _fake_submit)

    ex = _FakeOutput()
    node = {
        "id": "out1",
        "data": {
            "render_mode": "audio",
            "tts_enabled": True,
            "tts_text": "greeting",
            "tts_language": "en",
            "tts_wait": False,
        },
        "inputs": [],
        "connections": {},
    }
    ex._execute_output_node(node, lambda: False)

    assert submitted["text"] == "greeting"
    assert submitted["wait"] is False
    meta = ex.port_store.get_output("test", "out1", "output_meta")
    assert meta["render_mode"] == "audio"
    assert meta["spoken_text"] == "greeting"


# ---------------------------------------------------------------------------
# Input runtime (speak user prompt) with mocked tts_service
# ---------------------------------------------------------------------------


class _FakeInput(InputMixin):
    def __init__(self):
        self.port_store = _FakePortStore()
        self.chain_id = "test"
        self.llm_executor = _FakeLLMExecutor()
        self.workflow_graph = {}
        self._node_execution_count = {}

    def _get_input_next_node(self, node, port):
        return "__done__"

    def _resolve_input_upstream_value(self, node, port='input'):
        return None


def test_input_user_prompt_speaks_tts(monkeypatch):
    submitted = {}

    def _fake_submit(text, **kwargs):
        submitted["text"] = text
        submitted.update(kwargs)
        return None

    import player.tts_service
    monkeypatch.setattr(player.tts_service, "submit", _fake_submit)

    ex = _FakeInput()
    ex.llm_executor.vars["_ask_user_callback"] = lambda prompt: "user answer"
    node = {
        "id": "in1",
        "data": {
            "label": "q",
            "user_prompt": "What should I search for?",
            "tts_enabled": True,
            "tts_language": "en",
            "passthrough": True,
        },
        "inputs": [],
        "connections": {},
    }
    ex._execute_input_node(node, lambda: False)

    assert submitted["text"] == "What should I search for?"
    assert submitted["wait"] is False
    # Strict data semantics: the answer is published ONLY on the data port.
    assert ex.port_store.get_output("test", "in1", "data") == "user answer"
    # The base output port stays empty — exec-only edges must not carry it.
    assert ex.port_store.get_output("test", "in1", "output") is None


def test_input_tts_disabled_does_not_speak(monkeypatch):
    called = []

    def _fake_submit(text, **kwargs):
        called.append(text)
        return None

    import player.tts_service
    monkeypatch.setattr(player.tts_service, "submit", _fake_submit)

    ex = _FakeInput()
    ex.llm_executor.vars["_ask_user_callback"] = lambda prompt: "user answer"
    node = {
        "id": "in2",
        "data": {"user_prompt": "hi", "passthrough": True},
        "inputs": [],
        "connections": {},
    }
    ex._execute_input_node(node, lambda: False)
    assert called == []


def test_input_web_mode_types_into_focused_element(monkeypatch):
    """Non-passthrough web mode writes the value into the chain browser's
    focused editable (type_into_focused) instead of the desktop keystroke path."""
    typed = []

    import player.web.actions as web_actions
    import player.multi_sequence.llm_executor_resources.inputs as web_inputs

    monkeypatch.setattr(web_inputs, "_web_driver", lambda: object())
    monkeypatch.setattr(
        web_actions, "type_into_focused", lambda drv, text: typed.append(text) or True
    )

    ex = _FakeInput()
    desktop_calls = []
    ex.action_handlers = type("AH", (), {
        "handle_type_string_action": lambda self, i, a, s=None: desktop_calls.append(a),
    })()
    node = {
        "id": "in3",
        "data": {"default_value": "hello web", "web_mode": True},
        "inputs": [],
        "connections": {},
    }
    ex._execute_input_node(node, lambda: False)

    assert typed == ["hello web"]
    assert desktop_calls == []


def test_input_web_mode_off_always_writes_on_the_desktop(monkeypatch):
    """The toggle is strict: OFF means the desktop, even when the chain drives
    a browser - the page is never written to in that case."""
    typed = []

    import player.web.actions as web_actions
    import player.multi_sequence.llm_executor_resources.inputs as web_inputs

    monkeypatch.setattr(web_inputs, "_web_driver", lambda: object())
    monkeypatch.setattr(
        web_actions, "type_into_focused", lambda drv, text: typed.append(text) or True
    )

    ex = _FakeInput()

    class _Seq:
        _web_session_driver = object()  # a web sequence already ran

    ex.sequence_executor = _Seq()
    desktop_calls = []
    ex.action_handlers = type("AH", (), {
        "handle_type_string_action": lambda self, i, a, s=None: desktop_calls.append(a),
    })()
    node = {
        "id": "in4",
        "data": {"default_value": "hello desktop", "web_mode": False},
        "inputs": [],
        "connections": {},
    }
    ex._execute_input_node(node, lambda: False)

    assert [a["text"] for a in desktop_calls] == ["hello desktop"]
    assert typed == []


# ---------------------------------------------------------------------------
# Agent reply hygiene
# ---------------------------------------------------------------------------


def _executor_with_output(nid, output, meta):
    class _Ex:
        port_store = _FakePortStore()
        chain_id = "test"
        llm_executor = _FakeLLMExecutor()
        workflow_graph = {nid: {"type": "output", "id": nid}}

    ex = _Ex()
    ex.port_store.set_output("test", nid, "output", output)
    ex.port_store.set_output("test", nid, "output_meta", meta)
    return ex


def test_build_context_response_excludes_non_text_outputs():
    ex = _executor_with_output(
        "img1",
        "data:image/png;base64,AAAA",
        {"render_mode": "image", "label": "img"},
    )
    assert _build_context_response(ex, {}, "") == ""


def test_build_context_response_includes_text_outputs():
    ex = _executor_with_output(
        "txt1",
        "plain answer",
        {"render_mode": "text", "label": "txt"},
    )
    assert "plain answer" in _build_context_response(ex, {}, "")
