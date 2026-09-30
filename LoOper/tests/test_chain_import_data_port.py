"""Chain-import output content — selection, delivery, guards.

The chain import has NO dynamic data ports: the imported chain's Output
content is delivered DIRECTLY to the outer connection (the tool consumer /
agent context).  ``emit_data`` + ``data_output_nodes`` select WHICH Output
nodes are delivered.  Covers:
- ``upstream_value`` named-port resolution (base ports carry no data);
- the restore guard: a stale named source port is skipped, never re-wired
  onto the execution port by ``graph_manager.connect_nodes``' fallback;
- OutputNode ``variable_name`` persistence round-trip.

Run:  python -m pytest LoOper/tests/test_chain_import_data_port.py
"""

import json
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from player.multi_sequence.llm_executor_resources.inputs import upstream_value  # noqa: E402
from player.multi_sequence.worflow_interpreter_modules.builder import (  # noqa: E402
    WorkflowGraphBuilder,
)
from player.multi_sequence.worflow_interpreter_modules.executor_modules.core import (  # noqa: E402
    WorkflowExecutor,
)
from player.multi_sequence.worflow_interpreter_modules.executor_modules.port_store import (  # noqa: E402
    PortScopedStore,
)


# ---------------------------------------------------------------------------
# Fakes for executor tests
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


class _StubFallback:
    """fallback_handler with no path_resolver — guarded by hasattr upstream."""


@pytest.fixture
def exe():
    executor = WorkflowExecutor({}, None, _FakeLLM(), _StubFallback())
    executor.chain_id = 'parent_chain'
    return executor


def _sub_player(values):
    """Imported-player stand-in exposing the sub-chain's Output node results."""
    store = PortScopedStore()
    llm = _FakeLLM()
    for nid, val in values.items():
        store.set_output('sub_chain', nid, 'output', val)
        llm.set_variable(f"node_{nid}_output", val)
    we = SimpleNamespace(port_store=store, chain_id='sub_chain', llm_executor=llm)
    return SimpleNamespace(workflow_executor=we)


_SUB_CONFIG = {
    'output_nodes': [
        {'node_id': 'out_1', 'variable_name': 'summary', 'label': 'Summary'},
        {'node_id': 'out_2', 'variable_name': 'report'},
        {'node_id': 'out_3'},  # nameless -> never exposed
    ],
}


def _ci_node(**data):
    payload = {'emit_data': True, 'data_output_nodes': []}
    payload.update(data)
    return {'id': 'ci1', 'node_id': 'ci1', 'data': payload}


# ---------------------------------------------------------------------------
# Executor: chain import
# ---------------------------------------------------------------------------


def test_unresolvable_import_fails_the_node_instead_of_succeeding(exe):
    """A chain import whose file cannot be resolved is a BROKEN STEP: the old
    "continue to the next node" made a whole run of missing imports report
    success end to end (measured 2026-09-23: four unresolvable learned imports
    logged ERRORs and the parent still logged 'executed successfully').  None
    is the engine's failure convention — core stops with 'Node X execution
    failed' and the orchestrator records the step as failed."""
    assert exe._execute_chain_import_node(_ci_node(chain_file_path=''), None) is None
    assert exe._execute_chain_import_node(
        _ci_node(chain_file_path='definitely/not/here_zzz.json'), None) is None


# ---------------------------------------------------------------------------
# Output content delivered to the outer connection (no ports)
# ---------------------------------------------------------------------------


def test_collect_imported_outputs_all_by_default(exe):
    """Every Output node's content is delivered when nothing is selected."""
    player = _sub_player({'out_1': 'HELLO', 'out_2': 'WORLD', 'out_3': 'NO_NAME'})
    assert exe._collect_imported_outputs(player, _SUB_CONFIG) == (
        'Summary: HELLO\nWORLD\nNO_NAME')


def test_collect_imported_outputs_respects_selection(exe):
    """emit_data ON + a selection delivers ONLY the selected Output nodes."""
    player = _sub_player({'out_1': 'HELLO', 'out_2': 'WORLD'})
    node = _ci_node(emit_data=True, data_output_nodes=['out_2'])
    assert exe._collect_imported_outputs(player, _SUB_CONFIG, node) == 'WORLD'


def test_collect_imported_outputs_empty_when_nothing_ran(exe):
    assert exe._collect_imported_outputs(_sub_player({}), _SUB_CONFIG) == ""
    assert exe._collect_imported_outputs(_sub_player({}), {}) == ""
    assert exe._collect_imported_outputs(None, _SUB_CONFIG) == ""



