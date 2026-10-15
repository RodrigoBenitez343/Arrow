"""Tests for code-node data-port resolution (code node ports + studio plan).

Covers the Phase 1 runtime contract:
- web ctx_out payloads reach code nodes as RAW variables via the port-scoped
  store (webnodetest-style chains: html = {'page_html': ...}, never None);
- connected-but-undeclared ports are auto-injected as variables;
- reserved execution ports ('input') are never bound as code variables;
- code-node outputs are mirrored into port_store for port-store-first
  consumers (other code nodes, context/output nodes);
- the exec edge of the builder's exec+data pair never takes an input_data
  slot, so a single upstream value reaches consumers as a plain scalar.

Run:  python -m pytest LoOper/tests/test_code_node_port_flow.py
"""
import sys

import pytest

from player.multi_sequence.worflow_interpreter_modules.executor_modules.core import WorkflowExecutor


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeLLM:
    """Minimal llm_executor stand-in: a flat variables dict."""

    def __init__(self):
        self.variables = {}
        self.workflow_graph = None

    def get_variable(self, name, default=None):
        return self.variables.get(name, default)

    def set_variable(self, name, value):
        self.variables[name] = value

    def get_all_variables(self):
        return dict(self.variables)


class _StubFallback:
    """fallback_handler with no path_resolver — guarded by hasattr upstream."""


def _make_code_node(code, input_vars=None, output_vars=None, inputs=None):
    return {
        'type': 'code',
        'id': 'code1',
        'node_id': 'code1',
        'data': {
            'code': code,
            'file_path': '',
            'execute_on_input': True,
            'output_variable': 'result',
            'timeout': 10,
            'input_vars': input_vars or [],
            'output_vars': output_vars or [],
        },
        'inputs': inputs or [],
    }


# ---------------------------------------------------------------------------
# Fixture: real WorkflowExecutor, venv/dep machinery stubbed
# ---------------------------------------------------------------------------


@pytest.fixture
def exe(tmp_path):
    executor = WorkflowExecutor({}, None, _FakeLLM(), _StubFallback())
    executor.chain_id = 'test_chain'
    executor._runtime_initialized = True
    executor._chain_runtime_dir = str(tmp_path / 'runtime')
    executor._venv_python = sys.executable
    executor._ensure_chain_venv = lambda: None
    executor._install_code_dependencies = lambda code_str: {
        'installed': [], 'failed': [], 'skipped': [],
        'pip_stdout': '', 'pip_stderr': '',
    }
    return executor


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_web_ctx_out_raw_dict_reaches_declared_port(exe):
    """webnodetest-style wiring: ctx_out {'page_html': ...} -> declared 'html'.

    The dict must arrive whole (raw payload, never unwrapped) — not None.
    """
    exe.port_store.set_output('test_chain', 'ws', 'ctx_out', {'page_html': '<p>x</p>'})
    node = _make_code_node(
        code="result = html['page_html'] if isinstance(html, dict) "
             "else 'NOT_A_DICT:' + type(html).__name__",
        input_vars=[{'name': 'html', 'type': 'string'}],
        inputs=[{'from_node': 'ws', 'output_type': 'ctx_out', 'input_port': 'html'}],
    )
    exe._execute_code_node(node, stop_flag=lambda: False)
    # Raw dict was injected as the declared variable; user code unwrapped it.
    assert isinstance(exe.llm_executor.variables.get('html'), dict)
    assert exe.llm_executor.get_variable('node_code1_result') == '<p>x</p>'


def test_connected_undeclared_port_auto_injected(exe):
    """A connected custom port absent from input_vars is still available."""
    exe.port_store.set_output('test_chain', 'ws', 'ctx_out', {'page_html': '<p>y</p>'})
    node = _make_code_node(
        code="result = html['page_html'] if isinstance(html, dict) else 'missing'",
        input_vars=[],
        inputs=[{'from_node': 'ws', 'output_type': 'ctx_out', 'input_port': 'html'}],
    )
    exe._execute_code_node(node, stop_flag=lambda: False)
    assert exe.llm_executor.get_variable('node_code1_result') == '<p>y</p>'


def test_reserved_input_port_not_bound_as_variable(exe):
    """The base 'input' execution port feeds input_data, never a code var."""
    exe.port_store.set_output('test_chain', 'ws', 'data', 'PAYLOAD')
    node = _make_code_node(
        code="result = 'ok' if 'input' not in globals() and input_data == 'PAYLOAD' else 'bad'",
        input_vars=[],
        inputs=[{'from_node': 'ws', 'output_type': 'data', 'input_port': 'input'}],
    )
    exe._execute_code_node(node, stop_flag=lambda: False)
    assert exe.llm_executor.get_variable('node_code1_result') == 'ok'
    assert 'input' not in exe.llm_executor.variables


def test_code_node_outputs_mirrored_to_port_store(exe):
    """code -> port-store symmetry: output + named output vars are visible."""
    node = _make_code_node(
        code="total = 7\nresult = 'done'",
        output_vars=[{'name': 'total', 'type': 'int'}],
    )
    exe._execute_code_node(node, stop_flag=lambda: False)
    assert exe.port_store.get_output('test_chain', 'code1', 'output') == 'done'
    assert exe.port_store.get_output('test_chain', 'code1', 'total') == 7


