"""Generic tool-provider gating (task #1) — builder post-pass + scheduler gates.

The ``tool_provider`` flag replaced the old chain_import/code-only detectors:
ANY node whose ONLY execution edges land on a router input port (llm 'tools' /
orchestrator 'brains'|'chains') is a SUBROUTINE and must never run in the main
workflow loop.  These tests are the first coverage of the dependencies/core
consumers (the scheduler had none before).

Run:  python -m pytest LoOper/tests/test_tool_provider_flag.py
"""

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from player.multi_sequence.worflow_interpreter_modules.builder import (  # noqa: E402
    WorkflowGraphBuilder,
)
from player.multi_sequence.worflow_interpreter_modules.executor_modules.core import (  # noqa: E402
    WorkflowExecutor,
)
from player.multi_sequence.worflow_interpreter_modules.executor_modules.dependencies import (  # noqa: E402
    DependenciesMixin,
)

_CHAINS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "chains",
)


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


class _Deps(DependenciesMixin):
    """Scheduler-only harness (the consumers under test)."""

    def __init__(self, graph):
        self.workflow_graph = graph
        self.skipped_nodes = set()
        self.llm_executor = _FakeLLM()


def _build(**kw):
    return WorkflowGraphBuilder(**kw).build_workflow_graph()


def _edges_to(node, target_id, port, output_port='output'):
    return [{'target_node_id': target_id, 'output_port': output_port,
             'input_port': port}]


# ---------------------------------------------------------------------------
# Builder post-pass — the generic flag
# ---------------------------------------------------------------------------


def test_postpass_flags_mcp_wired_only_to_tools():
    """The concrete shipped case: testmcp.json's mcp tools."""
    graph = _build(
        llm_nodes=[{'node_id': 'L1', 'prompt': 'p', 'connections': []}],
        mcp_nodes=[{'node_id': 'M1', 'tool_name': 'browser_close',
                    'connections': _edges_to({}, 'L1', 'tools')}],
    )
    assert graph['M1']['tool_provider'] is True


def test_postpass_flags_sequence_wired_only_to_brains():
    graph = _build(
        orchestrator_nodes=[{'node_id': 'O1', 'connections': []}],
        sequences=[{'node_id': 'S1', 'connections': _edges_to({}, 'O1', 'brains')}],
    )
    assert graph['S1']['tool_provider'] is True


def test_postpass_flags_future_chains_port():
    """The 'chains' port does not exist yet — the predicate is groundwork."""
    graph = _build(
        orchestrator_nodes=[{'node_id': 'O1', 'connections': []}],
        sequences=[{'node_id': 'S1', 'connections': _edges_to({}, 'O1', 'chains')}],
    )
    assert graph['S1']['tool_provider'] is True


def test_postpass_ignores_mixed_edges():
    """One router edge + one normal edge = a real step, not a subroutine."""
    graph = _build(
        llm_nodes=[{'node_id': 'L1', 'prompt': 'p', 'connections': []}],
        output_nodes=[{'node_id': 'OUT', 'connections': []}],
        mcp_nodes=[{'node_id': 'M1', 'connections': (
            _edges_to({}, 'L1', 'tools') + _edges_to({}, 'OUT', 'input')
        )}],
    )
    assert not graph['M1'].get('tool_provider')


def test_postpass_ignores_zero_edge_node():
    graph = _build(mcp_nodes=[{'node_id': 'M1'}])
    assert not graph['M1'].get('tool_provider')


def test_postpass_flags_code_wired_only_to_tools():
    """Regression: the old code-node detector keeps working, generically."""
    graph = _build(
        llm_nodes=[{'node_id': 'L1', 'prompt': 'p', 'connections': []}],
        code_nodes=[{'node_id': 'C1', 'code': 'result=1',
                     'connections': _edges_to({}, 'L1', 'tools')}],
    )
    assert graph['C1']['tool_provider'] is True


def test_chain_import_brains_still_flagged_and_marked():
    """Regression pin: chain_import keeps the flag AND the selected_by_llm key."""
    graph = _build(
        orchestrator_nodes=[{'node_id': 'O1', 'connections': []}],
        chain_import_nodes=[{
            'node_id': 'B1', 'chain_file_path': 'chains/x.json',
            'connections': _edges_to({}, 'O1', 'brains'),
        }],
    )
    assert graph['B1']['tool_provider'] is True
    assert graph['B1']['selected_by_llm'] is False


