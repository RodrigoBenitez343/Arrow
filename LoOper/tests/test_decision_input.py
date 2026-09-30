"""Input node v2 — decision routing, question modes, ask-user v2, media.

Run:  python -m pytest LoOper/tests/test_decision_input.py
"""

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from player.multi_sequence.worflow_interpreter_modules.executor_modules.input_ops import (  # noqa: E402
    InputMixin,
)
from player.multi_sequence.worflow_interpreter_modules.executor_modules.port_store import (  # noqa: E402
    PortScopedStore,
)


class _Harness(InputMixin):
    """Minimal executor: InputMixin + dict-backed llm_executor."""

    def __init__(self):
        self._vars = {}
        self.llm_executor = self
        self.workflow_graph = {}
        self.port_store = PortScopedStore()
        self.chain_id = 'in_test'
        self.llm_script = []
        self.llm_prompts = []
        self._node_execution_count = {}

    # llm_executor duck-type
    def get_variable(self, key):
        return self._vars.get(key)

    def set_variable(self, key, value):
        self._vars[key] = value

    def get_all_variables(self):
        return dict(self._vars)


def _make(script=None):
    h = _Harness()
    if script is not None:
        h.llm_script = list(script)

    def _call(prompt, node_data, stop_flag, max_tokens=64):
        h.llm_prompts.append(prompt)
        return h.llm_script.pop(0) if h.llm_script else None

    h._input_llm_call = _call
    return h


def _input_node(**overrides):
    data = {'node_id': 'in1', 'label': 'q', 'default_value': 'the resolved value'}
    data.update(overrides)
    return {
        'id': 'in1', 'type': 'input', 'data': data,
        'inputs': [], 'connections': {'output': []},
    }


# ---------------------------------------------------------------------------
# Decision routing
# ---------------------------------------------------------------------------


def test_decision_toggle_off_keeps_today_behavior():
    h = _make()
    node = _input_node()
    h._execute_input_node(node, lambda: False)
    assert '_branch_result' not in node
    assert h.port_store.get_output('in_test', 'in1', 'data') == 'the resolved value'


def test_decision_true_routes_and_skips_writing():
    h = _make(['true'])
    node = _input_node(
        decision_mode=True,
        decision_criterion='The input asks for a search',
    )
    h._execute_input_node(node, lambda: False)
    assert node['_branch_result'] is True
    assert h.port_store.get_output('in_test', 'in1', 'decision') is True
    assert h.get_variable('node_in1_decision') is True
    # The value is still stored for downstream consumers.
    assert h.port_store.get_output('in_test', 'in1', 'data') == 'the resolved value'


def test_decision_false_from_llm():
    h = _make(['false'])
    node = _input_node(
        decision_mode=True,
        decision_criterion='The input asks for a search',
    )
    h._execute_input_node(node, lambda: False)
    assert node['_branch_result'] is False


def test_decision_think_wrapped_and_retry():
    # First answer unparseable, retry yields a think-wrapped 'true'.
    h = _make(['banana', '<think>reasoning</think>\ntrue'])
    node = _input_node(
        decision_mode=True,
        decision_criterion='c',
    )
    h._execute_input_node(node, lambda: False)
    assert node['_branch_result'] is True
    assert len(h.llm_prompts) == 2  # one retry


def test_decision_fail_closed_default_false():
    h = _make(['garbage', 'still garbage'])
    node = _input_node(
        decision_mode=True,
        decision_criterion='c',
    )
    h._execute_input_node(node, lambda: False)
    assert node['_branch_result'] is False


def test_decision_fail_closed_honors_configured_default_true():
    h = _make(['garbage', 'still garbage'])
    node = _input_node(
        decision_mode=True,
        decision_criterion='c',
        decision_default=True,
    )
    h._execute_input_node(node, lambda: False)
    assert node['_branch_result'] is True


def test_decision_missing_criterion_fails_closed_without_llm():
    h = _make(['true'])
    node = _input_node(decision_mode=True)
    h._execute_input_node(node, lambda: False)
    assert node['_branch_result'] is False
    assert h.llm_prompts == []


def test_decision_laya_evaluator_used(monkeypatch):
    import AI.laya_client as lc
    monkeypatch.setattr(lc, 'decision', lambda premise, criterion: True)
    h = _make()  # no LLM script needed
    node = _input_node(
        decision_mode=True,
        decision_criterion='c',
        decision_evaluator='laya',
    )
    h._execute_input_node(node, lambda: False)
    assert node['_branch_result'] is True
    assert h.llm_prompts == []


def test_decision_laya_falls_back_to_llm(monkeypatch):
    import AI.laya_client as lc
    monkeypatch.setattr(lc, 'decision', lambda premise, criterion: None)
    h = _make(['true'])
    node = _input_node(
        decision_mode=True,
        decision_criterion='c',
        decision_evaluator='laya',
    )
    h._execute_input_node(node, lambda: False)
    assert node['_branch_result'] is True
    assert len(h.llm_prompts) == 1