def test_code_node_reads_another_code_nodes_port_store_output(exe):
    """A downstream code node consumes an upstream code node's mirrored
    named output through the port store (producer writes only port_store)."""
    upstream = _make_code_node(
        code="parsed = {'page_html': '<p>z</p>'}\nresult = 'ok'",
        output_vars=[{'name': 'parsed', 'type': 'dict'}],
    )
    exe._execute_code_node(upstream, stop_flag=lambda: False)
    downstream = _make_code_node(
        code="result = parsed['page_html'] if isinstance(parsed, dict) else 'missing'",
        input_vars=[],
        inputs=[{'from_node': 'code1', 'output_type': 'parsed', 'input_port': 'parsed'}],
    )
    downstream['id'] = 'code2'
    downstream['node_id'] = 'code2'
    downstream['data']['output_variable'] = 'result'
    exe._execute_code_node(downstream, stop_flag=lambda: False)
    assert exe.llm_executor.get_variable('node_code2_result') == '<p>z</p>'


def test_llm_context_text_reaches_code_verbatim(exe):
    """The LLM 'context' output carries the RESPONSE TEXT (ports.resolve_port;
    the {user_input,response} envelope is internal metadata, never a data
    port).  A code node reads the answer verbatim."""
    exe.llm_executor.set_variable('node_LLM_context', '<h1>page</h1>')
    exe.port_store.set_output('test_chain', 'LLM', 'context', '<h1>page</h1>')
    node = _make_code_node(
        code="result = text",
        input_vars=[{'name': 'text', 'type': 'string'}],
        inputs=[{'from_node': 'LLM', 'output_type': 'context', 'input_port': 'text'}],
    )
    exe._execute_code_node(node, stop_flag=lambda: False)
    assert exe.llm_executor.get_variable('node_code1_result') == '<h1>page</h1>'


def test_code_reads_a_json_answer_verbatim_never_unwrapped(exe):
    """A legitimately JSON answer (the LLM literally returns a {..} object)
    must NOT be shape-guessed: data ports carry the value verbatim."""
    import json as _json
    answer = _json.dumps({'response': 'keep whole', 'x': 1})
    exe.port_store.set_output('test_chain', 'LLM', 'context', answer)
    node = _make_code_node(
        code="result = text",
        input_vars=[{'name': 'text', 'type': 'string'}],
        inputs=[{'from_node': 'LLM', 'output_type': 'context', 'input_port': 'text'}],
    )
    exe._execute_code_node(node, stop_flag=lambda: False)
    assert exe.llm_executor.get_variable('node_code1_result') == answer


def test_plain_ctx_out_text_is_not_unwrapped(exe):
    """A plain (non-JSON) ctx_out value passes through untouched."""
    exe.port_store.set_output('test_chain', 'CTX', 'ctx_out', 'just text')
    node = _make_code_node(
        code="result = text",
        input_vars=[{'name': 'text', 'type': 'string'}],
        inputs=[{'from_node': 'CTX', 'output_type': 'ctx_out', 'input_port': 'text'}],
    )
    exe._execute_code_node(node, stop_flag=lambda: False)
    assert exe.llm_executor.get_variable('node_code1_result') == 'just text'


# ---------------------------------------------------------------------------
# Exec edge + data edge on the same 'input' port (the builder's standard pair)
# ---------------------------------------------------------------------------

# code_ops + conditional_ops both read the producer's named port from the flat
# variables channel, exactly as the builder wires it: one exec edge (no data)
# plus one data edge carrying the value.
_BUILDER_PAIR = [
    {'from_node': 'up', 'output_type': 'output', 'input_port': 'input'},
    {'from_node': 'up', 'output_type': 'result', 'input_port': 'input'},
]


def test_exec_edge_does_not_take_an_input_data_slot(exe):
    """An exec-only edge must not occupy a slot in a code node's input_data.

    Counting it made the ONE upstream value arrive list-wrapped as
    [None, 'true']; a step comparing the text then never matched.
    """
    exe.llm_executor.set_variable('node_up_output_result', 'true')
    node = _make_code_node(
        code="result = type(input_data).__name__ + ':' + str(input_data)",
        inputs=_BUILDER_PAIR,
    )
    exe._execute_code_node(node, stop_flag=lambda: False)
    assert exe.llm_executor.get_variable('node_code1_result') == 'str:true'


def test_code_conditional_takes_the_true_branch_through_a_data_edge(exe):
    """The verdict a producer publishes on a named port reaches the condition
    as a plain scalar, so a loop gated on 'true' can terminate."""
    cond = {
        'condition_type': 'code',
        'code': 'result = str(input_data).strip().lower() == "true"',
        'timeout': 5.0,
        'node_id': 'cond1',
    }
    for verdict, expected in (('false', False), ('true', True)):
        exe.llm_executor.set_variable('node_up_output_result', verdict)
        assert exe._evaluate_code_condition(
            cond, lambda: False, _BUILDER_PAIR) is expected, verdict