def test_data_port_edges_do_not_weaken_the_flag():
    """A named data port (emit_data) is not an execution edge."""
    graph = _build(
        llm_nodes=[{'node_id': 'L1', 'prompt': 'p', 'connections': []}],
        context_nodes=[{'node_id': 'CTX', 'connections': []}],
        chain_import_nodes=[{
            'node_id': 'CI1', 'chain_file_path': 'chains/x.json',
            'connections': (
                _edges_to({}, 'L1', 'tools')
                + _edges_to({}, 'CTX', 'ctx_in', output_port='summary')
            ),
        }],
    )
    assert graph['CI1']['tool_provider'] is True


# ---------------------------------------------------------------------------
# Scheduler gates (dependencies.py)
# ---------------------------------------------------------------------------


def test_flagged_node_is_never_a_starting_node():
    graph = {
        'H1': {'type': 'handle', 'tool_provider': True,
               'connections': {}, 'data': {}},
        'S1': {'type': 'sequence', 'tool_provider': True,
               'connections': {'output': []}, 'data': {}},
    }
    assert _Deps(graph)._find_all_starting_nodes() == []


def test_unflagged_zero_input_node_is_still_a_starting_node():
    graph = {'A': {'type': 'handle', 'connections': {}, 'data': {}}}
    assert _Deps(graph)._find_all_starting_nodes() == ['A']


def test_flagged_node_never_ready_even_when_selected():
    """selected_by_llm is a dispatch/reporting marker, NOT an execution grant."""
    graph = {
        'H1': {'type': 'handle', 'tool_provider': True, 'selected_by_llm': True,
               'connections': {}, 'inputs': [], 'data': {}},
    }
    assert _Deps(graph)._are_all_dependencies_satisfied('H1', set()) is False


def test_llm_with_only_flagged_tool_sources_is_a_starting_node():
    graph = {
        'M1': {'type': 'mcp', 'tool_provider': True, 'data': {},
               'connections': {'output': [
                   {'node_id': 'L1', 'input_port': 'tools'}]}},
        'L1': {'type': 'llm', 'connections': {}, 'data': {},
               'inputs': [{'from_node': 'M1', 'input_port': 'tools'}]},
    }
    assert _Deps(graph)._find_all_starting_nodes() == ['L1']


# ---------------------------------------------------------------------------
# Full runs (core.py): a flagged node never executes and never stalls
# ---------------------------------------------------------------------------


def _executor(graph):
    ex = WorkflowExecutor(graph, None, _FakeLLM(), _StubFallback())
    ex.chain_id = 'flag_test'
    ex._context_db = None
    return ex


def test_flagged_zero_input_provider_never_runs_and_run_completes(monkeypatch):
    ran = []
    graph = {
        'A': {'type': 'code', 'id': 'A', 'node_id': 'A',
              'data': {'code': 'result=1'}, 'inputs': [],
              'connections': {'output': [
                  {'node_id': 'OUT', 'input_port': 'input'}]}},
        'OUT': {'type': 'output', 'id': 'OUT', 'node_id': 'OUT', 'data': {},
                'inputs': [{'from_node': 'A', 'input_port': 'input'}],
                'connections': {}},
        # A router subroutine with no graph inputs — gated by the builder.
        'H': {'type': 'handle', 'id': 'H', 'node_id': 'H', 'data': {},
              'tool_provider': True, 'connections': {}},
    }
    ex = _executor(graph)
    monkeypatch.setattr(
        ex, '_execute_code_node',
        lambda node, stop_flag=None, *a, **k: '__done__',
    )
    monkeypatch.setattr(
        ex, '_execute_output_node',
        lambda node, stop_flag=None, *a, **k: '__done__',
    )
    monkeypatch.setattr(
        ex, '_execute_handle_node',
        lambda node, stop_flag=None, *a, **k: ran.append(node.get('id')),
    )

    result = ex.execute_workflow(stop_flag=lambda: False)

    assert result is True, "a flagged provider must not stall the run"
    assert ran == [], "a flagged provider must never run standalone"