# ---------------------------------------------------------------------------
# Parse helpers
# ---------------------------------------------------------------------------


def test_parse_bool_token_table():
    p = InputMixin._parse_bool_token
    assert p('true') is True
    assert p('TRUE.') is True
    assert p('yes') is True
    assert p('No') is False
    assert p('0') is False
    assert p('<think>x</think>false') is False
    assert p('maybe') is None
    assert p('') is None
    assert p(None) is None


def test_normalize_yes_no():
    h = _make()
    assert h._normalize_yes_no('Yes please') is True
    assert h._normalize_yes_no('nope') is False
    assert h._normalize_yes_no('unsure') is None


# ---------------------------------------------------------------------------
# Ask-user v2 + question modes
# ---------------------------------------------------------------------------


def test_ask_v2_legacy_callback_yes_no():
    h = _make()
    h.set_variable('_ask_user_callback', lambda q: 'YES please')
    value, atts = h._ask_user_v2(
        _input_node()['data'], 'Proceed?', 'yes_no', {'text': True}, '',
    )
    assert value == 'yes' and atts == []


def test_ask_v2_legacy_choice_by_index_and_label():
    h = _make()
    data = _input_node(choices=json.dumps([
        {'label': 'Red', 'value': 'red'},
        {'label': 'Blue', 'value': 'blue'},
    ]))['data']
    h.set_variable('_ask_user_callback', lambda q: '2')
    value, _ = h._ask_user_v2(data, 'Pick', 'choice', {'text': True}, '')
    assert value == 'blue'
    h.set_variable('_ask_user_callback', lambda q: 'blue')
    value, _ = h._ask_user_v2(data, 'Pick', 'choice', {'text': True}, '')
    assert value == 'blue'


def test_ask_v2_choice_reasks_then_gives_up():
    h = _make()
    data = _input_node(choices=json.dumps([{'label': 'A'}, {'label': 'B'}]))['data']
    calls = []

    def cb(q):
        calls.append(q)
        return 'purple'

    h.set_variable('_ask_user_callback', cb)
    value, atts = h._ask_user_v2(data, 'Pick', 'choice', {'text': True}, '')
    assert value is None and atts == []
    assert len(calls) == 2  # one re-ask


def test_ask_v2_rich_request_shape_and_response():
    h = _make()
    seen = {}

    def rich(req):
        seen.update(req)
        return {'value': 'apply', 'attachments': []}

    h.set_variable('_ask_user_v2_callback', rich)
    data = _input_node(choices=json.dumps([{'label': 'Apply', 'value': 'apply'}]))['data']
    value, atts = h._ask_user_v2(
        data, 'Do it?', 'choice', {'text': True, 'images': False, 'documents': True}, 'default-x',
    )
    assert value == 'apply' and atts == []
    assert seen['kind'] == 'choice'
    assert seen['choices'][0]['label'] == 'Apply'
    assert seen['accepts'] == {'text': True, 'images': False, 'documents': True}
    assert seen['default'] == 'default-x'


def test_ask_v2_rich_pair_reply_is_unwrapped():
    """The manual-GUI channel returns the v2 PAIR (value, attachments), not a
    dict (see _BgInputDialogHelper.ask_rich).  Stringifying that pair typed the
    literal "(text, [])" on screen instead of the value."""
    h = _make()
    h.set_variable('_ask_user_v2_callback', lambda req: ('apply', []))
    data = _input_node(choices=json.dumps([{'label': 'Apply', 'value': 'apply'}]))['data']
    value, atts = h._ask_user_v2(data, 'Do it?', 'choice', {'text': True}, '')
    assert value == 'apply' and atts == []

    # A cancelled pair (None, []) means no answer - never the literal "(None, [])".
    h.set_variable('_ask_user_v2_callback', lambda req: (None, []))
    value, atts = h._ask_user_v2(data, 'Do it?', 'choice', {'text': True}, '')
    assert value is None and atts == []


def test_route_on_answer_yes_no_drives_branch():
    h = _make()
    h.set_variable('_ask_user_callback', lambda q: 'yes')
    node = _input_node(
        user_prompt='Proceed?',
        question_mode='yes_no',
        route_on_answer=True,
    )
    h._execute_input_node(node, lambda: False)
    assert node['_branch_result'] is True
    assert h.port_store.get_output('in_test', 'in1', 'data') == 'yes'


