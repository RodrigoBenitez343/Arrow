"""Runtime binding — bind_input / read_output / write_context + input
priority-0 bound values.

Run:  python -m pytest LoOper/tests/test_builder_runtime_binding.py
"""

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from player.builder.devtools import (  # noqa: E402
    execute_runtime_call,
    agent_modifiable_labels,
    resolve_chain_import_file,
)
from player.builder.runtime_tools import (  # noqa: E402
    find_output_node_id,
    read_output_value,
    write_context_value,
)
from player.multi_sequence.worflow_interpreter_modules.executor_modules.input_ops import (  # noqa: E402
    InputMixin,
)


# ---------------------------------------------------------------------------
# bind_input (schema-level)
# ---------------------------------------------------------------------------

def _chain_config_with_inputs(labels, agent_modifiable=True):
    return {
        'input_nodes': [
            {'type': 'input', 'data': {
                'label': label, 'agent_modifiable': agent_modifiable,
                'node_id': 'in%d' % i},
             'node_id': 'in%d' % i}
            for i, label in enumerate(labels)
        ],
        'chain_import_nodes': [],
    }


def _runtime_ctx(chain_config=None, bound=None):
    return {
        'chain_config': chain_config or {},
        'bound': bound if bound is not None else {},
        'read_output': None,
        'write_context': None,
    }


def test_bind_input_writes_known_label():
    ctx = _runtime_ctx(_chain_config_with_inputs(['site', 'query']))
    out = execute_runtime_call(
        'bind_input', {'alias': '', 'label': 'query', 'value': 'devops'},
        ctx)
    assert out == 'bound \'query\' = devops'
    assert ctx['bound']['query'] == 'devops'


def test_bind_input_refuses_unknown_label():
    ctx = _runtime_ctx(_chain_config_with_inputs(['site']))
    out = execute_runtime_call(
        'bind_input', {'alias': '', 'label': 'pages', 'value': '3'}, ctx)
    assert out.startswith('(bind_input:')
    assert 'site' in out
    assert not ctx['bound']


def test_bind_input_refuses_non_agent_modifiable_label():
    cfg = _chain_config_with_inputs(['site'], agent_modifiable=False)
    ctx = _runtime_ctx(cfg)
    out = execute_runtime_call(
        'bind_input', {'alias': '', 'label': 'site', 'value': 'x'}, ctx)
    assert out.startswith('(bind_input:')


def test_agent_modifiable_labels_from_imported_chain(tmp_path):
    sub = tmp_path / 'scrape.json'
    sub.write_text(json.dumps({
        'input_nodes': [
            {'data': {'label': 'site', 'agent_modifiable': True}},
            {'data': {'label': 'depth', 'agent_modifiable': False}},
        ],
    }), encoding='utf-8')
    chain_config = {
        '__chain_file__': str(tmp_path / 'system.json'),
        'chain_import_nodes': [
            {'data': {'prefix': 'scrape', 'chain_file_path': str(sub)}},
        ],
    }
    assert agent_modifiable_labels(chain_config, 'scrape') == ['site']
    assert agent_modifiable_labels(chain_config, 'nope') == []
    assert resolve_chain_import_file(chain_config, 'scrape') == str(sub)
    assert resolve_chain_import_file(chain_config, 'missing') == ''


# ---------------------------------------------------------------------------
# read_output / write_context helpers
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

    def get_variable(self, name, default=None):
        return self.vars.get(name, default)

    def set_variable(self, name, value):
        self.vars[name] = value


class _FakeExecutor:
    def __init__(self):
        self.port_store = _FakePortStore()
        self.llm_executor = _FakeLLMExecutor()
        self.chain_id = 'chain'


def test_find_output_node_by_label_and_variable_name():
    cfg = {'output_nodes': [
        {'data': {'label': 'report', 'variable_name': 'r1', 'node_id': 'o1'},
         'node_id': 'o1'},
        {'data': {'label': 'other', 'variable_name': 'r2', 'node_id': 'o2'},
         'node_id': 'o2'},
    ]}
    assert find_output_node_id(cfg, 'report') == 'o1'
    assert find_output_node_id(cfg, 'r2') == 'o2'
    assert find_output_node_id(cfg, 'missing') == ''