def test_a7_still_auto_completes_unflagged_legacy_provider(monkeypatch):
    """Union pin: the legacy type tuple still settles an UNFLAGGED leftover.

    The conditional chose 'true' but the chain_import on that branch was never
    queued (no branch decision reached it); a7 must auto-complete it instead of
    running it standalone — the pre-task#1 behavior, preserved by the union.
    """
    ran = []
    graph = {
        'A': {'type': 'code', 'id': 'A', 'node_id': 'A',
              'data': {'code': 'result=1'}, 'inputs': [],
              'connections': {'output': [
                  {'node_id': 'B', 'input_port': 'input'}]}},
        'B': {'type': 'conditional', 'id': 'B', 'node_id': 'B',
              'data': {'condition_type': 'code', 'code': 'result=False'},
              'inputs': [{'from_node': 'A', 'input_port': 'input'}],
              'connections': {'true': [
                  {'node_id': 'CI', 'input_port': 'input'}]}},
        'CI': {'type': 'chain_import', 'id': 'CI', 'node_id': 'CI',
               'data': {'chain_file_path': 'no_such_chain.json'},
               'inputs': [{'from_node': 'B', 'input_port': 'input'}],
               'connections': {}},
    }
    ex = _executor(graph)
    monkeypatch.setattr(
        ex, '_execute_code_node',
        lambda node, stop_flag=None, *a, **k: '__done__',
    )
    monkeypatch.setattr(ex, '_evaluate_conditional', lambda *a, **k: False)
    monkeypatch.setattr(
        ex, '_execute_chain_import_node',
        lambda node, stop_flag=None, *a, **k: ran.append(node.get('id')),
    )

    result = ex.execute_workflow(stop_flag=lambda: False)

    assert result is True
    assert ran == [], "a7 must settle the leftover, not run it"


# ---------------------------------------------------------------------------
# Authored descriptions survive the chain JSON (task #2)
# ---------------------------------------------------------------------------


def test_sequence_description_survives_chain_json_roundtrip():
    """The dialogs' description field is persisted as the node dict's
    'description' key — the exact text the orchestrator routes node brains by."""
    cfg = {
        'orchestrator_nodes': [{'node_id': 'O1', 'connections': []}],
        'sequences': [{
            'node_id': 'S1',
            'sequence_file': 'rec.json',
            'description': 'Open the jobs page',
            'connections': [{'target_node_id': 'O1', 'output_port': 'output',
                             'input_port': 'brains'}],
        }],
    }
    graph = _build_from_cfg(cfg)

    assert graph['S1']['data']['description'] == 'Open the jobs page'
    assert graph['S1']['tool_provider'] is True


def test_conditional_and_web_sequence_descriptions_survive_chain_json():
    cfg = {
        'orchestrator_nodes': [{'node_id': 'O1', 'connections': []}],
        'web_sequences': [{
            'node_id': 'W1', 'session_file': 'search.json',
            'description': 'Search the job board',
            'connections': [{'target_node_id': 'O1', 'output_port': 'output',
                             'input_port': 'brains'}],
        }],
        'conditional_nodes': [{
            'node_id': 'C1', 'condition_type': 'presence',
            'description': 'Is the Apply button visible?',
            'connections': [{'target_node_id': 'O1', 'output_port': 'output',
                             'input_port': 'brains'}],
        }],
    }
    graph = _build_from_cfg(cfg)

    assert graph['W1']['data']['description'] == 'Search the job board'
    assert graph['C1']['data']['description'] == 'Is the Apply button visible?'
    assert graph['W1']['tool_provider'] is True
    assert graph['C1']['tool_provider'] is True


# ---------------------------------------------------------------------------
# Chain classification (task #4): brain vs deterministic chain
# ---------------------------------------------------------------------------


def test_chain_classification_is_transitive(tmp_path):
    from player.chain_import_ports import (
        chain_contains_orchestrator, classify_chain,
    )

    plain = tmp_path / 'lib.json'
    plain.write_text(json.dumps({'sequences': [{'node_id': 's1'}]}), encoding='utf-8')
    brain = tmp_path / 'brain.json'
    brain.write_text(
        json.dumps({'orchestrator_nodes': [{'node_id': 'o1'}]}), encoding='utf-8')
    wrapper = tmp_path / 'wrapper.json'
    wrapper.write_text(json.dumps({'chain_import_nodes': [
        {'node_id': 'c1', 'chain_file_path': str(brain)}]}), encoding='utf-8')

    assert classify_chain(
        json.loads(plain.read_text(encoding='utf-8')), str(tmp_path)) == 'chain'
    assert classify_chain(
        json.loads(brain.read_text(encoding='utf-8')), str(tmp_path)) == 'brain'
    # Transitive: a chain that imports a brain chain is itself a brain.
    assert classify_chain(
        json.loads(wrapper.read_text(encoding='utf-8')), str(tmp_path)) == 'brain'
    # Broken/missing imports never raise and never classify as a brain.
    broken = {'chain_import_nodes': [
        {'chain_file_path': str(tmp_path / 'nope.json')}]}
    assert chain_contains_orchestrator(broken, str(tmp_path)) is False