def test_route_on_answer_unparseable_answer_uses_default_value():
    h = _make()
    h.set_variable('_ask_user_callback', lambda q: 'unsure')
    node = _input_node(
        user_prompt='Proceed?',
        question_mode='yes_no',
        route_on_answer=True,
        default_value='',
    )
    h._execute_input_node(node, lambda: False)
    # No branch result (unusable), value falls back to default ('' stays '').
    assert node.get('_branch_result') is None or '_branch_result' not in node


# ---------------------------------------------------------------------------
# Accepted media
# ---------------------------------------------------------------------------


def test_attachments_rejected_when_media_not_accepted():
    h = _make()
    value = h._apply_input_attachments(
        _input_node()['data'], 'in1', 'base',
        [{'kind': 'document', 'path': 'x.pdf'}],
        {'text': True, 'images': False, 'documents': False},
    )
    assert value == 'base'  # nothing merged
    assert h.get_variable('node_in1_files') is None  # nothing published


def test_document_extraction_merges_text(tmp_path):
    h = _make()
    p = tmp_path / 'notes.txt'
    p.write_text('the secret plan', encoding='utf-8')
    value = h._apply_input_attachments(
        _input_node()['data'], 'in1', 'base',
        [{'kind': 'document', 'path': str(p)}],
        {'text': True, 'images': False, 'documents': True},
    )
    assert 'the secret plan' in value and 'base' in value
    meta = json.loads(h.get_variable('node_in1_files'))
    assert meta['files'][0]['kind'] == 'document'
    assert meta['files'][0]['chars'] > 0


def test_image_attachment_contributes_path_only_when_accepted():
    h = _make()
    value = h._apply_input_attachments(
        _input_node()['data'], 'in1', '',
        [{'kind': 'image', 'path': '/tmp/shot.png'}],
        {'text': True, 'images': True, 'documents': False},
    )
    assert '/tmp/shot.png' in value


def test_extraction_failure_never_raises(tmp_path):
    h = _make()
    bad = tmp_path / 'broken.pdf'
    bad.write_text('not a pdf', encoding='utf-8')
    value = h._apply_input_attachments(
        _input_node()['data'], 'in1', 'base',
        [{'kind': 'document', 'path': str(bad)}],
        {'text': True, 'images': False, 'documents': True},
    )
    assert 'base' in value


def test_attachment_kind_classification():
    from player.qt_input import _attachment_kind
    assert _attachment_kind('shot.png') == 'image'
    assert _attachment_kind('SHOT.JPG') == 'image'
    assert _attachment_kind('doc.pdf') == 'document'
    assert _attachment_kind('notes.docx') == 'document'
    assert _attachment_kind('') == 'text'


# ---------------------------------------------------------------------------
# Carry nodes — pre-collected manual supply + strict fail
# ---------------------------------------------------------------------------


def test_agent_modifiable_input_extracts_parameter_from_the_prompt(monkeypatch):
    """Steering contract for Orchestrator brains: an ``agent_modifiable``
    input inside a mini brain pulls its parameter out of the composed
    goal+progress prompt (the parent's ``_chain_input_context``) in ONE
    extraction call — that is how a brain is steered without the orchestrator
    ever attaching an instruction, and it is what the 'modular tools' story
    rests on.  The input's LABEL is the extraction filter; without upstream
    connections the context comes from ``_chain_input_context`` verbatim.
    """
    import requests

    h = _make()
    h.set_variable('_ask_user_callback', lambda _q: '')  # agent mode
    h.set_variable(
        '_chain_input_context',
        'Long-term goal: find the cheapest 27 inch monitor for the studio\n'
        'Progress so far:\n(nothing yet)\n'
        'Your task: search the web for what the user needs',
    )
    node = _input_node(
        label='Search query', default_value='', agent_modifiable=True,
        passthrough=True,
    )
    captured = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {'response': 'cheapest 27 inch monitor'}

    def _post(url, json=None, timeout=None):
        captured['payload'] = json
        return _Resp()

    monkeypatch.setattr(requests, 'post', _post)
    h._execute_input_node(node, lambda: False)

    blob = json.dumps(captured['payload'])
    assert 'Search query' in blob, 'the label must be the extraction filter'
    assert 'Original user input (from parent chain)' in blob
    assert 'find the cheapest 27 inch monitor' in blob
    # The extracted parameter is what flows downstream (not the whole prompt).
    assert h.port_store.get_output('in_test', 'in1', 'data') == \
        'cheapest 27 inch monitor'


