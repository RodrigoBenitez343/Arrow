"""Output node overlay toggle: silent in chat, still stored (memory records).

Run:  python -m pytest LoOper/tests/test_output_overlay_visibility.py
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from player.agentic_ops.chain_executor import _build_context_response  # noqa: E402
from player.multi_sequence.worflow_interpreter_modules.executor_modules.output_ops import (  # noqa: E402
    OutputMixin,
)
from player.multi_sequence.worflow_interpreter_modules.executor_modules.port_store import (  # noqa: E402
    PortScopedStore,
)


class _Harness(OutputMixin):
    """OutputMixin over a dict-backed llm_executor + real port store."""

    def __init__(self, agent_mode=True):
        self.calls = []
        self._vars = {}
        if agent_mode:
            # Agent mode is detected by output_ops via this callback being set.
            self._vars['_ask_user_callback'] = lambda q: ''
        self.llm_executor = self
        self.port_store = PortScopedStore()
        self.chain_id = 't'
        self.workflow_graph = {}

    def get_variable(self, key):
        return self._vars.get(key)

    def set_variable(self, key, value):
        self._vars[key] = value

    def _on_output_ready(self, label, content, **kw):
        self.calls.append({'label': label, 'content': content, **kw})


def _node(overlay_visible=True):
    return {
        'id': '0xout', 'type': 'output',
        'data': {
            'label': 'Summary', 'overlay_visible': overlay_visible,
            'popup_on_finish': True, 'render_mode': 'text', 'show_rating': True,
        },
        'inputs': [{
            'from_node': '0xup', 'input_port': 'input', 'output_type': 'data',
        }],
        'connections': {},
    }


def _seed_upstream(h):
    h.port_store.set_output('t', '0xup', 'data', 'hello world')


def test_overlay_visible_false_is_silent_but_stored():
    h = _Harness(agent_mode=True)
    _seed_upstream(h)
    out = h._execute_output_node(_node(overlay_visible=False), None)

    assert out == '__done__'
    assert h.calls == []  # nothing posted to the overlay
    # Still stored for downstream + the linear activity memory (core hook
    # reads these ports and records the node regardless of overlay posting).
    assert h.port_store.get_output('t', '0xout', 'output') == 'hello world'
    meta = h.port_store.get_output('t', '0xout', 'output_meta')
    assert meta and meta['overlay_visible'] is False
    # Mid-execution marker NOT set: nothing was shown.
    assert h.get_variable('_agent_mid_execution_outputs_shown') is None


def test_overlay_visible_true_posts_and_marks_mid_shown():
    h = _Harness(agent_mode=True)
    _seed_upstream(h)
    h._execute_output_node(_node(overlay_visible=True), None)

    assert len(h.calls) == 1
    assert h.calls[0]['label'] == 'Summary'
    assert h.calls[0]['content'] == 'hello world'
    assert h.calls[0]['show_rating'] is True
    assert h.get_variable('_agent_mid_execution_outputs_shown') is True


def test_manual_mode_popup_unaffected_by_overlay_toggle():
    h = _Harness(agent_mode=False)  # no ask callback -> manual mode
    _seed_upstream(h)
    h._execute_output_node(_node(overlay_visible=False), None)

    # popup_on_finish still drives the manual popup; the overlay toggle
    # only silences agent-mode chat posting.
    assert len(h.calls) == 1
    assert h.get_variable('_agent_mid_execution_outputs_shown') is None


def _static_node(overlay_visible=True):
    """A dead-end Output node carrying only its predefined static text."""
    return {
        'id': '0xout', 'type': 'output',
        'data': {
            'label': 'Start', 'overlay_visible': overlay_visible,
            'popup_on_finish': False, 'render_mode': 'text',
            'show_rating': True, 'tts_text': 'starting page filling',
        },
        'inputs': [{
            'from_node': '0xup', 'input_port': 'input', 'output_type': 'data',
        }],
        'connections': {},
    }


def test_static_text_output_posts_to_agent_chat():
    # A branch/dead-end Output node has no collected data (nothing upstream):
    # only its predefined static text.  It must still post to the agent chat
    # (desktop overlay + every web client) instead of reaching only memory.
    # popup_on_finish is False on purpose: agent posting must not depend on a
    # manual-mode toggle.
    h = _Harness(agent_mode=True)
    h._execute_output_node(_static_node(), None)

    assert len(h.calls) == 1
    assert h.calls[0]['label'] == 'Start'
    assert h.calls[0]['content'] == 'starting page filling'
    assert h.get_variable('_agent_mid_execution_outputs_shown') is True


def test_static_text_output_respects_overlay_silence():
    h = _Harness(agent_mode=True)
    h._execute_output_node(_static_node(overlay_visible=False), None)
    assert h.calls == []


class _FakeExec:
    """Minimal executor for _build_context_response (final-reply builder)."""

    def __init__(self, overlay_visible):
        self.chain_id = 't'
        self.port_store = PortScopedStore()
        self.workflow_graph = {
            '0xout': {'type': 'output', 'id': '0xout', 'data': {}},
        }
        self.llm_executor = self
        self._vars = {}
        self.port_store.set_output('t', '0xout', 'output', 'final text')
        self.port_store.set_output('t', '0xout', 'output_meta', {
            'render_mode': 'text', 'overlay_visible': overlay_visible,
        })

    def get_variable(self, key):
        return self._vars.get(key)


def test_final_reply_skips_silenced_output():
    assert _build_context_response(
        _FakeExec(overlay_visible=False), {}, '') == ''


def test_final_reply_uses_visible_output():
    assert _build_context_response(
        _FakeExec(overlay_visible=True), {}, '') == 'final text'