def test_read_output_uses_port_store_first():
    ex = _FakeExecutor()
    ex.port_store.set_output('chain', 'o1', 'output', 'the report')
    cfg = {'output_nodes': [
        {'data': {'label': 'report', 'node_id': 'o1'}, 'node_id': 'o1'}]}
    assert read_output_value(ex, cfg, 'report') == 'the report'


def test_read_output_legacy_variable_fallback():
    ex = _FakeExecutor()
    ex.llm_executor.set_variable('node_o1_output', 'legacy report')
    cfg = {'output_nodes': [
        {'data': {'label': 'report', 'node_id': 'o1'}, 'node_id': 'o1'}]}
    assert read_output_value(ex, cfg, 'report') == 'legacy report'


def test_write_context_stages_and_merges():
    ex = _FakeExecutor()
    assert write_context_value(ex, 'first note') == 'ok'
    assert write_context_value(ex, 'second note') == 'ok'
    staged = ex.llm_executor.get_variable('_chain_bound_context_text')
    assert staged == 'first note\nsecond note'


def test_runtime_tools_never_raise_on_missing_executor():
    assert read_output_value(None, {}, 'x') is None
    assert write_context_value(None, 'x').startswith('no executor')


# ---------------------------------------------------------------------------
# input_ops priority 0: bound values beat LLM reasoning
# ---------------------------------------------------------------------------

class _InputHarness(InputMixin):
    """Binds InputMixin onto a stub (test_handle_input_flow pattern)."""

    def __init__(self):
        self.port_store = _FakePortStore()
        self.chain_id = 'chain'
        self.llm_executor = _FakeLLMExecutor()
        self.workflow_graph = {}
        self._node_execution_count = {}
        self.reason_calls = []

    def _get_input_next_node(self, node, port):
        return '__done__'

    def _reason_input_via_llm(self, node, label, ctx, stop_flag):
        self.reason_calls.append(label)
        return 'LLM-INVENTED'

    def _resolve_input_upstream_value(self, node, port='input'):
        return None

    def _feeds_handle_only(self, node):
        return False


def _bound_input_node(nid='IN', label='site', downstream='OUT'):
    return {
        'type': 'input',
        'id': nid,
        'data': {
            'node_id': nid,
            'label': label,
            'default_value': '',
            'user_prompt': '',
            'passthrough': True,
            'agent_modifiable': True,
        },
        'inputs': [],
        'connections': {'output': [{'node_id': downstream}]},
    }


def test_bound_input_value_beats_llm_reasoning():
    h = _InputHarness()
    h.llm_executor.set_variable('_ask_user_callback', lambda *a, **k: None)
    h.llm_executor.set_variable('_chain_bound_inputs', {'site': 'linkedin.com'})
    h.workflow_graph = {'OUT': {'id': 'OUT', 'type': 'output'}}
    node = _bound_input_node()
    assert h._execute_input_node(node, lambda: False) == '__done__'
    assert h.reason_calls == []  # the LLM path never ran
    assert h.llm_executor.get_variable('node_IN_data') == 'linkedin.com'
    assert h.port_store.get_output('chain', 'IN', 'data') == 'linkedin.com'


def test_agent_modifiable_falls_back_to_llm_when_nothing_bound():
    h = _InputHarness()
    h.llm_executor.set_variable('_ask_user_callback', lambda *a, **k: None)
    h.llm_executor.set_variable('_chain_input_context', 'some context')
    h.workflow_graph = {'OUT': {'id': 'OUT', 'type': 'output'}}
    node = _bound_input_node()
    h._execute_input_node(node, lambda: False)
    assert h.reason_calls == ['site']
    assert h.llm_executor.get_variable('node_IN_data') == 'LLM-INVENTED'