def test_extractor_prefers_structured_goal_and_step(monkeypatch):
    """Agent-modifiable extraction gets a TINY, clean task when the
    orchestrator published the scoping pair (_chain_goal / _chain_step):
    goal + immediate step instead of the raw composed prompt (which made
    small models paste question chunks and carried trace noise)."""
    import requests

    h = _make()
    h.set_variable('_ask_user_callback', lambda _q: '')  # agent mode
    h.set_variable(
        '_chain_input_context',
        'Long-term goal: open linkedin.com and tell me what you see\n'
        'Progress so far:\n1. linkedin_system_chain: Tool ID: 0x1c97db4e650\n'
        'Your next step: Open LinkedIn and search for "Easy Apply" jobs.',
    )
    h.set_variable('_chain_goal', 'open linkedin.com and tell me what you see')
    h.set_variable('_chain_step', 'Open LinkedIn and search for "Easy Apply" jobs.')
    node = _input_node(
        label='a job position from the user', default_value='',
        agent_modifiable=True, passthrough=True,
        user_prompt='what do you want to apply to?',
    )
    captured = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {'response': 'NONE'}

    def _post(url, json=None, timeout=None):
        captured['payload'] = json
        return _Resp()

    monkeypatch.setattr(requests, 'post', _post)
    monkeypatch.setattr(h, '_ask_user_v2', lambda *a, **k: ('', []))
    h._execute_input_node(node, lambda: False)

    blob = json.dumps(captured['payload'])
    assert 'Workflow goal: open linkedin.com' in blob
    assert 'Immediate task for this worker' in blob
    # The raw composed prompt (trace noise included) is not the task anymore.
    assert 'Original user input (from parent chain)' not in blob
    assert '0x1c97db4e650' not in blob


def test_agent_modifiable_without_grounded_value_asks_the_user(monkeypatch):
    """Grounded-or-ask contract: when extraction finds no value the user
    stated, the field's own question is surfaced in the agent chat instead
    of inventing one (regression: the linkedin brain typed a hallucinated
    'search for Easy Apply jobs' that leaked from the orchestrator step)."""
    h = _make()
    h.set_variable('_ask_user_callback', lambda _q: 'IT support role')
    h.set_variable(
        '_chain_input_context',
        'Long-term goal: open linkedin.com and tell me what you see',
    )
    node = _input_node(
        label='a job position from the user', default_value='',
        agent_modifiable=True, user_prompt='what do you want to apply to?',
    )
    monkeypatch.setattr(h, '_reason_input_via_llm', lambda *a, **k: '')
    h._execute_input_node(node, lambda: False)
    assert h.port_store.get_output('in_test', 'in1', 'data') == 'IT support role'


def test_agent_modifiable_miss_without_a_question_carries_the_context(monkeypatch):
    """A plain injection node (label = the input's purpose, user_prompt
    empty) flipped to agent-modifiable must not go EMPTY when the reasoning
    turn finds nothing: with no question to ask, the raw context is carried
    — the node's pre-flag behavior."""
    h = _make()
    h.set_variable('_ask_user_callback', lambda _q: '')  # agent mode
    h.set_variable(
        '_chain_input_context',
        'check if linkedin is currently open in the browser',
    )
    node = _input_node(
        label='what do you want to check from the screen?', default_value='',
        agent_modifiable=True, passthrough=True,
    )
    monkeypatch.setattr(h, '_reason_input_via_llm', lambda *a, **k: '')
    h._execute_input_node(node, lambda: False)
    assert h.port_store.get_output('in_test', 'in1', 'data') == \
        'check if linkedin is currently open in the browser'


def test_agent_modifiable_grounded_value_never_asks(monkeypatch):
    """When the context supplies the value, the node extracts it and never
    interrupts the user."""
    h = _make()
    asked = []
    h.set_variable(
        '_ask_user_callback', lambda q: (asked.append(q), 'unused')[1],
    )
    h.set_variable(
        '_chain_input_context',
        'apply to the IT support role at Acme',
    )
    node = _input_node(
        label='a job position from the user', default_value='',
        agent_modifiable=True, user_prompt='what do you want to apply to?',
    )
    monkeypatch.setattr(h, '_reason_input_via_llm', lambda *a, **k: 'IT support role')
    h._execute_input_node(node, lambda: False)
    assert h.port_store.get_output('in_test', 'in1', 'data') == 'IT support role'
    assert asked == []


def test_carry_consumes_pre_collected_value():
    """A manual GUI run pre-collects entry carry values on the main thread
    (main_window 'Enter value for ...'); the carry branch must consume them
    without upstream or _chain_input_context.  Regression: the orchestrator
    root's goal input failed in manual runs."""
    h = _make()
    h.set_variable('_pre_collected_in1', 'take a note')
    node = _input_node(default_value='')
    h._execute_input_node(node, lambda: False)
    assert h.port_store.get_output('in_test', 'in1', 'data') == 'take a note'


def test_carry_without_any_source_still_fails_for_non_handle_consumers():
    """No pre-collected value, no upstream, no context, no Handle consumer —
    the strict fail is preserved (never a silent empty value)."""
    h = _make()
    node = _input_node(default_value='')  # connections: {'output': []}
    assert h._execute_input_node(node, lambda: False) is None