def test_shipped_entry_chain_is_a_brain_and_libraries_are_not():
    from player.chain_import_ports import classify_chain

    with open(os.path.join(_CHAINS_DIR, 'ORCHESTRATOR.json'),
              encoding='utf-8') as f:
        orch = json.load(f)
    with open(os.path.join(_CHAINS_DIR, 'CLOSE.json'), encoding='utf-8') as f:
        close = json.load(f)

    assert classify_chain(orch, _CHAINS_DIR) == 'brain'
    assert classify_chain(close, _CHAINS_DIR) == 'chain'


# ---------------------------------------------------------------------------
# Shipped chains: bounded behavior change + exclusivity invariant
# ---------------------------------------------------------------------------


def _iter_shipped_chains():
    for root, _dirs, files in os.walk(_CHAINS_DIR):
        for fn in sorted(files):
            if not fn.lower().endswith('.json'):
                continue
            path = os.path.join(root, fn)
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
            except Exception:
                continue
            if not isinstance(cfg, dict) or not any(
                k in cfg for k in ('sequences', 'llm_nodes',
                                   'chain_import_nodes', 'orchestrator_nodes')
            ):
                continue
            yield os.path.relpath(path, _CHAINS_DIR).replace('\\', '/'), cfg


def _build_from_cfg(cfg):
    return WorkflowGraphBuilder(
        sequences=cfg.get('sequences'),
        conditional_nodes=cfg.get('conditional_nodes'),
        llm_nodes=cfg.get('llm_nodes'),
        chain_import_nodes=cfg.get('chain_import_nodes'),
        code_nodes=cfg.get('code_nodes'),
        container_nodes=cfg.get('container_nodes'),
        context_nodes=cfg.get('context_nodes'),
        input_nodes=cfg.get('input_nodes'),
        handle_nodes=cfg.get('handle_nodes'),
        mcp_nodes=cfg.get('mcp_nodes'),
        output_nodes=cfg.get('output_nodes'),
        web_sequences=cfg.get('web_sequences'),
        form_filler_nodes=cfg.get('form_filler_nodes'),
        orchestrator_nodes=cfg.get('orchestrator_nodes'),
    ).build_workflow_graph()


def _legacy_flag(graph, node):
    """The pre-task#1 detector: chain_import / code, output bucket only."""
    if node.get('type') not in ('chain_import', 'code'):
        return False
    pairs = [
        (str(c.get('node_id')), str(c.get('input_port') or ''))
        for c in ((node.get('connections') or {}).get('output') or [])
        if isinstance(c, dict) and c.get('node_id')
    ]
    if not pairs:
        return False
    for tid, port in pairs:
        target = graph.get(tid) or {}
        if not ((target.get('type') == 'llm' and port == 'tools')
                or (target.get('type') == 'orchestrator' and port == 'brains')):
            return False
    return True


def test_shipped_chains_flag_is_bounded_and_exclusive():
    """Every shipped chain: flagged nodes are exclusivity-true, and the only
    non-legacy (i.e. not chain_import/code) flagged nodes live in the chains
    authored AFTER the generic flag shipped — testmcp.json's two mcp tool
    providers and the step-sized LinkedIn library wired to brains/chains
    ports.  A new chain type appearing in this list is a deliberate asset
    change, not a silent behavior drift (extend the set when you add one)."""
    extras = {}
    for rel, cfg in _iter_shipped_chains():
        graph = _build_from_cfg(cfg)
        for nid, node in graph.items():
            if not node.get('tool_provider'):
                continue
            # Invariant: a flagged node never has an execution input.
            for inp in (node.get('inputs') or []):
                port = str(inp.get('input_port') or 'input')
                assert port not in ('input', '', 'None'), (
                    f"{rel}: flagged node {nid} ({node.get('type')}) has an "
                    f"execution input on port {port!r}"
                )
            if not _legacy_flag(graph, node):
                extras.setdefault(rel, []).append(
                    (nid, node.get('type')))
    assert set(extras) <= {
        'testmcp.json', 'linkedin.json', 'linkedin_system_chain.json',
    }, extras
    assert sorted(t for _nid, t in extras['testmcp.json']) == ['mcp', 'mcp']