# ---------------------------------------------------------------------------
# upstream_value: named ports + fallback
# ---------------------------------------------------------------------------


def test_upstream_value_named_port_no_generic_fallback():
    assert upstream_value({'node_ci_output_summary': 'S'}, 'ci', 'summary') == 'S'
    # No fallback to the base 'output' channel: base ports carry no data.
    assert upstream_value({'node_ci_output': 'G'}, 'ci', 'summary') is None
    assert upstream_value({'node_ci_output_summary': None, 'node_ci_output': 'G'},
                          'ci', 'summary') is None


def test_upstream_value_existing_channels_unchanged():
    assert upstream_value({'node_ci_data': 'D'}, 'ci', 'data') == 'D'
    assert upstream_value({'node_ci_context': 'C'}, 'ci', 'ctx_out') == 'C'
    # Base 'output' and branch 'true' carry no data (execution only).
    assert upstream_value({'node_ci_output': 'O'}, 'ci', 'output') is None
    assert upstream_value({'node_ci_output': 'O'}, 'ci', 'true') is None


# ---------------------------------------------------------------------------
# Builder: execution bucket vs per-port data buckets
# ---------------------------------------------------------------------------


def test_builder_port_buckets_and_tool_provider():
    ci = {
        'id': 'ci1',
        'node_id': 'ci1',
        'data': {'chain_file_path': 'x.json'},
        'connections': [
            {'output_port': 'output', 'target_node_id': 'llm_1', 'input_port': 'tools'},
            {'output_port': 'summary', 'target_node_id': 'ctx_1', 'input_port': 'input'},
        ],
    }
    llm = {'id': 'llm_1', 'node_id': 'llm_1', 'connections': []}
    ctx = {'id': 'ctx_1', 'node_id': 'ctx_1', 'connections': []}
    graph = WorkflowGraphBuilder(
        chain_import_nodes=[ci], llm_nodes=[llm], context_nodes=[ctx],
    ).build_workflow_graph()

    conns = graph['ci1']['connections']
    assert [c['node_id'] for c in conns['output']] == ['llm_1']
    assert [c['node_id'] for c in conns['summary']] == ['ctx_1']
    # Data-port edges never weaken tool-provider detection (output-only).
    assert graph['ci1']['tool_provider'] is True
    # The consumer resolves the edge with output_type=<port name>.
    assert {'from_node': 'ci1', 'output_type': 'summary', 'input_port': 'input'} \
        in graph['ctx_1']['inputs']


def test_builder_data_port_edges_never_drive_execution():
    ci = {
        'id': 'ci1',
        'node_id': 'ci1',
        'data': {},
        'connections': [
            {'output_port': 'report', 'target_node_id': 'ctx_1', 'input_port': 'input'},
        ],
    }
    ctx = {'id': 'ctx_1', 'node_id': 'ctx_1', 'connections': []}
    graph = WorkflowGraphBuilder(
        chain_import_nodes=[ci], context_nodes=[ctx],
    ).build_workflow_graph()
    conns = graph['ci1']['connections']
    assert conns['output'] == []
    assert [c['node_id'] for c in conns['report']] == ['ctx_1']


# ---------------------------------------------------------------------------
# Port store: node port enumeration
# ---------------------------------------------------------------------------


def test_port_store_get_node_ports():
    store = PortScopedStore()
    store.set_output('c', 'n1', 'output', 'exec')
    store.set_output('c', 'n1', 'result', '-94')
    store.set_output('c', 'n2', 'output', 'other')
    assert store.get_node_ports('c', 'n1') == {'output': 'exec', 'result': '-94'}
    assert store.get_node_ports('c', 'ghost') == {}


# ---------------------------------------------------------------------------
# Context turn: user prompt + tool + result (BASE_SYSTEM_CHAIN pattern)
# ---------------------------------------------------------------------------


class _StubContextDB:
    """ContextDatabase stand-in: no persistence, empty reads."""

    def push(self, **kwargs):
        return None

    def pull(self, **kwargs):
        return {}

    def clear_for_node(self, *args, **kwargs):
        return 0


def _context_node(inputs):
    return {
        'type': 'context',
        'id': 'ctx_1',
        'node_id': 'ctx_1',
        'data': {'clear_on_finish': True, 'max_history': 5},
        'inputs': inputs,
    }


