"""Tests for code-node data-port resolution (code node ports + studio plan).

Covers the Phase 1 runtime contract:
- web ctx_out payloads reach code nodes as RAW variables via the port-scoped
  store (webnodetest-style chains: html = {'page_html': ...}, never None);
- connected-but-undeclared ports are auto-injected as variables;
- reserved execution ports ('input') are never bound as code variables;
- code-node outputs are mirrored into port_store for port-store-first
  consumers (other code nodes, context/output nodes).

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