def test_context_turn_records_prompt_tool_and_result(exe, tmp_path):
    """Router pattern: the context node must resolve the user prompt one hop
    through the router and read the chain import's named data ports.

    Regression: the executed-tool detector read the never-published 'output'
    port, so turns carried only the router's 'USE_TOOL:...' text — no tool,
    no result, no user prompt.  The executor now publishes the tool's
    readable description (never the routing key), so the decision line must
    not duplicate the tool line.
    """
    tool_chain = tmp_path / "calculator.json"
    tool_chain.write_text(json.dumps({
        "description": "on-demand arithmetic operations",
        "output_nodes": [],
    }), encoding="utf-8")
    graph = {
        'llm_1': {
            'type': 'llm',
            'inputs': [
                {'from_node': 'in_1', 'output_type': 'data', 'input_port': 'context'},
                {'from_node': 'ci_1', 'output_type': 'output', 'input_port': 'tools'},
            ],
            'connections': {},
        },
        'in_1': {'type': 'input', 'inputs': [], 'connections': {}},
        'ci_1': {
            'type': 'chain_import',
            'data': {'chain_file_path': str(tool_chain), 'prefix': 'calculator'},
            'inputs': [],
            'connections': {},
        },
    }
    exe.workflow_graph = graph
    exe.llm_executor.workflow_graph = graph
    exe._context_db = _StubContextDB()
    exe.port_store.set_output('parent_chain', 'in_1', 'data', 'calculate 5 + 3')
    # The executor publishes only the tool's DESCRIPTION (routing ids and
    # aliases never surface — executor._tool_selection_text).
    exe.port_store.set_output(
        'parent_chain', 'llm_1', 'context',
        'on-demand arithmetic operations',
    )
    exe.port_store.set_output('parent_chain', 'ci_1', 'result', '-94')

    exe._execute_context_node(
        _context_node([
            {'from_node': 'llm_1', 'output_type': 'context', 'input_port': 'ctx_in'},
            {'from_node': 'ci_1', 'output_type': 'result', 'input_port': 'ctx_in'},
        ]),
        stop_flag=lambda: False,
    )

    turns = exe.port_store.get_turns('parent_chain', node_id='ctx_1')
    assert turns, 'context node recorded no turn'
    turn = turns[-1]
    assert turn['query'] == 'calculate 5 + 3'
    assert turn['tool'] == 'calculator: on-demand arithmetic operations'
    assert '-94' in turn['result']
    # The published description duplicates the tool line -> deduped away.
    assert turn['decision'] == ''


def test_context_turn_keeps_non_tool_reply_as_decision(exe):
    """A direct LLM reply (not a tool selection) still lands as the decision."""
    graph = {
        'llm_1': {'type': 'llm', 'inputs': [], 'connections': {}},
        'ci_1': {
            'type': 'chain_import',
            'data': {'chain_file_path': '', 'prefix': 'calculator'},
            'inputs': [],
            'connections': {},
        },
    }
    exe.workflow_graph = graph
    exe.llm_executor.workflow_graph = graph
    exe._context_db = _StubContextDB()
    exe.port_store.set_output('parent_chain', 'llm_1', 'context', 'Sure, done.')
    exe.port_store.set_output('parent_chain', 'ci_1', 'result', '-94')

    exe._execute_context_node(
        _context_node([
            {'from_node': 'llm_1', 'output_type': 'context', 'input_port': 'ctx_in'},
            {'from_node': 'ci_1', 'output_type': 'result', 'input_port': 'ctx_in'},
        ]),
        stop_flag=lambda: False,
    )

    turn = exe.port_store.get_turns('parent_chain', node_id='ctx_1')[-1]
    assert turn['decision'] == 'Sure, done.'


def test_context_turn_query_falls_back_to_first_upstream(exe):
    """Without Input nodes in reach, the legacy first-upstream query is kept."""
    graph = {'llm_1': {'type': 'llm', 'inputs': [], 'connections': {}}}
    exe.workflow_graph = graph
    exe.llm_executor.workflow_graph = graph
    exe._context_db = _StubContextDB()
    exe.port_store.set_output('parent_chain', 'llm_1', 'context', 'hello world')

    exe._execute_context_node(
        _context_node([
            {'from_node': 'llm_1', 'output_type': 'context', 'input_port': 'ctx_in'},
        ]),
        stop_flag=lambda: False,
    )

    turn = exe.port_store.get_turns('parent_chain', node_id='ctx_1')[-1]
    assert turn['query'] == 'hello world'


# ---------------------------------------------------------------------------
# Qt node tests (offscreen)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def qapp():
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def _make_graph():
    from NodeGraphQt import NodeGraph
    from NGUI.nodes_resources.chain_import_node import ChainImportNode

    graph = NodeGraph()
    graph.register_node(ChainImportNode)
    return graph


def _chain_import_node(graph=None):
    graph = graph or _make_graph()
    return graph.create_node("chain_import.ChainImportNode", name="Chain Import", pos=[0, 0])


def _write_chain(tmp_path, output_nodes, name="tool.json"):
    chain = tmp_path / name
    chain.write_text(json.dumps({"output_nodes": output_nodes}), encoding="utf-8")
    return str(chain)


def _save_config(node):
    from NGUI.graph_elements.config_manager import ConfigManager

    cm = ConfigManager(type("P", (), {})())
    cm._get_node_connections = lambda n: []
    cm._save_chain_import_node(node)
    return cm.chain_config["chain_import_nodes"][-1]


def test_restore_guard_skips_missing_named_port(qapp):
    """graph_manager.connect_nodes falls back to output port #0 on a missing
    name — the restore path must skip the connection instead."""
    from NGUI.graph_elements.config_manager import ConfigManager

    connected = []

    class _Port:
        def __init__(self, name):
            self._name = name

        def name(self):
            return self._name

    class _Node:
        def __init__(self, node_id, out_ports):
            self.id = node_id
            self._out = [_Port(n) for n in out_ports]

        def output_ports(self):
            return self._out

    class _GM:
        def connect_nodes(self, src, dst, out_port, in_port):
            connected.append((src.id, out_port))
            return True

    cm = ConfigManager(SimpleNamespace(graph_manager=_GM()))
    cm.chain_config = {
        'chain_import_nodes': [{
            'node_id': 'ci1',
            'type': 'chain_import',
            'connections': [
                {'output_port': 'summary', 'target_node_id': 'llm1', 'input_port': 'input'},
                {'output_port': 'stale_port', 'target_node_id': 'llm1', 'input_port': 'input'},
            ],
        }],
    }
    node_map = {
        'ci1': _Node('ci1', ['output', 'summary']),
        'llm1': _Node('llm1', []),
    }
    cm._restore_connections(node_map)
    assert connected == [('ci1', 'summary')]


def test_emit_data_round_trips_through_save(qapp, tmp_path):
    chain = _write_chain(tmp_path, [{'node_id': 'out_1', 'variable_name': 'summary'}])
    node = _chain_import_node()
    node.set_chain_import_data(chain)
    node.set_property('emit_data', 'true')
    node.set_property('data_output_nodes', json.dumps(['out_1']))

    cfg = node.get_chain_import_config()
    assert cfg['emit_data'] is True
    assert cfg['data_output_nodes'] == ['out_1']

    saved = _save_config(node)
    assert saved['emit_data'] is True
    assert saved['data_output_nodes'] == ['out_1']


def test_output_variable_name_round_trips(qapp):
    from NodeGraphQt import NodeGraph
    from NGUI.graph_elements.config_manager import ConfigManager
    from NGUI.nodes_resources.output_node import OutputNode

    graph = NodeGraph()
    graph.register_node(OutputNode)
    node = graph.create_node("output.OutputNode", name="Output", pos=[0, 0])
    node.set_property("variable_name", "summary")
    assert node.get_output_config()["variable_name"] == "summary"

    cm = ConfigManager(type("P", (), {})())
    cm._get_node_connections = lambda n: []
    cm._save_output_node(node)
    saved = cm.chain_config["output_nodes"][-1]
    assert saved["variable_name"] == "summary"


def test_edit_output_node_applies_variable_name(qapp, monkeypatch):
    """Regression: closing the Output dialog must persist variable_name.

    ``edit_output_node`` gathers the current config and writes the dialog
    result back with EXPLICIT property lists — a missing key silently drops
    the setting on close.  This pins both directions (prefill + apply).
    """
    import NGUI.graph_elements.node_operations_modules.output as out_ops_mod
    from NodeGraphQt import NodeGraph
    from NGUI.nodes_resources.output_node import OutputNode

    graph = NodeGraph()
    graph.register_node(OutputNode)
    node = graph.create_node("output.OutputNode", name="Output", pos=[0, 0])

    seen = {}

    class _FakeDialog:
        Accepted = 1

        def __init__(self, parent, config):
            seen.clear()
            seen.update(config)

        def exec_(self):
            return self.Accepted

        def get_config(self):
            cfg = dict(seen)
            cfg["variable_name"] = "summary"
            return cfg

    monkeypatch.setattr(out_ops_mod, "OutputPropertiesDialog", _FakeDialog)

    ops = out_ops_mod.OutputOperationsMixin()
    ops.parent_widget = SimpleNamespace()
    ops.edit_output_node(node)
    assert node.get_property("variable_name") == "summary"

    # Second pass: the dialog must be prefilled with the stored name.
    ops.edit_output_node(node)
    assert seen.get("variable_name") == "summary"
