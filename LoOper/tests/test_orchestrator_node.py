"""Orchestrator node — builder wiring, brains gating, goal loop, guards.

Run:  python -m pytest LoOper/tests/test_orchestrator_node.py
"""

import json
import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from player.multi_sequence.worflow_interpreter_modules.builder import (  # noqa: E402
    WorkflowGraphBuilder,
)
from player.multi_sequence.worflow_interpreter_modules.executor_modules.dependencies import (  # noqa: E402
    DependenciesMixin,
)
from player.multi_sequence.worflow_interpreter_modules.executor_modules.conditional_ops import (  # noqa: E402
    ConditionalMixin,
)
from player.multi_sequence.worflow_interpreter_modules.executor_modules.input_ops import (  # noqa: E402
    InputMixin,
)
from player.multi_sequence.worflow_interpreter_modules.executor_modules.llm_ops import (  # noqa: E402
    LLMMixin,
)
from player.multi_sequence.worflow_interpreter_modules.executor_modules.orchestrator_ops import (  # noqa: E402
    OrchestratorMixin,
)
from player.multi_sequence.worflow_interpreter_modules.executor_modules.port_store import (  # noqa: E402
    PortScopedStore,
)


# ---------------------------------------------------------------------------
# Harness — real mixins, dict-backed llm_executor, stub brain invocation
# ---------------------------------------------------------------------------


class _Harness(OrchestratorMixin, LLMMixin):
    def __init__(self):
        self._vars = {}
        self.llm_executor = self
        self.workflow_graph = {}
        self.port_store = PortScopedStore()
        self.chain_id = 'orch_test'
        self.executed = []       # brain node ids invoked
        self.dispatched = []     # _chain_input_context at each invocation
        self.llm_prompts = []    # every _orchestrator_llm_call prompt
        self.llm_script = []     # scripted answers (picked in order)
        self.brain_results = {}  # brain_id -> result text
        self._upstream_goal = None
        self._prompt_goal = None
        # The planner stub: a single-action goal needs no planning turn, so by
        # default one directive = the goal and llm_script stays reserved for
        # the picker/scoper.  Tests that exercise splitting set `directive_plan`.
        self.directive_plan = None
        self.plan_calls = []
        self.plan_states = []    # the state digest handed to each planning turn
        # A REAL temp chain identity by default: without one, the artifact
        # write/attach paths fall back to the SHIPPED chains dir and leak test
        # data into the repo (measured three times on 2026-09-23/24).  Tests
        # that assert wiring overwrite it via _sealed_chain_identity.
        _tmp_host = Path(tempfile.mkdtemp()) / 'ORCHESTRATOR.json'
        _tmp_host.write_text('{}', encoding='utf-8')
        self.chain_identity = str(_tmp_host)

    def _orchestrator_plan_directives(self, goal, node_data, stop_flag,
                                      generic=frozenset(), state=None):
        self.plan_calls.append(str(goal or ''))
        self.plan_states.append(state)
        if self.directive_plan is not None:
            return list(self.directive_plan)
        return [str(goal or '')]

    # llm_executor duck-type
    def get_variable(self, key):
        return self._vars.get(key)

    def set_variable(self, key, value):
        self._vars[key] = value

    def get_all_variables(self):
        return dict(self._vars)

    # Input mixin stubs (the harness does not inherit InputMixin)
    def _resolve_input_upstream_value(self, node, port='input'):
        if port == 'prompt':
            return self._prompt_goal
        return self._upstream_goal

    def _get_input_next_node(self, node, port):
        return '__done__'

    def _execute_chain_import_node(self, node, stop_flag):
        nid = node.get('id')
        self.executed.append(nid)
        self.dispatched.append(self.get_variable('_chain_input_context'))
        self.set_variable(
            'chain_tool_last_result_text', self.brain_results.get(nid, 'ok'),
        )
        return '__done__'

    def _orchestrator_llm_call(self, prompt, node_data, stop_flag, max_tokens=256):
        self.llm_prompts.append(prompt)
        if not self.llm_script:
            return None
        return self.llm_script.pop(0)


def _brain(nid, label, path='no_such_chain.json'):
    return {
        'id': nid, 'type': 'chain_import',
        'data': {'label': label, 'chain_file_path': path},
        'connections': {'output': []},
    }


def _orch_node(brains, data_overrides=None, with_goal_port=False):
    inputs = [
        {'from_node': b['id'], 'input_port': 'brains', 'output_type': 'output'}
        for b in brains
    ]
    if with_goal_port:
        inputs.append({'from_node': 'goal1', 'input_port': 'input', 'output_type': 'output'})
    data = {'max_steps': '5', 'picker': 'llm', 'synthesize': True}
    data.update(data_overrides or {})
    return {
        'id': 'orch1', 'type': 'orchestrator', 'data': data,
        'inputs': inputs, 'connections': {'output': []},
    }


def _make(orch, brains, goal='Find the cheapest monitor'):
    h = _Harness()
    h.workflow_graph = {orch['id']: orch}
    for b in brains:
        h.workflow_graph[b['id']] = b
    h.set_variable('_chain_input_context', goal)
    return h


def test_goal_resolution_prompt_port_wins_then_fixed_goal():
    """A goal wired to the 'prompt' port WINS over the node's fixed 'Goal'
    field; the field is the fallback when the port carries nothing."""
    h = _Harness()
    node = _orch_node([], data_overrides={'goal': 'fixed goal'})
    h.workflow_graph = {'orch1': node}

    # No prompt-port value -> the node's fixed goal.
    assert h._orchestrator_resolve_goal(node) == 'fixed goal'

    # A goal on the 'prompt' port overrides the fixed goal.
    h._prompt_goal = 'dynamic goal'
    assert h._orchestrator_resolve_goal(node) == 'dynamic goal'


# ---------------------------------------------------------------------------
# Builder wiring
# ---------------------------------------------------------------------------


def test_builder_parses_orchestrator_and_gates_brains():
    chain = {
        'orchestrator_nodes': [{
            'node_id': '0xorc', 'max_steps': 7, 'picker': 'laya',
            'connections': [],
            'outputs': {
                'output': [{'node_id': '0xout', 'input_port': 'input'}],
                'trace': [{'node_id': '0xctx', 'input_port': 'ctx_in'}],
                'route': [{'node_id': '0xrt', 'input_port': 'input'}],
            },
        }],
        'chain_import_nodes': [{
            'node_id': '0xbrain', 'label': 'Browser brain',
            'chain_file_path': 'chains/browser.json',
            'connections': [
                {'target_node_id': '0xorc', 'output_port': 'output', 'input_port': 'brains'},
            ],
        }],
        'output_nodes': [
            {'node_id': '0xout', 'label': 'O', 'connections': []},
            {'node_id': '0xrt', 'label': 'R', 'connections': []},
        ],
        'context_nodes': [{'node_id': '0xctx', 'label': 'C', 'connections': []}],
    }
    graph = WorkflowGraphBuilder(
        orchestrator_nodes=chain['orchestrator_nodes'],
        chain_import_nodes=chain['chain_import_nodes'],
        output_nodes=chain['output_nodes'],
        context_nodes=chain['context_nodes'],
    ).build_workflow_graph()

    assert graph['0xorc']['type'] == 'orchestrator'
    # 'output'/'route' drive execution; 'trace' is a data bucket.
    assert graph['0xorc']['connections']['output'][0]['node_id'] == '0xout'
    assert graph['0xorc']['connections']['trace'][0]['node_id'] == '0xctx'
    assert graph['0xorc']['connections']['route'][0]['node_id'] == '0xrt'
    # A chain_import wired to an orchestrator's brains port is a tool provider.
    assert graph['0xbrain']['tool_provider'] is True
    assert graph['0xbrain']['selected_by_llm'] is False
    # The brains entry lands in the orchestrator's inputs list.
    assert any(
        i['input_port'] == 'brains' and i['from_node'] == '0xbrain'
        for i in graph['0xorc'].get('inputs', [])
    )


def test_builder_chain_import_to_llm_tools_still_gated():
    """Regression: the llm/tools gating keeps working after the brains change."""
    graph = WorkflowGraphBuilder(
        llm_nodes=[{'node_id': '0xllm', 'prompt': 'p', 'connections': []}],
        chain_import_nodes=[{
            'node_id': '0xtool', 'chain_file_path': 'chains/t.json',
            'connections': [
                {'target_node_id': '0xllm', 'output_port': 'output', 'input_port': 'tools'},
            ],
        }],
    ).build_workflow_graph()
    assert graph['0xtool']['tool_provider'] is True


# ---------------------------------------------------------------------------
# Loop behavior
# ---------------------------------------------------------------------------


def test_loop_runs_brain_then_done_then_synthesis():
    b = _brain('0xb1', 'Browser brain')
    orch = _orch_node([b])
    h = _make(orch, [b])
    h.brain_results = {'0xb1': 'Found 3 monitors'}
    h.llm_script = ['0', 'open the monitor search page', 'DONE', 'Final answer text']

    h._execute_orchestrator_node(orch, None)

    assert h.executed == ['0xb1']
    assert h.get_variable('node_orch1_output') == 'Final answer text'
    trace = json.loads(h.get_variable('node_orch1_trace'))
    assert len(trace) == 1
    assert trace[0]['brain'] == 'Browser brain'
    # The picker prompt carried the goal and the worker description.
    assert 'Find the cheapest monitor' in h.llm_prompts[0]
    assert 'Browser brain' in h.llm_prompts[0]
    # The brain received the composed prompt through _chain_input_context.
    assert 'Your next step: open the monitor search page' in h.get_variable('_chain_input_context')


def test_step_cap_stops_and_falls_back_to_trace():
    b = _brain('0xb1', 'Brain A')
    orch = _orch_node([b], data_overrides={'max_steps': '3'})
    h = _make(orch, [b])
    results = ['r1', 'r2', 'r3']

    def _exec(node, stop_flag):
        h.executed.append(node.get('id'))
        h.set_variable('chain_tool_last_result_text', results.pop(0))
        return '__done__'

    h._execute_chain_import_node = _exec
    h.llm_script = ['0', 's1', '0', 's2', '0', 's3']  # pick+scope x3; synthesis finds no script -> None

    h._execute_orchestrator_node(orch, None)

    assert len(h.executed) == 3
    out = h.get_variable('node_orch1_output')
    assert 'r1' in out and 'r3' in out  # joined trace fallback


def test_no_progress_guard_stops_identical_result():
    b = _brain('0xb1', 'Brain A')
    orch = _orch_node([b], data_overrides={'max_steps': '9'})
    h = _make(orch, [b])
    h.brain_results = {'0xb1': 'same result'}
    # No scoped step (the scoping turn fails) — the identical-step guard does
    # not apply to the description task, so the RESULT-signature guard stops
    # the loop after the repeated outcome.
    h.llm_script = ['0', '', '0', '', 'S']

    h._execute_orchestrator_node(orch, None)

    assert h.executed == ['0xb1', '0xb1']  # stopped right after the repeated result
    assert h.get_variable('node_orch1_output') == 'S'


def test_identical_step_is_not_rerun():
    """Same worker + byte-identical scoped step right after itself = the
    scoping turn had nothing new; the loop stops instead of burning another
    full brain run (measured 2026-09-23: 3 identical LinkedIn re-runs)."""
    b = _brain('0xb1', 'Brain A')
    orch = _orch_node([b], data_overrides={'max_steps': '9'})
    h = _make(orch, [b])
    h.brain_results = {'0xb1': 'some result'}
    h.llm_script = ['0', 'open the page', '0', 'open the page']  # 2nd pair repeats

    h._execute_orchestrator_node(orch, None)

    assert h.executed == ['0xb1']  # the repeat was refused, not re-run
    # Synthesis found no script entry -> trace fallback carries the steps.
    assert 'open the page' in h.get_variable('node_orch1_output')


def test_trace_result_strips_router_headers():
    """The trace keeps only what the tool produced: the ``Tool ID:`` /
    ``Chain Description:`` headers exist for the worker's OWN router and
    poison the NEXT worker's router (it copies the nearest hex id into
    USE_TOOL — measured 2026-09-23)."""
    b1 = _brain('0xb1', 'Brain A')
    b2 = _brain('0xb2', 'Brain B')
    orch = _orch_node([b1, b2], data_overrides={'max_steps': '5'})
    h = _make(orch, [b1, b2])
    h.brain_results = {
        '0xb1': ('Tool ID: 0x1c97db4e650\n'
                 'Chain Description: Interacts with linkedin.com ...\n'
                 'Result: LinkedIn opened; the jobs page is visible'),
    }
    h.llm_script = ['0', 'open it', '1', 'look at the screen', 'DONE', 'S']

    h._execute_orchestrator_node(orch, None)

    assert len(h.dispatched) == 2
    second_prompt = h.dispatched[1]
    assert 'Tool ID' not in second_prompt
    assert 'Interacts with linkedin.com' not in second_prompt
    assert 'LinkedIn opened; the jobs page is visible' in second_prompt


def test_synthesis_prompt_echo_falls_back_to_trace():
    """A bracketed prompt-echo from the model is never a final answer
    (measured: Qwen3.8-2B returned '[Your complete step-by-step reasoning…]');
    the recorded trace is the honest fallback."""
    b = _brain('0xb1', 'Brain A')
    orch = _orch_node([b])
    h = _make(orch, [b])
    h.brain_results = {'0xb1': 'Found 3 monitors'}
    h.llm_script = ['0', 'do it', 'DONE',
                    '[Your complete step-by-step reasoning. End with a clear '
                    'statement of the final result.]']

    h._execute_orchestrator_node(orch, None)

    out = h.get_variable('node_orch1_output')
    assert 'Found 3 monitors' in out
    assert not out.startswith('[')


def test_chain_goal_and_step_published_for_workers():
    """Before invoking a worker the orchestrator publishes the clean scoping
    pair (_chain_goal / _chain_step) so its Input nodes get a tiny task with
    real context instead of the raw composed prompt."""
    b = _brain('0xb1', 'Brain A')
    orch = _orch_node([b])
    h = _make(orch, [b])
    h.llm_script = ['0', 'do it', 'DONE', 'S']

    h._execute_orchestrator_node(orch, None)

    assert h.get_variable('_chain_goal') == 'Find the cheapest monitor'
    assert h.get_variable('_chain_step') == 'do it'


def test_propagation_packet_published_with_every_dispatch(tmp_path, monkeypatch):
    """The propagation packet rides beside the scoping pair: the ROOT goal
    and the plan cursor (so a nested brain starts from its parent's
    decomposition) plus the parent's observed digest."""
    nested = _write_chain(tmp_path / 'nested.json',
                          {'orchestrator_nodes': [{'node_id': 'o1'}]})
    b = _brain('0xb1', 'Brain A', str(nested))
    orch = _orch_node([b])
    h = _make(orch, [b], goal='find and open the report')
    h.directive_plan = ['find the report', 'open the report']
    seen = []
    monkeypatch.setattr(h, '_orchestrator_observe_state',
                        lambda: 'Desktop now: reports app')

    def _chain(node, stop_flag=None, *a, **k):
        seen.append({
            'root': h.get_variable('_chain_root_goal'),
            'plan': h.get_variable('_chain_plan'),
            'state': h.get_variable('_chain_state'),
        })
        return '__done__'

    h._execute_chain_import_node = _chain
    h.llm_script = ['0', 'S']

    h._execute_orchestrator_node(orch, None)

    assert seen, 'a dispatch happened'
    assert seen[0]['root'] == 'find and open the report'
    plan = json.loads(seen[0]['plan'])
    assert plan['index'] == 0 and plan['total'] == 2
    assert plan['remaining'] == ['find the report', 'open the report']
    assert seen[0]['state'] == 'Desktop now: reports app'


def test_nested_goal_resolution_prefers_the_structured_pair(tmp_path):
    """A relayed run resolves its goal from the clean _chain_goal pair
    instead of parsing the composed prompt; the unwrap stays the fallback
    for relays that carry no pair (older builds, manual runs)."""
    nested = _write_chain(tmp_path / 'nested.json',
                          {'orchestrator_nodes': [{'node_id': 'o1'}]})
    b = _brain('0xb1', 'Brain A', str(nested))
    orch = _orch_node([b])
    h = _make(orch, [b], goal='ignored')
    h.set_variable(
        '_chain_input_context',
        'Your mission: parent mission\nProgress so far: (nothing yet)\n'
        'Your next step: parent scoped step')
    h.set_variable('_chain_goal', 'pair goal wins')
    h.llm_script = ['0', 'S']

    h._execute_orchestrator_node(orch, None)

    assert h.plan_calls == ['pair goal wins'], h.plan_calls

    # No pair variable: the composed text unwraps exactly as before.
    orch2 = _orch_node([b])
    h2 = _make(orch2, [b], goal='ignored')
    h2.set_variable(
        '_chain_input_context',
        'Your mission: parent mission\nProgress so far: (nothing yet)\n'
        'Your next step: parent scoped step')
    h2.llm_script = ['0', 'S']

    h2._execute_orchestrator_node(orch2, None)

    assert h2.plan_calls == ['parent scoped step'], h2.plan_calls


def test_composed_prompt_carries_the_root_goal_when_it_differs(tmp_path):
    """The worker prompt anchors on the ROOT goal when it differs from the
    scoped mission (multi-directive runs) — exactly once."""
    b = _brain('0xb1', 'Brain A')
    orch = _orch_node([b])
    h = _make(orch, [b])
    h.directive_plan = ['open the file', 'click the save button']
    h.llm_script = ['0', 'do it', 'DONE', 'S']

    h._execute_orchestrator_node(orch, None)

    assert h.dispatched, 'a dispatch happened'
    first = h.dispatched[0]
    assert 'Your mission: open the file' in first, first
    assert 'Root goal: Find the cheapest monitor' in first, first
    assert first.count('Root goal:') == 1, first


def test_child_outcome_is_parsed_stripped_and_recorded(tmp_path, monkeypatch):
    """The structured outcome marker is stripped from the prose, recorded on
    the trace entry (observability), and never leaks into the result text."""
    import AI.laya_client as lc
    nested = _write_chain(tmp_path / 'nested.json',
                          {'orchestrator_nodes': [{'node_id': 'o1'}]})
    b = _brain('0xb1', 'LinkedIn brain', str(nested))
    orch = _orch_node([b])
    h = _make(orch, [b], goal='open linkedin and click network')
    h.directive_plan = ['open linkedin', 'click on my network']
    outcome = {'stop_reason': 'no_worker',
               'steps': [{'step': 'open linkedin', 'ok': True}],
               'observed': 'Desktop now: chrome'}
    text = ('LinkedIn is open.\n[ORCH-REMAINING] ["click on my network"]'
            '\n[ORCH-OUTCOME] ' + json.dumps(outcome))

    monkeypatch.setattr(lc, 'available', lambda: True)
    monkeypatch.setattr(lc, 'noul', lambda state, instructions: 0.1)

    def _chain(node, stop_flag=None, *a, **k):
        h.set_variable('chain_tool_last_result_text', text)
        return '__done__'

    h._execute_chain_import_node = _chain
    h.llm_script = ['0', '0', 'DONE', 'S']

    h._execute_orchestrator_node(orch, None)

    trace = json.loads(h.get_variable('node_orch1_trace'))
    assert trace[0]['result'].startswith('LinkedIn is open.')
    assert '[ORCH-OUTCOME]' not in trace[0]['result']
    assert trace[0]['outcome'] == outcome
    # Every occurrence is stripped; the first valid object wins.
    assert h._orchestrator_split_outcome(
        'ok.\n[ORCH-OUTCOME] {"a": 1}\n[ORCH-OUTCOME] {"b": 2}') == (
            'ok.', {'a': 1})
    assert h._orchestrator_split_outcome('plain text') == ('plain text', None)


def test_nested_run_inherits_the_parent_state_when_blind(tmp_path, monkeypatch):
    """A relayed run that observes nothing falls back to the parent's digest
    so its planning turn is not blind."""
    nested = _write_chain(tmp_path / 'nested.json',
                          {'orchestrator_nodes': [{'node_id': 'o1'}]})
    b = _brain('0xb1', 'Brain A', str(nested))
    orch = _orch_node([b])
    h = _make(orch, [b], goal='open the report and read it')
    h.set_variable(
        '_chain_input_context',
        'Your mission: open the report\nProgress so far: (nothing yet)\n'
        'Your next step: open the report')
    h.set_variable('_chain_state', 'Desktop now: reports app is open')
    monkeypatch.setattr(h, '_orchestrator_observe_state', lambda: '')
    h.llm_script = ['0', 'S']

    h._execute_orchestrator_node(orch, None)

    assert h.plan_states, 'the planner ran'
    assert str(h.plan_states[0]).startswith('Parent-observed:'), h.plan_states[0]
    assert 'reports app is open' in h.plan_states[0]


def test_action_graph_shadow_logs_wired_workers(tmp_path, caplog):
    """Plan Phase 2: every activation renders the wired workers' DERIVED
    action graphs into the log (coverage + lines) — observability only."""
    import logging as _logging
    child = _write_chain(tmp_path / 'child.json', {
        'handle_nodes': [{'node_id': 'h1', 'action_type': 'click',
                          'goal_description': 'the login button'}],
    })
    b = _brain('0xb1', 'Brain A', str(child))
    orch = _orch_node([b])
    h = _make(orch, [b])
    h.llm_script = ['0', 'do it', 'DONE', 'S']
    caplog.set_level(_logging.INFO)

    h._execute_orchestrator_node(orch, None)

    assert 'Action graph (shadow)' in caplog.text
    assert 'Brain A' in caplog.text
    assert 'the login button' in caplog.text


def test_action_graph_shadow_respects_the_kill_switch(tmp_path, caplog,
                                                       monkeypatch):
    import logging as _logging
    monkeypatch.setenv('LOOPER_ORCH_GRAPH', 'off')
    child = _write_chain(tmp_path / 'child.json', {
        'handle_nodes': [{'node_id': 'h1', 'action_type': 'click',
                          'goal_description': 'the login button'}],
    })
    b = _brain('0xb1', 'Brain A', str(child))
    orch = _orch_node([b])
    h = _make(orch, [b])
    h.llm_script = ['0', 'do it', 'DONE', 'S']
    caplog.set_level(_logging.INFO)

    h._execute_orchestrator_node(orch, None)

    assert 'Action graph (shadow)' not in caplog.text
    assert 'Plan projection (shadow)' not in caplog.text


def test_plan_projection_matches_directives_to_action_lines(tmp_path, caplog):
    """Plan Phase 3 shadow: each directive is projected onto the workers'
    derived action lines — matched (with the shared words) or UNMATCHED —
    so the funnel becomes a number."""
    import logging as _logging
    seq = tmp_path / 'jobs_seq.json'
    seq.write_text(json.dumps({
        'schema_version': 1, 'mode': 'web',
        'actions': [{'type': 'click', 'locator': {'text': 'Jobs'}}],
    }), encoding='utf-8')
    child = _write_chain(tmp_path / 'child.json', {
        'web_sequences': [{'node_id': 'w1', 'name': 'jobs_seq.json',
                           'session_file': str(seq), 'connections': []}],
    })
    b = _brain('0xb1', 'Brain A', str(child))
    orch = _orch_node([b])
    h = _make(orch, [b])
    h.directive_plan = ['click the jobs button', 'check the weather']
    h.llm_script = ['0', 'do it', 'DONE', 'S']
    caplog.set_level(_logging.INFO)

    h._execute_orchestrator_node(orch, None)

    text = caplog.text
    assert 'Plan projection (shadow)' in text
    assert 'shared: job' in text
    assert 'UNMATCHED' in text
    assert 'projection: 1/2 directive(s) matched' in text


def test_missing_goal_fails_node():
    b = _brain('0xb1', 'Brain A')
    orch = _orch_node([b])
    h = _make(orch, [b], goal='')
    assert h._execute_orchestrator_node(orch, None) is None
    assert h.executed == []


def test_no_brains_fails_node():
    orch = _orch_node([], data_overrides={})
    h = _make(orch, [])
    assert h._execute_orchestrator_node(orch, None) is None


def test_ask_user_appends_to_trace():
    b = _brain('0xb1', 'Brain A')
    orch = _orch_node([b])
    h = _make(orch, [b])
    h.set_variable('_ask_user_callback', lambda q: 'Try the Dell one')
    h.llm_script = ['ASK', 'DONE', 'Answer']

    h._execute_orchestrator_node(orch, None)

    trace = json.loads(h.get_variable('node_orch1_trace'))
    assert trace and trace[0]['brain'] == 'user'
    assert 'Try the Dell one' in trace[0]['result']


def test_ask_without_channel_marks_blocked():
    b = _brain('0xb1', 'Brain A')
    orch = _orch_node([b])
    h = _make(orch, [b])
    h.llm_script = ['ASK']

    h._execute_orchestrator_node(orch, None)

    assert h.get_variable('node_orch1_blocked') is True
    assert orch['data'].get('_orchestrator_blocked') is True


# ---------------------------------------------------------------------------
# Pickers
# ---------------------------------------------------------------------------


def test_parse_pick_table():
    p = OrchestratorMixin._orchestrator_parse_pick
    assert p('0', 2) == 0
    assert p('1', 2) == 1
    assert p('2', 2) == 1          # 1-based fallback for the last candidate
    assert p('9', 2) is None
    assert p('DONE', 2) == 'done'
    assert p('done.', 2) == 'done'
    assert p('ask', 2) == 'ask'
    assert p('garbage here', 2) is None
    assert p('<think>hmm</think>done', 2) == 'done'
    assert p('', 2) is None


def test_laya_picker_used_when_engine_available(monkeypatch):
    import AI.laya_client as lc
    # done-probe: not done on step 1, done on step 2; ask stays low.
    done = [0.1, 0.9]

    def _noul(state, instructions):
        return done.pop(0) if 'already been fully achieved' in instructions else 0.1

    monkeypatch.setattr(lc, 'noul', _noul)
    monkeypatch.setattr(lc, 'choice', lambda state, instructions, criteria: next(iter(criteria)))

    b = _brain('0xb1', 'Brain A')
    orch = _orch_node([b], data_overrides={'picker': 'laya'})
    h = _make(orch, [b])
    h.brain_results = {'0xb1': 'done by laya pick'}
    h.llm_script = ['do the thing', 'S']  # scoping + synthesis reach the LLM

    h._execute_orchestrator_node(orch, None)

    assert h.executed == ['0xb1']
    assert h.get_variable('node_orch1_output') == 'S'


def test_laya_picker_falls_back_to_llm_when_engine_unavailable(monkeypatch):
    import AI.laya_client as lc
    monkeypatch.setattr(lc, 'noul', lambda state, instructions: None)

    b = _brain('0xb1', 'Brain A')
    orch = _orch_node([b], data_overrides={'picker': 'laya'})
    h = _make(orch, [b])
    h.brain_results = {'0xb1': 'llm picked this'}
    h.llm_script = ['0', 'do it', 'DONE', 'S']

    h._execute_orchestrator_node(orch, None)

    assert h.executed == ['0xb1']
    assert h.get_variable('node_orch1_output') == 'S'


def test_laya_picker_done_is_believed_only_after_a_step(monkeypatch):
    """A 'done' at ZERO steps is not taken at face value — nothing has run,
    so 'achieved' cannot be verified (measured 2026-09-23: the nested relay
    blob scored 0.916 done with zero steps).  One worker runs first, then the
    next 'done' stops the loop."""
    import AI.laya_client as lc
    monkeypatch.setattr(
        lc, 'noul',
        lambda state, instructions: 0.9 if 'already been fully achieved' in instructions else 0.1,
    )
    monkeypatch.setattr(
        lc, 'choice', lambda state, instructions, criteria: next(iter(criteria)),
    )

    b = _brain('0xb1', 'Brain A')
    orch = _orch_node([b], data_overrides={'picker': 'laya'})
    h = _make(orch, [b])
    h.brain_results = {'0xb1': 'did the thing'}
    h.llm_script = ['look at it', 'S']  # scope + synthesis

    h._execute_orchestrator_node(orch, None)

    assert h.executed == ['0xb1']
    assert h.get_variable('node_orch1_output') == 'S'


def test_compound_goal_needs_a_step_per_sequenced_action():
    """The real 2026-09-23 failure: the goal 'open linkedin, search a job with
    the easy apply badge and then check the screen and tell me what you see'
    scored p_done 0.905 after ONE step, so the loop stopped before the vision
    brain ever ran.  The gate is structural: a goal that explicitly sequences N
    actions must have had N steps before a 'done' is taken at face value."""
    h = _Harness()
    goal = (
        'open linkedin, search a job that has the "easy apply badge" and '
        'then check the screen and tell me what you see'
    )
    assert h._orchestrator_action_floor(goal) == 2
    assert h._orchestrator_done_is_believable(goal, [{'n': 1}]) is False
    assert h._orchestrator_done_is_believable(
        goal, [{'n': 1}, {'n': 2}]) is True
    # A single-action goal is believed after its one step.
    assert h._orchestrator_done_is_believable(
        'click the messages button', [{'n': 1}]) is True


def test_unwrap_local_goal_table():
    """A nested orchestrator's input is the parent's relay; its LOCAL goal is
    the parent-scoped step (or the description task) — never the blob."""
    u = OrchestratorMixin._orchestrator_unwrap_local_goal
    assert u('find the monitor') == 'find the monitor'
    assert u('') == ''
    assert u('Long-term goal: A\nProgress so far:\n(nothing yet)\n'
             'Your next step: B') == 'B'
    assert u('Long-term goal: A\nProgress so far:\n(nothing yet)\n'
             'Your task: C') == 'C'
    assert u('Long-term goal: A\nProgress so far:\n(nothing yet)') == 'A'
    # Current scaffolding: the mission IS the scope this worker covers, and the
    # step is what it does next.
    assert u('Your mission: 1) open linkedin\n2) click messages\n'
             'Progress so far:\n(nothing yet)\n'
             'Your next step: 1) open linkedin\n2) click messages') == (
        '1) open linkedin\n2) click messages')
    assert u('Your mission: click on my network\nProgress so far:\n(nothing '
             'yet)') == 'click on my network'


def test_action_floor_table():
    f = OrchestratorMixin._orchestrator_action_floor
    assert f('open linkedin') == 1
    # a bare 'and' joins one action with its object — not two actions
    assert f('open linkedin and search easy apply jobs') == 1
    assert f('open linkedin and then peek at the screen') == 2
    assert f('a; b; c') == 3
    assert f('a then b then c then d then e') == 4  # capped
    assert f('') == 1


def test_nested_orchestrator_goal_is_the_parent_relay_step():
    """A sub-brain's orchestrator receives the parent's composed prompt; its
    goal must be the parent-scoped step, not the relay blob — measured
    2026-09-23: with the blob, the nested done-probe read the relay's own
    action text as completed work (0.916, zero steps) and the sub-brain
    stopped instantly with 'The goal could not be advanced.'"""
    b = _brain('0xb1', 'Brain A')
    orch = _orch_node([b])
    h = _make(orch, [b], goal=(
        'Long-term goal: open linkedin and then peek at the screen\n'
        'Progress so far:\n(nothing yet)\n'
        'Your next step: Open LinkedIn and search for "Easy Apply" jobs.'
    ))
    h.brain_results = {'0xb1': 'jobs page open'}
    h.llm_script = ['0', 'Open LinkedIn and search for "Easy Apply" jobs',
                    'DONE', 'S']

    h._execute_orchestrator_node(orch, None)

    # The picker and scoper saw the LOCAL step, not the parent's scaffolding.
    assert 'GOAL: Open LinkedIn and search for "Easy Apply" jobs.' in h.llm_prompts[0]
    assert 'Long-term goal:' not in h.llm_prompts[0]
    # The worker still receives a composed prompt (this orchestrator's own
    # wrapper around the local goal), and the loop ended via DONE.
    assert h.dispatched[0].startswith(
        'Your mission: Open LinkedIn and search for "Easy Apply" jobs.'
    )
    assert h.get_variable('node_orch1_output') == 'S'


# ---------------------------------------------------------------------------
# Router brains — step scoping + per-brain dispatch
# ---------------------------------------------------------------------------


def _router_brain_file(tmp_path, tools):
    """Write a router brain chain: one LLM router + chain_import tools wired
    to its 'tools' port, each tool chain carrying a description."""
    nodes = []
    for i, (prefix, desc) in enumerate(tools):
        tool_file = tmp_path / f"{prefix}.json"
        tool_file.write_text(json.dumps({'description': desc}), encoding='utf-8')
        nodes.append({
            'node_id': f'0xt{i}', 'prefix': prefix,
            'chain_file_path': str(tool_file),
            'connections': [
                {'output_port': 'output', 'target_node_id': '0xr1', 'input_port': 'tools'},
            ],
        })
    brain_file = tmp_path / 'router_brain.json'
    brain_file.write_text(json.dumps({
        'description': 'brain with atomic tools',
        'llm_nodes': [{'node_id': '0xr1', 'prompt': 'Decide which connected tool...'}],
        'chain_import_nodes': nodes,
    }), encoding='utf-8')
    return str(brain_file)


def test_router_brain_tools_are_context_not_the_dispatch_unit(tmp_path):
    """Tools are discovered for CONTEXT — they surface in the brain's runtime
    description ("[tools: ...]"); the BRAIN stays the dispatch unit: the
    orchestrator scopes a step and the brain's own router chooses the tool."""
    brain_file = _router_brain_file(tmp_path, [
        ('linkedin', 'opens linkedin.com and navigates to the jobs section'),
        ('jobsearch', 'searches for the label "easy apply" on job postings'),
    ])
    b = _brain('0xb1', 'LinkedIn brain', brain_file)
    orch = _orch_node([b])
    h = _make(orch, [b])

    brains = h._orchestrator_collect_brains(orch)
    assert len(brains) == 1
    assert [t['alias'] for t in brains[0]['tools']] == ['linkedin', 'jobsearch']
    assert '[tools: linkedin, jobsearch]' in brains[0]['description']

    # The picker prompt offers the BRAIN (with its tool context), not tools.
    h.llm_script = ['0']
    pick = h._orchestrator_llm_pick('goal', '', brains, {}, None)
    assert pick == 0
    assert 'brain with atomic tools' in h.llm_prompts[0]
    assert '[tools: linkedin, jobsearch]' in h.llm_prompts[0]


def test_loop_scopes_a_step_and_dispatches_it(tmp_path):
    """Regression: the orchestrator used to re-send the whole goal verbatim
    to the brain's router input node.  It must instead scope the goal into a
    concrete step for the picked worker ("Your next step: ...") and let that
    worker's own router choose the tool, reading its output to drive the
    next turn."""
    b = _brain('0xb1', 'LinkedIn brain')  # no chain file -> name fallback
    orch = _orch_node([b], data_overrides={'max_steps': '4'})
    h = _make(orch, [b])
    h.brain_results = {'0xb1': 'jobs page open'}
    h.llm_script = [
        '0', 'open linkedin and navigate to the jobs section',  # pick, scope
        'DONE', 'S',
    ]

    h._execute_orchestrator_node(orch, None)

    assert h.executed == ['0xb1']
    # The dispatch carries the SCOPED step — not the raw goal alone, and no
    # tool naming (the brain routes).
    assert 'Your next step: open linkedin and navigate to the jobs section' in h.dispatched[0]
    # The worker receives its MISSION (the task inside its scope), never the
    # original request: a long goal must not ride into every instance.
    assert 'Your mission:' in h.dispatched[0]
    assert 'Long-term goal:' not in h.dispatched[0]
    assert 'execute the tool' not in h.dispatched[0].lower()
    # The scoping turn saw the goal, the worker, the progress, and asked for
    # one concrete step.
    scope_prompt = h.llm_prompts[1]
    assert 'Find the cheapest monitor' in scope_prompt
    assert 'LinkedIn brain' in scope_prompt
    assert 'STEP:' in scope_prompt
    trace = json.loads(h.get_variable('node_orch1_trace'))
    assert trace[0]['step'] == 'open linkedin and navigate to the jobs section'
    assert trace[0]['result'] == 'jobs page open'


def test_scoping_falls_back_to_description_task(tmp_path):
    """When the scoping turn yields nothing, the worker's description is the
    task (previous behavior) and the loop still runs."""
    b = _brain('0xb1', 'Brain A')
    orch = _orch_node([b], data_overrides={'max_steps': '3'})
    h = _make(orch, [b])
    h.llm_script = ['0', '', 'DONE', 'S']  # the scope turn returns empty

    h._execute_orchestrator_node(orch, None)

    assert h.executed == ['0xb1']
    assert 'Your task: Brain A' in h.dispatched[0]
    trace = json.loads(h.get_variable('node_orch1_trace'))
    assert trace[0]['step'] == ''


def test_laya_picker_options_are_worker_level(tmp_path, monkeypatch):
    import AI.laya_client as lc
    captured = {}

    def _choice(state, instructions, criteria):
        captured['options'] = list(criteria)
        return list(criteria)[0]

    done = [0.1, 0.9]

    def _noul(state, instructions):
        return done.pop(0) if 'already been fully achieved' in instructions else 0.1

    monkeypatch.setattr(lc, 'noul', _noul)
    monkeypatch.setattr(lc, 'choice', _choice)
    b = _brain('0xb1', 'LinkedIn brain')
    orch = _orch_node([b], data_overrides={'picker': 'laya'})
    h = _make(orch, [b])
    h.llm_script = []

    h._execute_orchestrator_node(orch, None)

    assert captured['options'][0].startswith("Run the worker 'LinkedIn brain'")


def test_laya_done_probe_judges_steps_only_not_result_prose(monkeypatch):
    """The done-probe must see the STEPS, never the workers' result prose:
    LLM-authored completion claims ("I have opened LinkedIn and observed ...")
    read as facts and stopped the loop after one step (measured p_done 0.89
    on the real failing traces)."""
    import AI.laya_client as lc

    seen = {}

    def _noul(state, instructions):
        if 'already been fully achieved' in instructions:
            seen['done_state'] = state
            return 0.1
        return 0.1

    monkeypatch.setattr(lc, 'noul', _noul)
    monkeypatch.setattr(
        lc, 'choice', lambda state, instructions, criteria: next(iter(criteria)),
    )

    h = _Harness()
    brains = [{'name': 'vision_system_chain', 'description': 'looks at the screen'}]
    trace = [{
        'n': 1, 'brain': 'vision_system_chain',
        'step': 'look at the current screen and report what is visible',
        'result': ('I have opened LinkedIn and observed the current screen '
                   'content. The home page displays a search bar, ...'),
    }]
    pick = h._orchestrator_laya_pick(
        'open linkedin and peek', 'FULL TRACE PROSE', brains, trace,
    )
    assert pick == 0
    assert 'Steps taken so far' in seen['done_state']
    assert 'look at the current screen' in seen['done_state']
    assert 'I have opened LinkedIn' not in seen['done_state']
    assert 'FULL TRACE PROSE' not in seen['done_state']


def test_the_worker_pick_prefers_the_brains_that_name_the_step(monkeypatch):
    """The step's OWN objects decide who MAY serve it, mirroring the chains
    gate: a single namer is picked deterministically (no engine), several
    namers go to the typed question restricted to them, and ambient words
    from the observed state confirm nothing (measured 2026-09-25: 'Open
    Chrome browser' went to the linkedin brain over the word 'chrome')."""
    import AI.laya_client as lc

    brains = [
        {'name': 'linkedin_system_chain',
         'description': "Interacts with linkedin.com: opens LinkedIn, "
                        "searches job postings for 'Easy Apply', and "
                        'fills/submits job application forms once they are '
                        'open.'},
        {'name': 'vision_system_chain',
         'description': 'Looks at the current screen and returns a text '
                        'summary of what is visible, including observing an '
                        'open page another brain already opened. It does not '
                        'open, navigate, or type into websites.'},
        {'name': 'web_basetools',
         'description': 'this has a set of web related verification tools to '
                        'read, see, and interact with the browser.'},
    ]
    h = _Harness()
    state = ("Desktop now: 'Recibidos (11,330) - x@gmail.com - Gmail - "
             "Google Chrome' (process: chrome.exe)")

    # One namer ('screen'): deterministic pick, no engine call at all.
    def _boom(*a, **k):
        raise AssertionError('a single namer must not consult the engine')

    monkeypatch.setattr(lc, 'choice', _boom)
    assert h._orchestrator_laya_worker(
        'Observe what is displayed on the screen', brains, '', state) == 1

    # 'Open Chrome browser': the verb 'open' is not an object and 'chrome'
    # is ambient — only 'browser' remains, named by web_basetools alone.
    assert h._orchestrator_laya_worker(
        'Open Chrome browser', brains, '', state) == 2

    # The RICHER namer wins deterministically: for 'read the browser visible
    # screen' vision names 'visible' AND 'screen' where web_basetools names
    # only 'browser' (an equal count would go to the typed question).
    assert h._orchestrator_laya_worker(
        'read the browser visible screen', brains, '', state) == 1

    # A brain whose description REFUSES the step's verb is never a
    # candidate: 'Open LinkedIn' — vision reads 'does not open, navigate, or
    # type into websites' (measured 2026-09-25: it was handed 'Open LinkedIn'
    # while its own description says it cannot).
    assert h._orchestrator_laya_worker(
        'Open LinkedIn', brains, '', state) == 0

    # A tie (one object each) goes to the RESTRICTED question — and the
    # refusing brain is not even in its pool.
    seen = {}

    def _choice(premise, instructions, criteria):
        seen['opts'] = list(criteria)
        return next(iter(criteria))

    monkeypatch.setattr(lc, 'choice', _choice)
    picked = h._orchestrator_laya_worker(
        'click the browser page', brains, '', state)
    assert picked != 0, 'the linkedin brain names nothing in that step'
    assert seen['opts'], 'the question still runs over the namers'
    assert all('linkedin_system_chain' not in o for o in seen['opts'])


def test_laya_done_probe_threshold_055(monkeypatch):
    """The measured separation (real done 0.587 vs false 0.506/0.144) needs
    the threshold at 0.55: 0.52 keeps the loop going, 0.56 stops it — with
    the done-gate's floor already satisfied (one step taken)."""
    import AI.laya_client as lc

    monkeypatch.setattr(
        lc, 'choice', lambda state, instructions, criteria: next(iter(criteria)),
    )
    h = _Harness()
    brains = [{'name': 'Brain A', 'description': 'does things'}]
    trace = [{'n': 1, 'brain': 'Brain A', 'step': 'did something', 'result': 'r'}]

    monkeypatch.setattr(
        lc, 'noul',
        lambda state, instructions: (
            0.52 if 'already been fully achieved' in instructions else 0.1),
    )
    assert h._orchestrator_laya_pick('goal', '', brains, trace) == 0

    monkeypatch.setattr(
        lc, 'noul',
        lambda state, instructions: (
            0.56 if 'already been fully achieved' in instructions else 0.1),
    )
    assert h._orchestrator_laya_pick('goal', '', brains, trace) == 'done'


# ---------------------------------------------------------------------------
# Branch-source helper (shared by core.py and dependencies.py)
# ---------------------------------------------------------------------------


def test_is_branch_source_helper():
    assert DependenciesMixin._is_branch_source({'type': 'conditional'}) is True
    assert DependenciesMixin._is_branch_source({'type': 'input', 'data': {}, 'connections': {'output': []}}) is False
    # decision routing active + true/false wired -> branch source
    assert DependenciesMixin._is_branch_source({
        'type': 'input', 'data': {'decision_mode': 'true'}, 'connections': {'true': []},
    }) is True
    # routing flag but no true/false wiring -> normal output path
    assert DependenciesMixin._is_branch_source({
        'type': 'input', 'data': {'decision_mode': 'true'}, 'connections': {'output': []},
    }) is False
    # route_on_answer counts too
    assert DependenciesMixin._is_branch_source({
        'type': 'input', 'data': {'route_on_answer': True}, 'connections': {'false': []},
    }) is True
    assert DependenciesMixin._is_branch_source(None) is False
    assert DependenciesMixin._is_branch_source({'type': 'llm'}) is False
    # orchestrator: route wired -> fulfilment branch source ...
    assert DependenciesMixin._is_branch_source({
        'type': 'orchestrator',
        'connections': {'output': [], 'route': [{'node_id': '0xb'}]},
    }) is True
    # ... unwired route -> normal output path
    assert DependenciesMixin._is_branch_source({
        'type': 'orchestrator', 'connections': {'output': []},
    }) is False


class _DepHarness(DependenciesMixin):
    """Just enough executor surface for the dependency gate."""

    def __init__(self, graph):
        self.workflow_graph = graph
        self.skipped_nodes = set()
        self.llm_executor = None


def _route_graph(fulfilled):
    orch = {
        'id': '0xa', 'type': 'orchestrator', 'data': {},
        'connections': {
            'output': [{'node_id': '0xx', 'input_port': 'input'}],
            'route': [{'node_id': '0xb', 'input_port': 'input'}],
        },
        '_orchestrator_fulfilled': fulfilled,
    }
    x = {'id': '0xx', 'type': 'output', 'data': {}, 'inputs': [
        {'from_node': '0xa', 'input_port': 'input', 'output_type': 'output'}]}
    b = {'id': '0xb', 'type': 'orchestrator', 'data': {}, 'inputs': [
        {'from_node': '0xa', 'input_port': 'input', 'output_type': 'route'}]}
    return {'0xa': orch, '0xx': x, '0xb': b}


def test_orchestrator_route_gates_dependents_on_the_verdict():
    """'route' targets arm only when the run did NOT achieve its goal;
    'output' targets only when it did."""
    h = _DepHarness(_route_graph(False))
    assert h._is_branch_source(h.workflow_graph['0xa']) is True
    assert h._are_all_dependencies_satisfied('0xb', {'0xa'}) is True
    assert h._are_all_dependencies_satisfied('0xx', {'0xa'}) is False

    h2 = _DepHarness(_route_graph(True))
    assert h2._are_all_dependencies_satisfied('0xx', {'0xa'}) is True
    assert h2._are_all_dependencies_satisfied('0xb', {'0xa'}) is False


def test_orchestrator_branch_selects_output_or_route():
    h = ConditionalMixin()
    orch = {
        'id': '0xa', 'type': 'orchestrator', 'data': {},
        'connections': {
            'output': [{'node_id': '0xx', 'input_port': 'input'}],
            'route': [{'node_id': '0xb', 'input_port': 'input'}],
        },
    }
    assert h._get_conditional_branch_node(orch, True) == '0xx'
    assert h._get_conditional_branch_node(orch, False) == '0xb'


# ---------------------------------------------------------------------------
# Goal ledger + ask-user v2 integration
# ---------------------------------------------------------------------------


def test_goal_ledger_two_activations(monkeypatch, tmp_path):
    """Activation 1 blocks (no ask channel) and parks the goal; activation 2
    resumes it from the ledger (empty input) and completes it."""
    monkeypatch.setenv('LOOPER_GOAL_LEDGER', str(tmp_path / 'goals.json'))
    b = _brain('0xb1', 'Brain A')
    orch = _orch_node([b], data_overrides={'use_goal_ledger': True})

    h = _make(orch, [b], goal='Finish the report')
    h.llm_script = ['ASK']  # ask -> no channel -> blocked
    h._execute_orchestrator_node(orch, None)
    assert h.get_variable('node_orch1_blocked') is True

    from player.agentic_ops import goal_ledger
    active = goal_ledger.get_active_goal()
    assert active and active['status'] == 'blocked'
    assert active['text'] == 'Finish the report'

    # Second activation: no goal on the input -> the ledger supplies it.
    h2 = _make(orch, [b], goal='')
    h2.llm_script = ['0', 'do it', 'DONE', 'S']
    h2._execute_orchestrator_node(orch, None)
    assert h2.get_variable('node_orch1_output') == 'S'
    assert goal_ledger.get_active_goal() == {}


def test_route_port_carries_the_unfulfilled_goal():
    """A run that does not end 'done' publishes the goal on 'route' and marks
    the activation unfulfilled; a done run clears the value."""
    b = _brain('0xb1', 'Brain A')
    orch = _orch_node([b])
    h = _make(orch, [b], goal='Finish the report')
    h.llm_script = ['ASK']  # ask -> no channel -> blocked (not 'done')
    h._execute_orchestrator_node(orch, None)

    assert orch['_orchestrator_fulfilled'] is False
    assert orch['data']['_orchestrator_fulfilled'] is False
    assert h.port_store.get_output('orch_test', 'orch1', 'route') == 'Finish the report'
    assert h.get_variable('node_orch1_route') == 'Finish the report'

    h2 = _make(orch, [b], goal='Finish the report')
    h2.llm_script = ['0', 'do it', 'DONE', 'S']
    h2._execute_orchestrator_node(orch, None)

    assert orch['_orchestrator_fulfilled'] is True
    assert h2.port_store.get_output('orch_test', 'orch1', 'route') == ''


def test_orchestrator_ask_user_prefers_v2_channel():
    class _HarnessV2(_Harness, InputMixin):
        pass

    b = _brain('0xb1', 'Brain A')
    orch = _orch_node([b])
    h = _HarnessV2()
    h.workflow_graph = {orch['id']: orch, b['id']: b}
    h.set_variable('_chain_input_context', 'Find the cheapest monitor')
    h.set_variable(
        '_ask_user_v2_callback',
        lambda req: {'value': 'Use the Dell', 'kind': 'text', 'attachments': []},
    )
    h.llm_script = ['ASK', 'DONE', 'S']

    h._execute_orchestrator_node(orch, None)

    trace = json.loads(h.get_variable('node_orch1_trace'))
    assert trace and trace[0]['brain'] == 'user'
    assert 'Use the Dell' in trace[0]['result']


# ---------------------------------------------------------------------------
# Model resolution fallbacks
# ---------------------------------------------------------------------------


def test_orchestrator_model_node_value_wins():
    h = _Harness()
    engine, model = h._orchestrator_model_config(
        {'engine': 'llamacpp', 'model': 'My-Q4.gguf'})
    assert (engine, model) == ('llamacpp', 'My-Q4.gguf')


def test_orchestrator_model_borrows_chain_llm_node():
    h = _Harness()
    h.workflow_graph = {
        'llm1': {'type': 'llm', 'data': {'llamacpp_model_path': 'Borrowed.gguf'}},
    }
    engine, model = h._orchestrator_model_config({'engine': 'llamacpp', 'model': ''})
    assert model == 'Borrowed.gguf'


def test_orchestrator_model_falls_back_to_app_default():
    """The shipped orchestrator root has NO LLM node and a blank model — the
    picker must still get a model (AI/config.json default).  Regression: the
    run stopped with 'No model configured and no chain LLM node to borrow
    from' and produced zero steps."""
    h = _Harness()
    h.workflow_graph = {}
    engine, model = h._orchestrator_model_config({'engine': 'llamacpp', 'model': ''})
    assert engine == 'llamacpp'
    assert model.lower().endswith('.gguf'), f'app default not resolved: {model!r}'


def test_orchestrator_llm_call_payload_thinking_disabled(monkeypatch):
    """The picker/synthesis call must go out in chat mode with thinking
    DISABLED, the resolved model path, and the requested token budget.

    Verified against both local models: thinking on → the whole budget is
    spent on reasoning_content and content comes back EMPTY (seen in a real
    session); raw completion → chat-trained models emit an immediate EOS.
    """
    h = _Harness()
    captured = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {'response': '0'}

    import requests

    def _post(url, json=None, timeout=None):
        captured['url'] = url
        captured['payload'] = json
        return _Resp()

    monkeypatch.setattr(requests, 'post', _post)

    # Call the REAL implementation (the harness overrides it for loop tests).
    text = OrchestratorMixin._orchestrator_llm_call(
        h, 'PROMPT', {'engine': 'llamacpp', 'model': ''}, None, max_tokens=32,
    )
    assert text == '0'
    payload = captured['payload']
    assert payload['chat_template_kwargs'] == {'enable_thinking': False}
    assert 'raw_completion' not in payload
    assert payload['max_tokens'] == 32
    assert payload['model'].lower().endswith('.gguf')
    assert '/llamacpp/generate' in captured['url']


# ---------------------------------------------------------------------------
# Node brains (task #1) — single flagged nodes as mini brains
# ---------------------------------------------------------------------------


def _node_brain(nid, ntype, label='', data=None, flagged=True):
    """A single-node brain wired to the orchestrator's 'brains' port.

    ``flagged`` mirrors the builder's generic post-pass: a node whose only
    execution edges land on router ports gets ``tool_provider = True``.
    """
    d = dict(data or {})
    if label:
        d['label'] = label
    node = {
        'id': nid, 'type': ntype, 'data': d,
        'connections': {'output': []},
    }
    if flagged:
        node['tool_provider'] = True
    return node


def test_collect_brains_accepts_flagged_node_brain():
    """A flagged single node is a brain: kind='node', zero file I/O, and a
    minimal derived description (label + type + one structural hint)."""
    b = _node_brain('0xn1', 'sequence', 'Open the jobs page',
                    {'sequence_file': 'C:/rec/session1.json'})
    orch = _orch_node([b])
    h = _make(orch, [b])

    brains = h._orchestrator_collect_brains(orch)

    assert len(brains) == 1
    assert brains[0]['kind'] == 'node'
    assert brains[0]['chain_file'] == ''
    assert brains[0]['tools'] == []
    assert 'Open the jobs page' in brains[0]['description']
    assert 'sequence' in brains[0]['description']
    assert 'session1.json' in brains[0]['description']
    assert len(brains[0]['description']) <= 120


def test_collect_brains_rejects_unflagged_node_brain():
    """Wired to 'brains' but NOT gated by the builder: it would also run as a
    standalone step — never usable as a brain."""
    b = _node_brain('0xn1', 'sequence', 'x', flagged=False)
    orch = _orch_node([b])
    h = _make(orch, [b])
    assert h._orchestrator_collect_brains(orch) == []


def test_collect_brains_rejects_unsupported_node_type():
    """Input nodes block on user dialogs — never a step."""
    b = _node_brain('0xn1', 'input', 'ask me')
    orch = _orch_node([b])
    h = _make(orch, [b])
    assert h._orchestrator_collect_brains(orch) == []


def test_node_brain_dispatches_through_its_executor():
    """A code brain runs via _execute_code_node (not the chain-import path)
    and its node_<id>_output lands in the orchestrator trace."""
    b = _node_brain('0xn1', 'code', 'Compute the total', {'code': 'result=1'})
    orch = _orch_node([b])
    h = _make(orch, [b])
    calls = []

    def _code(node, stop_flag=None, *a, **k):
        calls.append(node.get('id'))
        h.set_variable('node_0xn1_output', 'total=42')
        return '__done__'

    h._execute_code_node = _code
    h.llm_script = ['0', 'compute the running total', 'DONE', 'Final answer']

    h._execute_orchestrator_node(orch, None)

    assert calls == ['0xn1'], 'the node brain must go through its own executor'
    trace = json.loads(h.get_variable('node_orch1_trace'))
    assert trace[0]['result'] == 'total=42'
    assert h.get_variable('node_orch1_output') == 'Final answer'


def test_conditional_brain_evaluates_once_and_never_branches():
    """A conditional brain is a CHECK: the verdict is published, no branch
    navigation and no loop (graph ownership stays with the main loop)."""
    b = _node_brain('0xc1', 'conditional', 'Is the page open?',
                    {'condition_type': 'code', 'code': 'result=True'})
    orch = _orch_node([b])
    h = _make(orch, [b])
    h._evaluate_conditional = lambda *a, **k: True
    h.llm_script = ['0', 'check the screen', 'DONE', 'S']

    h._execute_orchestrator_node(orch, None)

    assert h.workflow_graph['0xc1']['last_conditional_result'] is True
    trace = json.loads(h.get_variable('node_orch1_trace'))
    assert trace[0]['result'] == 'decision=True'
    assert h.get_variable('chosen_branch_node') is None


def test_invoke_brain_unknown_type_is_skipped_not_crashed():
    h = _Harness()
    assert h._orchestrator_invoke_brain(
        {'name': 'weird', 'node': {'type': 'metric', 'data': {}}}, None,
    ) is False


def test_recording_brain_reports_outcome_not_empty():
    """A sequence brain publishes no text: the honest 'Outcome:' line stands in
    (mirrors chain_ops' recorded-action convention)."""
    b = _node_brain('0xs1', 'sequence', 'Click the button',
                    {'sequence_file': 'C:/rec/s.json'})
    orch = _orch_node([b])
    h = _make(orch, [b])
    h._execute_sequence_node = lambda node, stop_flag=None, *a, **k: '__done__'

    brain = h._orchestrator_collect_brains(orch)[0]
    result = h._orchestrator_collect_brain_result(brain)

    assert result.startswith('Outcome: ran successfully (sequence)')
    assert 'Click the button' in result


def test_node_brain_failure_is_reported_honestly(monkeypatch):
    """A brain whose executor aborts (returns None — the engine's own failure
    convention, the one core turns into 'Node X execution failed') must never
    be dressed as success: the result says FAILED and the verdict is False
    WITHOUT paying a Laya forward (measured 2026-09-23: a failed web sequence
    reported 'ran successfully', Laya verified it True, and the loop closed as
    'done')."""
    import AI.laya_client as lc
    b = _node_brain('0xs1', 'web_sequence', 'Open linkedin.com',
                    {'session_file': 'open linkedin.com.json'})
    orch = _orch_node([b])
    h = _make(orch, [b])
    h._execute_web_sequence_node = lambda node, stop_flag=None, *a, **k: None
    probes = []
    monkeypatch.setattr(lc, 'available', lambda: True)
    monkeypatch.setattr(lc, 'noul', lambda *a, **k: probes.append(1) or 0.9)
    h.llm_script = ['0', 'open linkedin', 'DONE', 'S']

    h._execute_orchestrator_node(orch, None)

    trace = json.loads(h.get_variable('node_orch1_trace'))
    assert trace[0]['result'].startswith('Outcome: FAILED (web_sequence)')
    assert trace[0]['ok'] is False
    assert probes == [], 'a reported failure needs no scorer forward'


def test_node_brain_success_still_verifies_through_laya(monkeypatch):
    """The honest failure path must not swallow the normal one: a brain that
    did return its next node keeps the Laya verdict on the trace."""
    import AI.laya_client as lc
    b = _node_brain('0xs1', 'sequence', 'Click the button',
                    {'sequence_file': 'C:/rec/s.json'})
    orch = _orch_node([b])
    h = _make(orch, [b])
    h._execute_sequence_node = lambda node, stop_flag=None, *a, **k: '__done__'
    monkeypatch.setattr(lc, 'available', lambda: True)
    monkeypatch.setattr(lc, 'noul', lambda state, instructions: 0.9)
    h.llm_script = ['0', 'click it', 'DONE', 'S']

    h._execute_orchestrator_node(orch, None)

    trace = json.loads(h.get_variable('node_orch1_trace'))
    assert trace[0]['ok'] is True


def test_node_brain_description_prefers_the_authored_field():
    """The dialogs' description field wins over the derived label + hint, is
    capped, and a handle node falls back to its goal_description."""
    authored = 'Open the jobs page and click the first easy-apply result'
    b = _node_brain('0xn1', 'sequence', 'Open the jobs page',
                    {'description': authored})
    orch = _orch_node([b])
    h = _make(orch, [b])
    assert h._orchestrator_collect_brains(orch)[0]['description'] == authored

    b2 = _node_brain('0xn2', 'code', 'C', {'description': 'x' * 400})
    orch2 = _orch_node([b2])
    h2 = _make(orch2, [b2])
    assert len(h2._orchestrator_collect_brains(orch2)[0]['description']) == 120

    b3 = _node_brain('0xn3', 'handle', 'See the screen',
                     {'goal_description': 'Read the error dialog'})
    orch3 = _orch_node([b3])
    h3 = _make(orch3, [b3])
    desc3 = h3._orchestrator_collect_brains(orch3)[0]['description']
    assert 'Read the error dialog' in desc3


def test_node_brain_description_falls_back_to_label_and_hint():
    b = _node_brain('0xn1', 'web_sequence', 'Job search',
                    {'session_file': 'search.json', 'repeat_mode': 'per_element'})
    orch = _orch_node([b])
    h = _make(orch, [b])
    desc = h._orchestrator_collect_brains(orch)[0]['description']
    assert desc.startswith('Job search (web_sequence')
    assert 'search.json' in desc
    assert 'per_element' in desc


# ---------------------------------------------------------------------------
# Per-step verification (task #3) — one Laya forward per dispatched step
# ---------------------------------------------------------------------------


def test_verify_step_records_the_laya_verdict(monkeypatch):
    """With the engine up, each trace entry carries the step's verdict."""
    import AI.laya_client as lc
    monkeypatch.setattr(lc, 'available', lambda: True)
    monkeypatch.setattr(
        lc, 'noul',
        lambda state, instructions: 0.9 if 'accomplished what it was asked' in instructions else None,
    )

    b = _brain('0xb1', 'Browser brain')
    orch = _orch_node([b])
    h = _make(orch, [b])
    h.brain_results = {'0xb1': 'jobs page open'}
    h.llm_script = ['0', 'open the jobs page', 'DONE', 'S']

    h._execute_orchestrator_node(orch, None)

    trace = json.loads(h.get_variable('node_orch1_trace'))
    assert trace[0]['ok'] is True


def test_verify_step_returns_false_on_a_low_probability(monkeypatch):
    import AI.laya_client as lc
    monkeypatch.setattr(lc, 'available', lambda: True)
    monkeypatch.setattr(lc, 'noul', lambda state, instructions: 0.1)

    h = _Harness()
    assert h._orchestrator_verify_step('goal', 'step', 'result') is False


# ---------------------------------------------------------------------------
# State digest (task #3) — goal + what was done + the world NOW
# ---------------------------------------------------------------------------


def test_state_digest_is_empty_when_nothing_is_observable(monkeypatch):
    """A blind run behaves exactly as before: no live web driver and no
    desktop probe -> '' and the prompts simply lose the extra block."""
    h = _Harness()
    monkeypatch.setattr(h, '_orchestrator_web_state', lambda: '')
    monkeypatch.setattr(h, '_orchestrator_desktop_state', lambda: '')
    assert h._orchestrator_observe_state() == ''


def test_state_digest_is_bounded_and_exclusive(monkeypatch):
    """The digest reads ONE surface: the cursor's desktop, or - when the
    orchestrator is in web mode - the shared browser.  Each part is capped and
    a throwing probe degrades to ''."""
    from player.multi_sequence.worflow_interpreter_modules.executor_modules \
        import orchestrator_ops as ops
    h = _Harness()
    monkeypatch.setattr(
        h, '_orchestrator_web_state', lambda: 'W' * 5000)
    monkeypatch.setattr(
        h, '_orchestrator_desktop_state',
        lambda: (_ for _ in ()).throw(RuntimeError('boom')))

    # Web orchestrator: the browser digest is read (and capped); the desktop
    # probe is never consulted.
    h._orchestrator_web_mode = True
    assert h._orchestrator_observe_state() == 'W' * ops._STATE_DIGEST_CHARS

    # Desktop orchestrator: the browser digest is ignored entirely.
    h._orchestrator_web_mode = False
    assert h._orchestrator_observe_state() == ''


def test_state_digest_kill_switch(monkeypatch):
    monkeypatch.setenv('LOOPER_ORCH_STATE', 'off')
    h = _Harness()
    monkeypatch.setattr(h, '_orchestrator_web_state', lambda: 'Browser now: x')
    assert h._orchestrator_observe_state() == ''


def test_desktop_digest_reads_the_cursor_monitor(monkeypatch):
    """Multi-monitor: the focused window is reported only when it sits on the
    monitor under the cursor; otherwise the topmost window ON that monitor is
    used instead of the global foreground window (which may be elsewhere)."""
    import sys
    from player.multi_sequence.worflow_interpreter_modules.executor_modules \
        import orchestrator_ops as ops

    monkeypatch.setattr(ops, '_orchestrator_cursor_monitor_rect',
                        lambda: (0, 0, 1920, 1080))

    windows = {
        1: ('Off-screen window', (-2500, 100, -2000, 600)),  # other monitor
        2: ('On-cursor window', (100, 100, 700, 500)),        # cursor monitor
    }

    class _FakeWin32Gui:
        @staticmethod
        def GetForegroundWindow():
            return 1

        @staticmethod
        def GetWindowText(hwnd):
            return windows.get(int(hwnd), ('', (0, 0, 0, 0)))[0]

        @staticmethod
        def GetWindowRect(hwnd):
            return windows.get(int(hwnd), ('', (0, 0, 0, 0)))[1]

        @staticmethod
        def IsWindowVisible(hwnd):
            return True

        @staticmethod
        def EnumWindows(callback, extra):
            for hwnd in windows:
                if callback(hwnd, extra) is False:
                    break

    monkeypatch.setitem(sys.modules, 'win32gui', _FakeWin32Gui)

    digest = _Harness()._orchestrator_desktop_state()
    assert 'On-cursor window' in digest
    assert 'Off-screen window' not in digest


def test_state_reaches_the_verify_scoper_and_picker(monkeypatch):
    """The digest is the loop's third input: the scoping turn, the LLM picker
    and the per-step verdict all carry it, so the model judges by what the
    world shows instead of by the brain's own prose."""
    import AI.laya_client as lc
    h = _Harness()

    b = _node_brain('0xn1', 'code', 'Compute', {'code': 'result=1'})
    orch = _orch_node([b])
    h.workflow_graph = {orch['id']: orch, b['id']: b}
    h.set_variable('_chain_input_context', 'Find the cheapest monitor')
    h._execute_code_node = lambda node, stop_flag=None, *a, **k: '__done__'
    monkeypatch.setattr(
        h, '_orchestrator_observe_state', lambda: 'Browser now: jobs page open')

    premises = []
    monkeypatch.setattr(lc, 'available', lambda: True)
    monkeypatch.setattr(
        lc, 'noul',
        lambda state, instructions: premises.append(state) or 0.9)
    h.llm_script = ['0', 'click the first result', 'DONE', 'S']

    h._execute_orchestrator_node(orch, None)

    assert premises, 'the verify forward must have run'
    assert any('State after the step: Browser now: jobs page open' in p
               for p in premises), premises[:1]
    assert any('CURRENT STATE (observed, not claimed)' in p
               and 'jobs page open' in p for p in h.llm_prompts), \
        'the scoping/picker prompts must observe too'


def test_verify_step_is_none_without_a_running_engine(monkeypatch):
    """No daemon launch for a verdict nothing gates on yet: available() is
    False (engine never started / kill switch) -> None, no call."""
    import AI.laya_client as lc
    monkeypatch.setattr(lc, 'available', lambda: False)
    called = []
    monkeypatch.setattr(lc, 'noul', lambda *a, **k: called.append(1))

    h = _Harness()
    assert h._orchestrator_verify_step('goal', 'step', 'result') is None
    assert called == []

    b = _brain('0xb1', 'Browser brain')
    orch = _orch_node([b])
    h2 = _make(orch, [b])
    h2.brain_results = {'0xb1': 'ok'}
    h2.llm_script = ['0', 'do it', 'DONE', 'S']
    h2._execute_orchestrator_node(orch, None)
    trace = json.loads(h2.get_variable('node_orch1_trace'))
    assert trace[0]['ok'] is None


# ---------------------------------------------------------------------------
# Deterministic chains port (task #4) + learned-chain freeze (task #5)
# ---------------------------------------------------------------------------


def _orch_with_ports(brains=(), chains=()):
    inputs = [
        {'from_node': b['id'], 'input_port': 'brains', 'output_type': 'output'}
        for b in brains
    ]
    inputs += [
        {'from_node': c['id'], 'input_port': 'chains', 'output_type': 'output'}
        for c in chains
    ]
    return {
        'id': 'orch1', 'type': 'orchestrator',
        'data': {'max_steps': '5', 'picker': 'llm', 'synthesize': True},
        'inputs': inputs, 'connections': {'output': []},
    }


def _write_chain(path, cfg):
    path.write_text(json.dumps(cfg), encoding='utf-8')
    return str(path)


def _chain_node(nid, label, path):
    return {
        'id': nid, 'type': 'chain_import',
        'data': {'label': label, 'chain_file_path': path},
        'connections': {'output': []}, 'tool_provider': True,
    }


def test_chains_port_rejects_a_brain_chain(tmp_path):
    """The single rule, re-validated at runtime: a chain that contains an
    orchestrator can never run as a deterministic chain."""
    brain_file = _write_chain(
        tmp_path / 'nested_brain.json',
        {'orchestrator_nodes': [{'node_id': 'o2'}], 'description': 'needs inference'})
    c = _chain_node('0xc1', 'Nested brain', brain_file)
    orch = _orch_with_ports(chains=[c])
    h = _make(orch, [c])

    assert h._orchestrator_collect_chains(orch) == []


def test_direct_chain_hit_skips_the_brain_loop(tmp_path, monkeypatch):
    import AI.laya_client as lc
    lib = _write_chain(
        tmp_path / 'lib_chain.json',
        {'description': 'opens the jobs page',
         'learned': True,
         'routing': {'examples': ['open the jobs page on linkedin']}})

    b = _brain('0xb1', 'Browser brain')  # present, must NOT run
    c = _chain_node('0xc1', 'Jobs chain', lib)
    orch = _orch_with_ports(brains=[b], chains=[c])
    h = _make(orch, [b, c], goal='open the jobs page on linkedin')
    calls = []

    monkeypatch.setattr(lc, 'available', lambda: True)
    monkeypatch.setattr(lc, 'noul', lambda state, instructions: 0.9)

    def _chain(node, stop_flag=None, *a, **k):
        calls.append(node.get('id'))
        h.set_variable('chain_tool_last_result_text', 'jobs page open')
        return '__done__'

    h._execute_chain_import_node = _chain
    # The learned chain matches the request exactly, so the run ends with the
    # synthesis turn only.
    h.llm_script = ['final answer']

    h._execute_orchestrator_node(orch, None)

    assert calls == ['0xc1'], 'the learned chain runs; no brain is dispatched'
    trace = json.loads(h.get_variable('node_orch1_trace'))
    # The direct chain records the directive it served as its step text (a
    # '(frozen chain)' placeholder told the done-probe nothing about what ran
    # — measured 2026-09-25: an accomplished observation read p_done 0.039).
    assert trace[0]['step'] == 'open the jobs page on linkedin'
    assert trace[0]['ok'] is True
    assert h.get_variable('node_orch1_output') == 'final answer'
    assert h.executed == [], 'the brain loop never started'


def _sealed_chain_identity(h, tmp_path):
    """Give the harness a REAL chain file in tmp_path as its identity.

    Without one, ``_orchestrator_source_chain_file`` falls back to the shipped
    chains dir — and a passing run would freeze a learned chain INTO THE REPO
    (measured 2026-09-23: a test with two verified direct-chain steps wrote
    four learned files and four dangling imports into chains/ORCHESTRATOR.json).
    """
    orch_file = tmp_path / 'ORCHESTRATOR.json'
    orch_file.write_text(json.dumps({
        'orchestrator_nodes': [{'node_id': 'orch1', 'connections': []}],
        'chain_import_nodes': [],
    }), encoding='utf-8')
    h.chain_identity = str(orch_file)
    return orch_file


# ---------------------------------------------------------------------------
# The ladder (task #4): whole-job chain first, directives only if none fits
# ---------------------------------------------------------------------------


def test_partial_handback_reroutes_only_the_untouched_tail(tmp_path, monkeypatch):
    """The nested brain reports the directives it could NOT finish (marker on
    its output); the parent advances past the prefix that WAS accomplished and
    re-dispatches only the tail."""
    import AI.laya_client as lc
    nested = _write_chain(tmp_path / 'nested.json',
                          {'orchestrator_nodes': [{'node_id': 'o1'}]})
    b = _brain('0xb1', 'LinkedIn brain', str(nested))
    orch = _orch_node([b])
    goal = 'open linkedin.com and click on my messages'
    h = _make(orch, [b], goal=goal)
    h.directive_plan = ['open linkedin.com', 'click on my messages']
    dispatched = []

    monkeypatch.setattr(lc, 'available', lambda: True)
    monkeypatch.setattr(lc, 'noul', lambda state, instructions: 0.1)  # never verified

    def _chain(node, stop_flag=None, *a, **k):
        dispatched.append(h.get_variable('_chain_step'))
        # First pass: the brain opened LinkedIn but could not click messages.
        h.set_variable(
            'chain_tool_last_result_text',
            'LinkedIn is open.\n[ORCH-REMAINING] ["click on my messages"]')
        return '__done__'

    h._execute_chain_import_node = _chain
    h.llm_script = ['0', '0', 'DONE', 'S']

    h._execute_orchestrator_node(orch, None)

    assert len(dispatched) >= 2, dispatched
    assert 'open linkedin.com' in dispatched[0]
    assert 'click on my messages' in dispatched[1], dispatched[1]
    assert 'open linkedin.com' not in dispatched[1], \
        'the accomplished prefix must not be re-routed'
    # The marker never reaches the trace prose.
    trace = json.loads(h.get_variable('node_orch1_trace'))
    assert '[ORCH-REMAINING]' not in trace[0]['result']
    assert trace[0]['result'].startswith('LinkedIn is open.')


def test_an_unaligned_handback_report_is_ignored(tmp_path, monkeypatch):
    """A report that does not line up with the parent's span changes nothing:
    the parent keeps its own view and the next route still gets the FULL span."""
    import AI.laya_client as lc
    nested = _write_chain(tmp_path / 'nested.json',
                          {'orchestrator_nodes': [{'node_id': 'o1'}]})
    nested2 = _write_chain(tmp_path / 'nested2.json',
                           {'orchestrator_nodes': [{'node_id': 'o2'}]})
    b1 = _brain('0xb1', 'LinkedIn brain', str(nested))
    b2 = _brain('0xb2', 'Vision brain', str(nested2))
    orch = _orch_node([b1, b2])
    goal = 'open linkedin.com and click on my messages'
    h = _make(orch, [b1, b2], goal=goal)
    h.directive_plan = ['open linkedin.com', 'click on my messages']
    dispatched = []

    monkeypatch.setattr(lc, 'available', lambda: True)
    monkeypatch.setattr(lc, 'noul', lambda state, instructions: 0.1)

    def _chain(node, stop_flag=None, *a, **k):
        dispatched.append((node.get('id'), h.get_variable('_chain_step')))
        h.set_variable('chain_tool_last_result_text',
                       'Could not finish.\n[ORCH-REMAINING] ["something else"]')
        return '__done__'

    h._execute_chain_import_node = _chain
    h.llm_script = ['0', '0', 'DONE', 'S']

    h._execute_orchestrator_node(orch, None)

    assert len(dispatched) == 2, dispatched
    assert 'open linkedin.com' in dispatched[1][1] and \
        'click on my messages' in dispatched[1][1], dispatched[1][1]


def test_orchestrator_publishes_its_own_remaining_work(tmp_path, monkeypatch):
    """The other half: a RELAYED orchestrator (invoked by a parent) that ends
    with work outstanding appends the marker to its published output — and a
    top-level answer is never decorated with it."""
    import AI.laya_client as lc
    lib = _write_chain(tmp_path / 'lib.json', {'description': 'opens jobs'})
    c = _chain_node('0xc1', 'Jobs chain', lib)
    orch = _orch_with_ports(chains=[c])          # no brains: ends 'no_worker'
    h = _make(orch, [c], goal='open linkedin.com and click on my messages')
    h.directive_plan = ['open linkedin.com', 'click on my messages']
    # A relayed run: the goal arrives inside the parent's composed prompt.
    h.set_variable(
        '_chain_input_context',
        'Long-term goal: parent goal\nProgress so far: (nothing yet)\n'
        'Your next step: open linkedin.com and click on my messages')

    monkeypatch.setattr(lc, 'available', lambda: True)
    monkeypatch.setattr(lc, 'noul', lambda state, instructions: 0.1)  # nothing routes

    h._execute_orchestrator_node(orch, None)

    out = h.get_variable('node_orch1_output')
    assert '[ORCH-REMAINING]' in out, out
    assert 'open linkedin.com' in out and 'click on my messages' in out
    # A done run carries no marker; the parser strips it either way.
    assert h._orchestrator_split_remaining(
        'All good.\n[ORCH-REMAINING] ["x"]') == ('All good.', ['x'])


def test_route_wired_run_withholds_the_parent_handback(tmp_path, monkeypatch):
    """With 'route' wired the canvas owns the retry: a relayed run does NOT
    append the [ORCH-REMAINING] marker (no double-handling by the parent)."""
    import AI.laya_client as lc
    lib = _write_chain(tmp_path / 'lib.json', {'description': 'opens jobs'})
    c = _chain_node('0xc1', 'Jobs chain', lib)
    orch = _orch_with_ports(chains=[c])          # no brains: ends 'no_worker'
    orch['connections']['route'] = [{'node_id': '0xb9', 'input_port': 'input'}]
    h = _make(orch, [c], goal='open linkedin.com and click on my messages')
    h.directive_plan = ['open linkedin.com', 'click on my messages']
    h.set_variable(
        '_chain_input_context',
        'Long-term goal: parent goal\nProgress so far: (nothing yet)\n'
        'Your next step: open linkedin.com and click on my messages')

    monkeypatch.setattr(lc, 'available', lambda: True)
    monkeypatch.setattr(lc, 'noul', lambda state, instructions: 0.1)

    h._execute_orchestrator_node(orch, None)

    out = h.get_variable('node_orch1_output')
    assert '[ORCH-REMAINING]' not in out, out
    assert orch.get('_orchestrator_fulfilled') is False


def test_handback_prefers_a_different_brain_next(tmp_path, monkeypatch):
    """After a nested brain hands the work back, the parent re-evaluates with a
    DIFFERENT route: the brain that came back empty for this span is not offered
    again while another brain is available (it returns only when nothing else
    is left)."""
    import AI.laya_client as lc
    nested = _write_chain(tmp_path / 'nested.json',
                          {'orchestrator_nodes': [{'node_id': 'o1'}]})
    plain = _write_chain(tmp_path / 'plain.json', {'description': 'sees the screen'})

    b1 = _brain('0xb1', 'LinkedIn brain', str(nested))
    b2 = _brain('0xb2', 'Vision brain', str(plain))
    orch = _orch_node([b1, b2])
    goal = 'open linkedin.com and click on my messages'
    h = _make(orch, [b1, b2], goal=goal)
    h.directive_plan = ['open linkedin.com', 'click on my messages']
    picked = []

    monkeypatch.setattr(lc, 'available', lambda: True)
    monkeypatch.setattr(lc, 'noul', lambda state, instructions: 0.9)

    def _chain(node, stop_flag=None, *a, **k):
        picked.append(node.get('id'))
        # The nested brain fails (executor reports failure); the specialist
        # verifies fine and takes the span over.
        if node.get('id') == '0xb1':
            return None
        h.set_variable('chain_tool_last_result_text', 'messages inbox open')
        return '__done__'

    h._execute_chain_import_node = _chain
    h.llm_script = ['0', '1', 'S']

    h._execute_orchestrator_node(orch, None)

    assert picked == ['0xb1', '0xb2'], picked


def test_nested_brain_takes_the_task_list_specialist_takes_one_step(
        tmp_path, monkeypatch):
    """The recursion contract: a brain that is ITSELF an orchestrator takes the
    remaining directive LIST (it re-evaluates at its level and hands the
    unaccomplished work back); a specialist brain keeps the one-step contract.
    A verified delegated list completes the whole span."""
    import AI.laya_client as lc
    nested = _write_chain(tmp_path / 'nested.json',
                          {'orchestrator_nodes': [{'node_id': 'o1'}],
                           'description': 'does linkedin work'})

    b_nested = _brain('0xb1', 'LinkedIn brain', str(nested))
    orch = _orch_node([b_nested])
    goal = 'open linkedin.com and click on my messages'
    h = _make(orch, [b_nested], goal=goal)
    h.directive_plan = ['open linkedin.com', 'click on my messages']
    steps = []

    monkeypatch.setattr(lc, 'available', lambda: True)
    monkeypatch.setattr(lc, 'noul', lambda state, instructions: 0.9)

    def _chain(node, stop_flag=None, *a, **k):
        steps.append(h.get_variable('_chain_step'))
        h.set_variable('chain_tool_last_result_text', 'messages inbox open')
        return '__done__'

    h._execute_chain_import_node = _chain
    h.llm_script = ['0', 'S']

    h._execute_orchestrator_node(orch, None)

    assert len(steps) == 1, 'a delegated list is ONE dispatch, not one per step'
    assert '1) open linkedin.com' in steps[0] and '2) click on my messages' in steps[0]
    assert 'CURRENT STATE' not in steps[0], 'the list IS the task, no scoping turn'
    trace = json.loads(h.get_variable('node_orch1_trace'))
    assert len(trace) == 1 and trace[0]['ok'] is True


def test_specialist_brain_still_gets_the_one_scoped_step(tmp_path, monkeypatch):
    """A chain WITHOUT an orchestrator is a specialist: it never receives a
    task list — the orchestrator scopes one directive for it."""
    import AI.laya_client as lc
    plain = _write_chain(tmp_path / 'plain.json',
                         {'description': 'clicks on the "messages" button'})

    b = _brain('0xb1', 'Messages chain', str(plain))
    orch = _orch_node([b])
    h = _make(orch, [b], goal='click on my messages')
    steps = []

    monkeypatch.setattr(lc, 'available', lambda: True)
    monkeypatch.setattr(lc, 'noul', lambda state, instructions: 0.9)

    def _chain(node, stop_flag=None, *a, **k):
        steps.append(h.get_variable('_chain_step'))
        h.set_variable('chain_tool_last_result_text', 'inbox open')
        return '__done__'

    h._execute_chain_import_node = _chain
    h.llm_script = ['0', 'click the messages button', 'DONE', 'S']

    h._execute_orchestrator_node(orch, None)

    assert steps == ['click the messages button'], steps


def test_delegated_brain_reports_the_unaccomplished_work(
        tmp_path, monkeypatch, caplog):
    """Handback: when the nested brain comes back without the work verified,
    the parent keeps its directives and logs what is left for re-evaluation."""
    import logging as _logging

    import AI.laya_client as lc
    nested = _write_chain(tmp_path / 'nested.json',
                          {'orchestrator_nodes': [{'node_id': 'o1'}]})
    b = _brain('0xb1', 'LinkedIn brain', str(nested))
    orch = _orch_node([b])
    goal = 'open linkedin.com and click on my messages'
    h = _make(orch, [b], goal=goal)
    h.directive_plan = ['open linkedin.com', 'click on my messages']

    monkeypatch.setattr(lc, 'available', lambda: True)
    # The step is reported as FAILED by the executor -> verdict False.
    h._execute_chain_import_node = lambda node, stop_flag=None, *a, **k: None
    h.llm_script = ['0', 'DONE', 'S']

    with caplog.at_level(_logging.INFO, logger='player.multi_sequence'
                         '.worflow_interpreter_modules.executor_modules'
                         '.orchestrator_ops'):
        h._execute_orchestrator_node(orch, None)

    assert any('unaccomplished' in r.message and 'handed back' in r.message
               for r in caplog.records), [r.message for r in caplog.records]
    trace = json.loads(h.get_variable('node_orch1_trace'))
    assert trace[0]['ok'] is False


def test_whole_job_chain_runs_without_splitting(tmp_path, monkeypatch):
    """A learned chain whose routing.examples carry the request verbatim wins
    the whole-job rung deterministically (content-word set equality): it runs
    as the WHOLE answer, no planning turn is paid and no per-directive routing
    happens."""
    import AI.laya_client as lc
    lib = _write_chain(tmp_path / 'learned.json', {
        'description': 'opens linkedin and clicks the messages button',
        'learned': True,
        'routing': {'examples': ['open linkedin.com and click on my messages']}})

    b = _brain('0xb1', 'Browser brain')  # must NOT run
    c = _chain_node('0xc1', 'LinkedIn whole job', lib)
    orch = _orch_with_ports(brains=[b], chains=[c])
    h = _make(orch, [b, c], goal='open linkedin.com and click on my messages')
    _sealed_chain_identity(h, tmp_path)
    calls = []

    monkeypatch.setattr(lc, 'available', lambda: True)

    def _noul(state, instructions):
        if 'accomplished what it was asked' in instructions:
            return 0.9
        if 'whole request end to end' in instructions:
            return 0.9                     # the chain DOES the whole job
        return 0.1

    monkeypatch.setattr(lc, 'noul', _noul)

    def _chain(node, stop_flag=None, *a, **k):
        calls.append(node.get('id'))
        h.set_variable('chain_tool_last_result_text', 'messages inbox open')
        return '__done__'

    h._execute_chain_import_node = _chain
    h.llm_script = ['S']

    h._execute_orchestrator_node(orch, None)

    assert calls == ['0xc1'], 'the whole-job chain runs as the whole answer'
    trace = json.loads(h.get_variable('node_orch1_trace'))
    assert len(trace) == 1 and trace[0]['ok'] is True
    assert h.plan_calls == [], 'no planning turn when one chain does it all'
    # Lifecycle bookkeeping: the execution is counted on the artifact file
    # (runs always, wins when it verified) — the eviction policy reads these.
    payload = json.loads(Path(lib).read_text(encoding='utf-8'))
    assert payload['routing']['runs'] == 1
    assert payload['routing']['wins'] == 1


def test_whole_job_rung_needs_exact_coverage(tmp_path, monkeypatch):
    """Set equality is the safety property, not a heuristic: the request must
    cover everything the learned chain does (no unasked action) and nothing
    less (no step left undone).  Rewording, order and numbering are irrelevant
    — the deterministic match ignores them all."""
    monkeypatch.delenv('LOOPER_LEARN', raising=False)
    go = _write_chain(tmp_path / 'go to linkedin.json',
                      {'description': 'navigates to linkedin'})
    net = _write_chain(tmp_path / 'my network.json',
                       {'description': 'clicks on the my network button'})
    msg = _write_chain(tmp_path / 'messages.json',
                       {'description': 'clicks on the messages button'})
    h = _Harness()
    artifact = h._orchestrator_write_learned_chain(
        'frozen request text',
        [(str(go), 'go to linkedin'), (str(net), 'my network'),
         (str(msg), 'messages')])
    c = _chain_node('0xc1', 'LinkedIn chain', artifact)
    h.workflow_graph = {'0xc1': c}
    chains = h._orchestrator_collect_chains(
        {'inputs': [{'from_node': '0xc1', 'input_port': 'chains'}]})
    assert [x['learned'] for x in chains] == [True]
    # A realistic corpus: the library's shared verbs are generic for it, so
    # they never decide an action match (the wired decoys are not candidates).
    chains.extend([
        {'learned': False, 'signal': 'clicks on the settings page',
         'examples': [], 'chain_file': '', 'name': 'decoy1'},
        {'learned': False, 'signal': 'opens the profile and clicks done',
         'examples': [], 'chain_file': '', 'name': 'decoy2'},
    ])

    # The real repeat: reworded, re-ordered, un-numbered.
    got = h._orchestrator_route_whole_job(
        'navigate to linkedin, click on my messages and then my network', chains)
    assert got is not None and got['chain_id'] == '0xc1'

    # 2 of 3 actions: running the chain would click 'my network' unasked.
    assert h._orchestrator_route_whole_job(
        'navigate to linkedin and click on my messages', chains) is None
    # 4 of 3: the chain would leave the 'jobs' step undone.
    assert h._orchestrator_route_whole_job(
        'navigate to linkedin, click my network, click my messages and click '
        'jobs', chains) is None
    # A different task entirely.
    assert h._orchestrator_route_whole_job(
        'send an email to the team', chains) is None


def test_a_losing_learned_chain_is_retired_not_rerun(tmp_path, monkeypatch):
    """Lifecycle policy (measured 2026-09-25: the counters were written as 0
    and never updated — bad artifacts stayed matchable for the life of the
    install).  An artifact with enough runs and a losing record retires in
    place: marked on its own file, skipped from then on; a healthy one with
    the same shape still matches."""
    monkeypatch.delenv('LOOPER_LEARN', raising=False)
    loser = tmp_path / 'learned_loser.json'
    loser.write_text(json.dumps({
        'description': 'opens linkedin and clicks the messages button',
        'learned': True,
        'routing': {'examples': ['open linkedin.com and click on my messages'],
                    'runs': 5, 'wins': 0},
    }), encoding='utf-8')
    h = _Harness()
    c = _chain_node('0xc1', 'Loser', str(loser))
    h.workflow_graph = {'0xc1': c}
    chains = h._orchestrator_collect_chains(
        {'inputs': [{'from_node': '0xc1', 'input_port': 'chains'}]})
    assert chains[0]['runs'] == 5 and chains[0]['wins'] == 0

    got = h._orchestrator_route_whole_job(
        'open linkedin.com and click on my messages', chains)
    assert got is None, 'a losing artifact is never offered'
    assert json.loads(loser.read_text(encoding='utf-8'))['retired'] is True

    # Control: the same artifact with a winning record still matches.
    winner = tmp_path / 'learned_winner.json'
    winner.write_text(json.dumps({
        'description': 'opens linkedin and clicks the messages button',
        'learned': True,
        'routing': {'examples': ['open linkedin.com and click on my messages'],
                    'runs': 4, 'wins': 4},
    }), encoding='utf-8')
    h.workflow_graph = {'0xc2': _chain_node('0xc2', 'Winner', str(winner))}
    chains2 = h._orchestrator_collect_chains(
        {'inputs': [{'from_node': '0xc2', 'input_port': 'chains'}]})
    got2 = h._orchestrator_route_whole_job(
        'open linkedin.com and click on my messages', chains2)
    assert got2 is not None and got2['chain_id'] == '0xc2'


def test_composition_route_matches_a_reworded_request(tmp_path, monkeypatch):
    """The orchestrator judges a learned chain by WHAT IT DOES: with the steps'
    descriptions expanded (the composition), a reworded request that covers
    every step — and asks for nothing else — is recognised even though it
    matches no frozen example string."""
    monkeypatch.delenv('LOOPER_LEARN', raising=False)
    go = _write_chain(tmp_path / 'go to linkedin.json',
                      {'description': 'navigates to linkedin'})
    msg = _write_chain(tmp_path / 'messages.json',
                       {'description': 'clicks on the messages button'})
    h = _Harness()
    artifact = h._orchestrator_write_learned_chain(
        'frozen goal text', [(str(go), 'go to linkedin'),
                             (str(msg), 'messages')])
    assert artifact

    c = _chain_node('0xc1', 'Frozen', artifact)
    h.workflow_graph = {'0xc1': c}
    chains = h._orchestrator_collect_chains(
        {'inputs': [{'from_node': '0xc1', 'input_port': 'chains'}]})
    learned = chains[0]
    assert learned['learned'] is True
    assert 'navigates to linkedin' in learned['composition']
    # The artifact PERSISTS the step list (derived from each atomic chain), so
    # the expansion needs no file reads.
    assert [s['name'] for s in json.loads(
        open(artifact, encoding='utf-8').read()).get('steps', [])] == [
        'go to linkedin', 'messages']
    chains.extend([
        {'learned': False, 'signal': 'clicks on the settings page',
         'examples': [], 'chain_file': '', 'name': 'decoy1'},
        {'learned': False, 'signal': 'opens the profile and clicks done',
         'examples': [], 'chain_file': '', 'name': 'decoy2'},
    ])

    # Reworded: no example match, but every step is asked for, nothing extra.
    got = h._orchestrator_route_whole_job(
        'navigate to linkedin and click the messages button', chains)
    assert got is not None and got['chain_id'] == '0xc1'

    # A step nobody asked for (would run an unasked action) declines.
    assert h._orchestrator_route_whole_job('navigate to linkedin', chains) is None
    # An action no step serves (would be left undone) declines.
    assert h._orchestrator_route_whole_job(
        'navigate to linkedin, click the messages button and press enter',
        chains) is None


def test_learned_artifact_describes_itself_in_steps(tmp_path, monkeypatch):
    """The artifact says IN STEPS what it does: each step's description is
    derived from the atomic chain it imports and persists with the artifact —
    the orchestrator (and a LATER artifact composing this one) reads the steps
    without opening a single file."""
    monkeypatch.delenv('LOOPER_LEARN', raising=False)
    go = _write_chain(tmp_path / 'go to linkedin.json',
                      {'description': 'navigates to linkedin'})
    msg = _write_chain(tmp_path / 'messages.json',
                       {'description': 'clicks on the messages button'})
    h = _Harness()
    artifact = h._orchestrator_write_learned_chain(
        'open linkedin and click messages',
        [(str(go), 'go to linkedin'), (str(msg), 'messages')])
    cfg = json.loads(open(artifact, encoding='utf-8').read())
    assert cfg['steps'] == [
        {'name': 'go to linkedin', 'description': 'navigates to linkedin',
         'file': str(go)},
        {'name': 'messages', 'description': 'clicks on the messages button',
         'file': str(msg)},
    ]
    assert cfg['description'] == (
        'Learned from a verified run: '
        '1) go to linkedin — navigates to linkedin; '
        '2) messages — clicks on the messages button')
    # The expansion reads the PERSISTED steps: remove the atomics and it still
    # knows the composition (and its keys).
    os.remove(go)
    os.remove(msg)
    comp, keys, _files = h._orchestrator_expand_learned_chain(cfg, artifact)
    assert 'navigates to linkedin' in comp
    assert keys == [['linkedin', 'navigate'], ['button', 'click', 'message']]


def test_plan_units_prefers_the_artifact_and_declines_ambiguity(
        tmp_path, monkeypatch):
    """The unit planner reuses a WHOLE learned artifact for the span it covers
    (longest run first), serves the rest with atoms, and never guesses: two
    candidates for one run decline the whole plan."""
    monkeypatch.delenv('LOOPER_LEARN', raising=False)
    go = _write_chain(tmp_path / 'go to linkedin.json',
                      {'description': 'navigates to linkedin.com'})
    msg = _write_chain(tmp_path / 'messages.json',
                       {'description': 'clicks on the messages button'})
    jobs = _write_chain(tmp_path / 'jobs.json',
                        {'description': 'clicks on the jobs button'})
    host = tmp_path / 'ORCHESTRATOR.json'
    host.write_text('{}', encoding='utf-8')

    h = _Harness()
    h.chain_identity = str(host)
    artifact = h._orchestrator_write_learned_chain(
        'open linkedin and click messages',
        [(str(go), 'go to linkedin'), (str(msg), 'messages')])
    assert artifact

    h.workflow_graph = {
        '0xc1': _chain_node('0xc1', 'Frozen', artifact),
        '0xc2': _chain_node('0xc2', 'Jobs chain', jobs),
    }
    chains = h._orchestrator_collect_chains({'inputs': [
        {'from_node': '0xc1', 'input_port': 'chains'},
        {'from_node': '0xc2', 'input_port': 'chains'},
    ]})
    assert [c['learned'] for c in chains] == [True, False]
    # A realistic corpus: this library's shared verbs (click / open) are
    # generic FOR IT, so they cannot make one action pass for another.
    chains.extend([
        {'learned': False, 'signal': 'clicks on the settings page',
         'examples': [], 'chain_file': '', 'name': 'decoy1'},
        {'learned': False, 'signal': 'opens the profile and clicks done',
         'examples': [], 'chain_file': '', 'name': 'decoy2'},
    ])

    # The artifact covers the first two directives as ONE unit, jobs closes it.
    plan = h._orchestrator_plan_units(
        ['navigate to linkedin', 'click on my messages', 'click the jobs'],
        chains)
    assert plan is not None and len(plan) == 2, plan
    assert plan[0]['kind'] == 'artifact' and plan[0]['file'] == artifact
    assert plan[1]['kind'] == 'step' and plan[1]['file'] == str(jobs)

    # 'the button' is in two descriptions: no guess, no plan, normal ladder.
    assert h._orchestrator_plan_units(['click the button'], chains) is None


def test_assembly_reuses_a_learned_chain_and_persists_the_variation(
        tmp_path, monkeypatch):
    """A verified assembly becomes the new learned artifact for that variation
    — composed of the WHOLE learned chain plus an atom (learned chains made of
    learned chains), renamed out of '.pending_' and wired to the port."""
    import AI.laya_client as lc
    monkeypatch.delenv('LOOPER_LEARN', raising=False)
    go = _write_chain(tmp_path / 'go to linkedin.json',
                      {'description': 'navigates to linkedin.com'})
    msg = _write_chain(tmp_path / 'messages.json',
                       {'description': 'clicks on the messages button'})
    jobs = _write_chain(tmp_path / 'jobs.json',
                        {'description': 'clicks on the jobs button'})

    h = _Harness()
    artifact = h._orchestrator_write_learned_chain(
        'open linkedin and click messages',
        [(str(go), 'go to linkedin'), (str(msg), 'messages')])
    assert artifact

    c1 = _chain_node('0xc1', 'Frozen', artifact)
    c2 = _chain_node('0xc2', 'Jobs chain', jobs)
    orch = _orch_with_ports(chains=[c1, c2])
    h = _make(orch, [c1, c2], goal='open linkedin, click messages and jobs')
    # Seal AFTER _make: a harness without a real chain identity would resolve
    # the shipped chains dir and leak the variation into the repo.
    _sealed_chain_identity(h, tmp_path)
    h.directive_plan = ['navigate to linkedin', 'click on my messages',
                        'click the jobs']
    dispatched = []

    monkeypatch.setattr(lc, 'available', lambda: True)
    monkeypatch.setattr(lc, 'noul', lambda state, instructions: 0.9)

    def _chain(node, stop_flag=None, *a, **k):
        dispatched.append(
            str((node.get('data') or {}).get('chain_file_path') or ''))
        h.set_variable('chain_tool_last_result_text', 'done')
        return '__done__'

    h._execute_chain_import_node = _chain
    h.llm_script = ['S']

    h._execute_orchestrator_node(orch, None)

    assert len(dispatched) == 1, 'ONE assembled dispatch, no per-step routing'
    assert '.pending_' in dispatched[0], dispatched
    final = list((tmp_path / 'learned').glob('learned_*.json'))
    assert len(final) == 1, final
    assert not list((tmp_path / 'learned').glob('.pending_*'))
    cfg = json.loads(final[0].read_text(encoding='utf-8'))
    assert cfg['learned'] is True
    steps = [str(n.get('chain_file_path')) for n in cfg['chain_import_nodes']]
    assert steps == [artifact, str(jobs)], \
        'the variation composes the learned chain itself plus an atom'
    # And it is wired to the port of the chain that hosts this orchestrator.
    host_cfg = json.loads((tmp_path / 'ORCHESTRATOR.json')
                          .read_text(encoding='utf-8'))
    assert any('learned_' in str(n.get('chain_file_path'))
               for n in host_cfg['chain_import_nodes'])


def test_assembly_failure_removes_the_pending_variation(tmp_path, monkeypatch):
    """A failed verification deletes the pending file and the normal ladder
    takes over: an unverified composition is never left on disk."""
    import AI.laya_client as lc
    monkeypatch.delenv('LOOPER_LEARN', raising=False)
    go = _write_chain(tmp_path / 'go to linkedin.json',
                      {'description': 'navigates to linkedin.com'})
    msg = _write_chain(tmp_path / 'messages.json',
                       {'description': 'clicks on the messages button'})
    jobs = _write_chain(tmp_path / 'jobs.json',
                        {'description': 'clicks on the jobs button'})

    h = _Harness()
    artifact = h._orchestrator_write_learned_chain(
        'open linkedin and click messages',
        [(str(go), 'go to linkedin'), (str(msg), 'messages')])

    c1 = _chain_node('0xc1', 'Frozen', artifact)
    c2 = _chain_node('0xc2', 'Jobs chain', jobs)
    orch = _orch_with_ports(chains=[c1, c2])  # no brains: chains only
    h = _make(orch, [c1, c2], goal='open linkedin, click messages and jobs')
    _sealed_chain_identity(h, tmp_path)  # after _make — see the test above
    h.directive_plan = ['open linkedin.com', 'click on my messages',
                        'click the jobs']

    monkeypatch.setattr(lc, 'available', lambda: True)
    monkeypatch.setattr(lc, 'noul', lambda state, instructions: 0.1)
    h._execute_chain_import_node = (
        lambda node, stop_flag=None, *a, **k: '__done__')

    h._execute_orchestrator_node(orch, None)

    assert not list((tmp_path / 'learned').glob('.pending_*')), \
        'the unverified variation is removed'
    assert not list((tmp_path / 'learned').glob('learned_*.json')), \
        'nothing unverified is persisted (the fixture lives in its own temp)'
    assert not any('learned_' in str(n.get('chain_file_path'))
                   for n in json.loads((tmp_path / 'ORCHESTRATOR.json')
                                       .read_text(encoding='utf-8'))
                   ['chain_import_nodes']), 'and nothing is wired'


def test_scope_dropping_a_compound_directives_action_is_refused(
        tmp_path, monkeypatch):
    """A scope that drops one of a COMPOUND directive's actions is refused and
    handed back instead of dispatched (measured 2026-09-24: the scope dropped
    'home' and a job-search brain ran, losing the user's last action)."""
    nested = _write_chain(tmp_path / 'nested.json',
                          {'orchestrator_nodes': [{'node_id': 'o1'}]})
    b = _brain('0xb1', 'Job brain', str(nested))
    orch = _orch_node([b])
    d = ('navigate to linkedin.com, click on my network and then click on '
         'the home button')
    h = _make(orch, [b], goal=d)
    h.set_variable('_chain_input_context', 'Long-term goal: ' + d)  # relayed
    h.directive_plan = [d]  # the parser is bypassed: feed the compound directly
    h.llm_script = ['0', 'Open LinkedIn.com and click on your network.']
    h._execute_chain_import_node = lambda *a, **k: '__done__'

    h._execute_orchestrator_node(orch, None)

    assert h.executed == [], 'the dispatch was refused'
    out = h.get_variable('node_orch1_output') or ''
    assert '[ORCH-REMAINING]' in out, 'the directive is handed back'
    assert 'home' in out


def test_reported_remaining_overrides_a_true_verdict(tmp_path, monkeypatch):
    """A nested brain that REPORTS unfinished work is never treated as done,
    even when the verification read says True: the parent advances only past
    the accomplished prefix and re-routes the tail (without this, the marker
    was cosmetic and the last action vanished)."""
    import AI.laya_client as lc
    nested = _write_chain(tmp_path / 'nested.json',
                          {'orchestrator_nodes': [{'node_id': 'o1'}]})
    b = _brain('0xb1', 'LinkedIn brain', str(nested))
    orch = _orch_node([b])
    h = _make(orch, [b], goal='open linkedin and click network')
    h.directive_plan = ['open linkedin', 'click on my network']
    h.set_variable('_chain_input_context', 'Long-term goal: x')  # relayed
    steps = []
    texts = ['LinkedIn is open.\n[ORCH-REMAINING] ["click on my network"]',
             'messages open']

    monkeypatch.setattr(lc, 'available', lambda: True)
    monkeypatch.setattr(lc, 'noul', lambda state, instructions: 0.9)

    def _chain(node, stop_flag=None, *a, **k):
        steps.append(h.get_variable('_chain_step'))
        h.set_variable('chain_tool_last_result_text', texts.pop(0))
        return '__done__'

    h._execute_chain_import_node = _chain
    h.llm_script = ['0', '0']

    h._execute_orchestrator_node(orch, None)

    assert len(steps) == 2, steps
    assert 'click on my network' in steps[1], steps[1]
    assert 'open linkedin' not in steps[1], \
        'only the untouched tail is re-routed'


def test_a_mission_composed_relay_is_still_recognized(tmp_path):
    """The composer opens the worker prompt with 'Your mission:' while the
    relay detection read only the legacy 'Long-term goal:' — a nested brain
    was then judged a TOP-LEVEL run, its handback marker was suppressed, and
    a sub-run that did NOTHING was believed finished (measured 2026-09-25:
    'plan complete — all 4 directive(s) executed and verified' over a
    sub-run with ZERO steps).  Either scaffolding marker means relayed."""
    nested = _write_chain(tmp_path / 'nested.json',
                          {'orchestrator_nodes': [{'node_id': 'o1'}]})
    b = _brain('0xb1', 'Job brain', str(nested))
    orch = _orch_node([b])
    d = ('navigate to linkedin.com, click on my network and then click on '
         'the home button')
    h = _make(orch, [b], goal=d)
    h.set_variable('_chain_input_context', 'Your mission: ' + d)  # relayed
    h.directive_plan = [d]
    h.llm_script = ['0']
    h._execute_chain_import_node = lambda *a, **k: '__done__'

    h._execute_orchestrator_node(orch, None)

    out = h.get_variable('node_orch1_output') or ''
    assert '[ORCH-REMAINING]' in out, 'the relayed run hands its work back'
    assert 'home' in out


def test_a_reported_remaining_refuses_the_done_pick(tmp_path):
    """A probe 'done' is never believed while a brain has just REPORTED
    directives unaccomplished, and that brain is not re-offered for the span
    before the other workers had their turn (measured 2026-09-25 live: the
    done-probe read 0.587 and closed the run over five directives the brain
    itself had handed back, and the same brain was re-picked for the next
    step).  Structure beats the probe."""
    nested = _write_chain(tmp_path / 'nested.json',
                          {'orchestrator_nodes': [{'node_id': 'o1'}]})
    b1 = _brain('0xb1', 'LinkedIn brain', str(nested))
    b2 = _brain('0xb2', 'Vision brain', str(nested))
    orch = _orch_node([b1, b2])
    h = _make(orch, [b1, b2], goal='check the page and tell me who X is')
    h.directive_plan = ['look at the open page', 'report who X is']
    steps = []
    texts = ['LinkedIn is open.\n[ORCH-REMAINING] ["report who X is"]',
             'a page about X']

    def _chain(node, stop_flag=None, *a, **k):
        steps.append((node.get('id'), h.get_variable('_chain_step')))
        h.set_variable('chain_tool_last_result_text', texts.pop(0))
        return '__done__'

    h._execute_chain_import_node = _chain
    # picker: brain 1, then DONE (refused — the report wins), then brain 2
    # (the pool's only remaining worker).
    h.llm_script = ['0', 'DONE', '0']

    h._execute_orchestrator_node(orch, None)

    assert [s[0] for s in steps] == ['0xb1', '0xb2'], steps
    worker_prompts = [p for p in h.llm_prompts if 'AVAILABLE WORKERS' in p]
    assert 'Vision brain' in worker_prompts[1]
    assert 'LinkedIn brain' not in worker_prompts[1], \
        'the brain that reported the span unaccomplished is not re-offered'


def test_synthesis_is_told_what_actually_ran_and_what_remains():
    """Small models narrate the goal list as done work (measured 2026-09-25:
    'has been completed successfully ... logged into my account' over a run
    whose only executed step was 'go to linkedin'), so the synthesis gets the
    trace as the ONLY evidence plus the explicit remaining list."""
    from player.multi_sequence.worflow_interpreter_modules.executor_modules \
        import orchestrator_ops as ops

    class _P(ops.OrchestratorMixin):
        def __init__(self):
            self.calls = []

        def _orchestrator_llm_call(self, prompt, node_data, stop_flag,
                                   max_tokens=256):
            self.calls.append(prompt)
            return 'final answer'

    h = _P()
    trace = [{'n': 1, 'brain': 'linkedin_system_chain',
              'step': '1) Open Chrome browser', 'result': 'Open LinkedIn'}]
    out = h._orchestrator_synthesize(
        'check the page and tell me who X is', trace, {}, None,
        remaining=['report who X is'])
    assert out == 'final answer'
    prompt = h.calls[0]
    assert 'Steps that ACTUALLY ran' in prompt
    assert 'Open LinkedIn' in prompt
    assert 'Still NOT done' in prompt and 'report who X is' in prompt
    assert 'never evidence it was done' in prompt


def test_learned_chain_never_enters_the_step_gate(tmp_path, monkeypatch):
    """The step gate offers STEP-sized chains only.  A learned artifact's
    example IS the goal text, so on the step question it scores like the goal
    itself and collapses the margin (measured 2026-09-24: 0.756 against the
    correct atomic step's 0.809 → margin 0.053 → refused → a fat brain ran and
    did something else)."""
    import AI.laya_client as lc
    step = _write_chain(tmp_path / 'step.json',
                        {'description': 'opens the jobs page'})
    frozen = _write_chain(tmp_path / 'frozen.json', {
        'learned': True,
        'routing': {'examples': ['go to linkedin and click messages']}})

    b = _brain('0xb1', 'Browser brain')  # must NOT run
    c_step = _chain_node('0xc1', 'Jobs chain', step)
    c_learned = _chain_node('0xc2', 'Learned chain', frozen)
    orch = _orch_with_ports(brains=[b], chains=[c_step, c_learned])
    h = _make(orch, [b, c_step, c_learned], goal='open the jobs page')
    called = []
    crit_seen = []

    monkeypatch.setattr(lc, 'available', lambda: True)

    def _noul(state, instructions):
        crit_seen.append(instructions)
        if 'accomplished what it was asked' in instructions:
            return 0.9
        if 'go to linkedin and click messages' in instructions:
            return 0.99   # the learned chain would win if it were offered
        if 'opens the jobs page' in instructions:
            return 0.7
        return 0.05

    monkeypatch.setattr(lc, 'noul', _noul)

    def _chain(node, stop_flag=None, *a, **k):
        called.append(node.get('id'))
        h.set_variable('chain_tool_last_result_text', 'jobs page open')
        return '__done__'

    h._execute_chain_import_node = _chain
    h.llm_script = ['DONE', 'S']

    h._execute_orchestrator_node(orch, None)

    assert called == ['0xc1'], 'the STEP-sized chain serves the step'
    assert not any('go to linkedin and click messages' in c for c in crit_seen), \
        'the learned chain is never offered to the step gate'


def test_split_routes_one_directive_at_a_time(tmp_path, monkeypatch):
    """No chain fits the whole request → the goal is split and each directive
    is routed on its own premise (the routing text must never be the compound
    request — that is what made the gate score 'my network' at 0.414)."""
    import AI.laya_client as lc
    go = _write_chain(tmp_path / 'go.json',
                      {'description': 'navigates to linkedin.com'})
    msg = _write_chain(tmp_path / 'msg.json',
                       {'description': 'clicks on the "messages" button'})

    b = _brain('0xb1', 'Browser brain')
    c1 = _chain_node('0xc1', 'Go chain', go)
    c2 = _chain_node('0xc2', 'Messages chain', msg)
    orch = _orch_with_ports(brains=[b], chains=[c1, c2])
    goal = 'open linkedin.com and click on my messages'
    h = _make(orch, [b, c1, c2], goal=goal)
    _sealed_chain_identity(h, tmp_path)
    h.directive_plan = ['open linkedin.com', 'click on my messages']
    calls = []
    gate_goals = []

    monkeypatch.setattr(lc, 'available', lambda: True)

    def _noul(state, instructions):
        if 'accomplished what it was asked' in instructions:
            return 0.9
        if 'fully achieved' in instructions:
            return 0.9
        if 'whole request end to end' in instructions:
            return 0.1                     # nothing does the whole job
        gate_goals.append(state)
        if 'navigates to linkedin' in instructions:
            return 0.9 if 'Goal: open linkedin.com' in state else 0.1
        return 0.9 if 'Goal: click on my messages' in state else 0.1

    monkeypatch.setattr(lc, 'noul', _noul)

    def _chain(node, stop_flag=None, *a, **k):
        calls.append(node.get('id'))
        h.set_variable('chain_tool_last_result_text', f"{node.get('id')} done")
        return '__done__'

    h._execute_chain_import_node = _chain
    h.llm_script = ['DONE', 'S']

    h._execute_orchestrator_node(orch, None)

    assert calls == ['0xc1', '0xc2'], 'one directive per chain, in order'
    assert h.plan_calls, 'the planner was asked for the split'
    assert any('Goal: open linkedin.com' in s for s in gate_goals), gate_goals
    assert any('Goal: click on my messages' in s for s in gate_goals), gate_goals


def test_plan_completion_ends_the_run_without_a_picker_or_scoper(
        tmp_path, monkeypatch):
    """The 2026-09-24 bug: both directives verified, yet the loop fell back to
    the WHOLE goal, the done-probe read 0.394 on a steps-only premise, and the
    picker+scoper fabricated "select 'Send a Message'" for jobsearch, which ran
    and looped until ESC.  Plan complete IS the completion signal — the run
    ends 'done' before any picker, scoper or done-probe is consulted."""
    import AI.laya_client as lc
    go = _write_chain(tmp_path / 'go.json',
                      {'description': 'navigates to linkedin.com'})
    msg = _write_chain(tmp_path / 'msg.json',
                       {'description': 'clicks on the "messages" button'})

    b = _brain('0xb1', 'LinkedIn brain')  # present, must NOT run
    c1 = _chain_node('0xc1', 'Go chain', go)
    c2 = _chain_node('0xc2', 'Messages chain', msg)
    orch = _orch_with_ports(brains=[b], chains=[c1, c2])
    goal = 'open linkedin.com and click on my messages'
    h = _make(orch, [b, c1, c2], goal=goal)
    _sealed_chain_identity(h, tmp_path)
    h.directive_plan = ['open linkedin.com', 'click on my messages']
    calls = []

    monkeypatch.setattr(lc, 'available', lambda: True)

    def _noul(state, instructions):
        if 'accomplished what it was asked' in instructions:
            return 0.9                     # both steps verify
        if 'fully achieved' in instructions:
            return 0.394                   # the probe that misfired in the log
        if 'whole request end to end' in instructions:
            return 0.1                     # no whole-job fit
        if 'navigates to linkedin' in instructions:
            return 0.9 if 'Goal: open linkedin.com' in state else 0.1
        if 'clicks on the "messages"' in instructions:
            return 0.9 if 'Goal: click on my messages' in state else 0.1
        return 0.1

    monkeypatch.setattr(lc, 'noul', _noul)

    def _chain(node, stop_flag=None, *a, **k):
        calls.append(node.get('id'))
        h.set_variable('chain_tool_last_result_text', f"{node.get('id')} done")
        return '__done__'

    h._execute_chain_import_node = _chain

    def _no_more_routing(*a, **k):
        raise AssertionError('a completed plan must not reach the picker/scoper')

    h._orchestrator_laya_pick = _no_more_routing
    h._orchestrator_llm_pick = _no_more_routing
    h._orchestrator_scope_step = _no_more_routing

    h._execute_orchestrator_node(orch, None)

    assert calls == ['0xc1', '0xc2']
    assert h.executed == [], 'no brain was dispatched for invented work'
    trace = json.loads(h.get_variable('node_orch1_trace'))
    assert [t['ok'] for t in trace] == [True, True]
    assert h.get_variable('node_orch1_output'), 'the run answers normally'


def test_no_marker_from_a_nested_brain_completes_its_span(
        tmp_path, monkeypatch):
    """Handback is a two-way contract: marker ABSENCE means the sub-run ended
    'done', so the whole delegated span is complete by construction — the
    parent must not re-dispatch it, and must not need a probe to know (the
    engine is down here: the verify read is None)."""
    import AI.laya_client as lc
    nested = _write_chain(tmp_path / 'nested.json',
                          {'orchestrator_nodes': [{'node_id': 'o1'}]})
    b = _brain('0xb1', 'LinkedIn brain', str(nested))
    orch = _orch_node([b])
    goal = 'open linkedin.com and click on my messages'
    h = _make(orch, [b], goal=goal)
    _sealed_chain_identity(h, tmp_path)
    h.directive_plan = ['open linkedin.com', 'click on my messages']
    dispatched = []

    monkeypatch.setattr(lc, 'available', lambda: False)
    monkeypatch.setattr(lc, 'ensure_running', lambda: False)

    def _chain(node, stop_flag=None, *a, **k):
        dispatched.append(h.get_variable('_chain_step'))
        h.set_variable('chain_tool_last_result_text',
                       'LinkedIn is open and the messages page is shown.')
        return '__done__'

    h._execute_chain_import_node = _chain
    h.llm_script = ['0']

    h._execute_orchestrator_node(orch, None)

    assert len(dispatched) == 1, dispatched
    assert 'open linkedin.com' in dispatched[0]
    assert 'click on my messages' in dispatched[0], 'the LIST is the step'
    trace = json.loads(h.get_variable('node_orch1_trace'))
    assert len(trace) == 1, 'a finished span is never re-dispatched'


def test_the_model_lists_the_steps_under_the_protocol():
    """The LIST is the model's work: one short question, one step per line,
    a worked example — and every line is validated before it is used."""
    from player.multi_sequence.worflow_interpreter_modules.executor_modules \
        import orchestrator_ops as ops

    class _P(ops.OrchestratorMixin):
        def __init__(self, replies):
            self.calls = []
            self.replies = list(replies)

        def _orchestrator_llm_call(self, prompt, node_data, stop_flag,
                                   max_tokens=256):
            self.calls.append(prompt)
            return self.replies.pop(0) if self.replies else None

    p = _P(['navigate to linkedin\nclick on my messages\n'
            'click on my network\npress the home button'])
    goal = ('navigate to linkedin, click on my messages, then on my network '
            'and then press the home button')
    plan = p._orchestrator_plan_directives(goal, {}, None)
    assert plan == ['navigate to linkedin', 'click on my messages',
                    'click on my network', 'press the home button']
    assert goal in p.calls[0], 'the request rides the prompt'
    assert 'STEPS:' in p.calls[0]
    assert len(p.calls) == 1, 'a good reply is never re-asked'

    # A single action stays one line (the protocol says so and the model
    # obeyed): no re-ask, no invented steps.
    p2 = _P(['click on the messages'])
    assert p2._orchestrator_plan_directives(
        'click the messages button', {}, None) == ['click on the messages']
    assert len(p2.calls) == 1


def test_a_verbless_line_inherits_the_action_before_it():
    """The model may write a step without a verb of its own (measured live
    2026-09-24: 'on my network' for a '...then on my network' clause), so the
    ellipse is resolved by grammar: a line that OPENS with a function word
    continues the previous action — its verb is the previous step's first word.
    A line with its own verb is untouched."""
    from player.multi_sequence.worflow_interpreter_modules.executor_modules \
        import orchestrator_ops as ops

    class _P(ops.OrchestratorMixin):
        def _orchestrator_llm_call(self, prompt, node_data, stop_flag,
                                   max_tokens=256):
            return ('navigate to linkedin\nclick on my messages\n'
                    'on my network\npress the home button')

    plan = _P()._orchestrator_plan_directives(
        'navigate to linkedin, click on my messages, then on my network and '
        'then press the home button', {}, None)
    assert plan == ['navigate to linkedin', 'click on my messages',
                    'click on my network', 'press the home button']

    # Grammar only: a first step has nothing to inherit from, and a line that
    # opens with its own verb is never touched.
    assert ops._orchestrator_inherit_verbs(
        ['on my network', 'press the home button', 'scroll down']) == [
        'on my network', 'press the home button', 'scroll down']
    assert ops._orchestrator_inherit_verbs(
        ['click on my messages', 'on my network', 'the jobs page']) == [
        'click on my messages', 'click on my network', 'click the jobs page']


def test_a_one_line_reply_to_a_joined_request_is_re_asked_once():
    """The protocol's second chance: a request that JOINS clauses cannot be
    answered with one line, so the protocol is asked once more with its
    example.  A reply that is still one line is used as-is (one directive —
    the ladder's picker / done-probe own the run), never cut into pieces by
    patterns: fragments are what disconnect the steps from the chains."""
    from player.multi_sequence.worflow_interpreter_modules.executor_modules \
        import orchestrator_ops as ops

    class _P(ops.OrchestratorMixin):
        def __init__(self, replies):
            self.calls = []
            self.replies = list(replies)

        def _orchestrator_llm_call(self, prompt, node_data, stop_flag,
                                   max_tokens=256):
            self.calls.append(prompt)
            return self.replies.pop(0) if self.replies else None

    goal = 'navigate to linkedin then click on my messages'
    p = _P([goal, 'navigate to linkedin\nclick on my messages'])
    assert p._orchestrator_plan_directives(goal, {}, None) == [
        'navigate to linkedin', 'click on my messages']
    assert len(p.calls) == 2, 'one re-ask, with the protocol example'
    assert 'more than one step' in p.calls[1]

    # Still one line -> the first reply stands as ONE directive, and NOTHING
    # is cut (a cut step is a fragment the gate cannot connect to a chain).
    p2 = _P([goal, goal])
    assert p2._orchestrator_plan_directives(goal, {}, None) == [goal]
    assert len(p2.calls) == 2

    # A single-action request is never re-asked (nothing joins clauses).
    p3 = _P(['open linkedin'])
    assert p3._orchestrator_plan_directives('open linkedin', {}, None) == [
        'open linkedin']
    assert len(p3.calls) == 1


def test_a_handoff_list_is_read_not_re_planned():
    """The numbered list a parent level hands down is already discrete steps —
    its lines ARE the directives and the model is not asked to re-plan them
    (measured 2026-09-24: a re-plan dropped 'jobs' from a clean four-item
    list).  The verbless line is still completed by grammar."""
    from player.multi_sequence.worflow_interpreter_modules.executor_modules \
        import orchestrator_ops as ops

    class _P(ops.OrchestratorMixin):
        def __init__(self):
            self.calls = []

        def _orchestrator_llm_call(self, prompt, node_data, stop_flag,
                                   max_tokens=256):
            self.calls.append(prompt)
            return 'should not be asked'

    p = _P()
    plan = p._orchestrator_plan_directives(
        '1) navigate to linkedin\n2) click on my messages\n3) on my network\n'
        '4) press the home button', {}, None)
    assert plan == ['navigate to linkedin', 'click on my messages',
                    'click on my network', 'press the home button']
    assert p.calls == [], 'a handoff list is read, never re-planned'

    # Prose is NOT a list: it goes to the model.
    assert ops._orchestrator_list_items('open linkedin, click messages') == []
    assert ops._orchestrator_list_items('1) only one line') == []


def test_the_planner_reads_the_current_state():
    """The plan is the one turn that decides what the WHOLE run will do, so
    the observed state rides in: 'check on linkedin, i have the page open'
    planned 'Open LinkedIn' first (measured 2026-09-25 live) because the
    planner was the only turn without the state digest.  No state -> no
    block (a blind run behaves exactly as before)."""
    from player.multi_sequence.worflow_interpreter_modules.executor_modules \
        import orchestrator_ops as ops

    class _P(ops.OrchestratorMixin):
        def __init__(self, replies):
            self.calls = []
            self.replies = list(replies)

        def _orchestrator_llm_call(self, prompt, node_data, stop_flag,
                                   max_tokens=256):
            self.calls.append(prompt)
            return self.replies.pop(0) if self.replies else None

    state = "Desktop now: 'Feed | LinkedIn' (process: chrome.exe)"
    p = _P(['search for aryan raj\nread the profile'])
    plan = p._orchestrator_plan_directives(
        'who is aryan raj ? check on linkedin, i have the page open', {}, None,
        state=state)
    assert plan == ['search for aryan raj', 'read the profile']
    assert state in p.calls[0], 'the planner reads the observed state'
    assert 'already done' in p.calls[0], 'and is told to skip satisfied steps'

    p2 = _P(['open linkedin'])
    assert p2._orchestrator_plan_directives('open linkedin', {}, None) == [
        'open linkedin']
    assert 'CURRENT STATE' not in p2.calls[0]


def test_the_planner_drops_reaching_steps_the_state_already_satisfies():
    """'Open Chrome browser' for a request about the ALREADY OPEN page was
    planned, executed, and its chains gate navigated the user's open tab
    away (measured 2026-09-25: 'get context from the website i opened and
    tell me what you see in it').  The rule is deterministic: a reaching
    verb (open/go/navigate/visit/launch/start) whose thing the state
    already shows is dropped before anything routes."""
    from player.multi_sequence.worflow_interpreter_modules.executor_modules \
        import orchestrator_ops as ops

    class _P(ops.OrchestratorMixin):
        def __init__(self, replies):
            self.calls = []
            self.replies = list(replies)

        def _orchestrator_llm_call(self, prompt, node_data, stop_flag,
                                   max_tokens=256):
            self.calls.append(prompt)
            return self.replies.pop(0) if self.replies else None

    state = ("Desktop now: 'Recibidos (11,330) - x@gmail.com - Gmail - "
             "Google Chrome' (process: chrome.exe)")
    p = _P(['Open Chrome browser\nNavigate to the website you opened\n'
            'Observe what is displayed on the screen'])
    plan = p._orchestrator_plan_directives(
        'get context from the website i opened and tell me what you see in it',
        {}, None, state=state)
    assert 'Open Chrome browser' not in plan
    assert 'Observe what is displayed on the screen' in plan

    # Blind runs (no state) keep the plan untouched.
    p2 = _P(['Open Chrome browser\nclick the save button'])
    assert 'Open Chrome browser' in p2._orchestrator_plan_directives(
        'open chrome and click the save button', {}, None)


def test_the_planner_gets_the_intent_rules_without_a_worker_list():
    """The intent rules stay (an observe-and-report request must plan
    observations, never navigation; list only what the request asks for) and
    NO worker list rides along: a 2B planner copies reference material INTO
    the plan — worker descriptions became plan lines and a learned chain's
    own description became a step the run then executed (measured
    2026-09-25, two runs: 'go to linkedin.com', 'click on my network
    button', 'Learned from a verified run: ...')."""
    from player.multi_sequence.worflow_interpreter_modules.executor_modules \
        import orchestrator_ops as ops

    class _P(ops.OrchestratorMixin):
        def __init__(self, replies):
            self.calls = []
            self.replies = list(replies)

        def _orchestrator_llm_call(self, prompt, node_data, stop_flag,
                                   max_tokens=256):
            self.calls.append(prompt)
            return self.replies.pop(0) if self.replies else None

    p = _P(['look at the open page\nreport who angel daniel m. is'])
    plan = p._orchestrator_plan_directives(
        'check the web page i have open and tell me who angel daniel m. is',
        {}, None)
    assert plan == ['look at the open page', 'report who angel daniel m. is']
    q = p.calls[0]
    assert 'observation steps, never navigation' in q
    assert 'never plan to open, navigate' in q
    assert 'WORKERS' not in q, 'no worker list may ride into the plan prompt'
    assert 'linkedin' not in q.lower(), \
        'the worked examples must stay task-neutral'


def test_the_planner_discards_an_example_echo_on_the_retry():
    """A weak model answers the retry by COPYING the protocol's example
    (measured 2026-09-25 live: 'check this web page i have open and tell me
    what it has' planned 'open linkedin / click on my messages' — the
    example verbatim).  An echo is discarded when the request does not
    itself ask for the example's objects; the first reply stands."""
    from player.multi_sequence.worflow_interpreter_modules.executor_modules \
        import orchestrator_ops as ops

    class _P(ops.OrchestratorMixin):
        def __init__(self, replies):
            self.calls = []
            self.replies = list(replies)

        def _orchestrator_llm_call(self, prompt, node_data, stop_flag,
                                   max_tokens=256):
            self.calls.append(prompt)
            return self.replies.pop(0) if self.replies else None

    echo = '\n'.join(ops.OrchestratorMixin._ORCH_PLAN_EXAMPLE)
    p = _P(['look at the open page', echo])
    plan = p._orchestrator_plan_directives(
        'check this web page i have open and tell me what it has', {}, None)
    assert plan == ['look at the open page'], 'the echoed example is discarded'

    # A request that DOES ask for those objects keeps the retry reply.
    p2 = _P(['open the file', echo])
    assert p2._orchestrator_plan_directives(
        'open the file and click the save button', {}, None) == [
        'open the file', 'click the save button']


def test_a_worker_name_prefix_is_stripped_from_a_directive():
    """The planner mirrors the WORKERS block's 'name:' shape into a plan
    line (measured 2026-09-25: 'web_check: check if linkedin is currently
    open in the browser' rode into the gate — and into the tool's own Input
    node — with the tool's name inside the step)."""
    from player.multi_sequence.worflow_interpreter_modules.executor_modules \
        import orchestrator_ops as ops

    workers = [{'name': 'web_check'}, {'name': 'free_click_web'}]
    out = ops.OrchestratorMixin._orchestrator_strip_worker_prefixes(
        ['web_check: check if linkedin is currently open in the browser',
         'free_click_web — navigate to linkedin.com',
         'open linkedin',
         'check the pod bay doors: report back'],
        workers)
    assert out == [
        'check if linkedin is currently open in the browser',
        'navigate to linkedin.com',
        'open linkedin',
        'check the pod bay doors: report back',
    ]


def test_bracket_scaffolding_never_becomes_a_directive():
    """The 2B model answers the retry with its own reasoning scaffolding
    (measured 2026-09-25: '[Analyze the current state, identify constraints
    (only one step allowed), choose your approach,' / 'explain each step
    clearly.]') — a scaffolding line is not an action, and its ']' also
    corrupted the handback marker downstream.  Dropped, the first reply
    stands."""
    from player.multi_sequence.worflow_interpreter_modules.executor_modules \
        import orchestrator_ops as ops

    class _P(ops.OrchestratorMixin):
        def __init__(self, replies):
            self.calls = []
            self.replies = list(replies)

        def _orchestrator_llm_call(self, prompt, node_data, stop_flag,
                                   max_tokens=256):
            self.calls.append(prompt)
            return self.replies.pop(0) if self.replies else None

    junk = ('[Analyze the current state, identify constraints (only one '
            'step allowed), choose your approach,\nexplain each step clearly.]')
    p = _P(['look at the page', junk])
    plan = p._orchestrator_plan_directives(
        'check this website and tell me what you see', {}, None)
    assert plan == ['look at the page'], 'the scaffolding reply is discarded'

    # A reply that is ONLY scaffolding falls back to the goal as one
    # directive — the ladder owns the run from there.
    p2 = _P([junk])
    assert p2._orchestrator_plan_directives(
        'check this website and tell me what you see', {}, None) == [
        'check this website and tell me what you see']


def test_a_handback_marker_with_brackets_inside_survives():
    """The marker's JSON array may contain ']' inside its strings — the old
    character class stopped at the first one, json.loads failed, and the
    unaccomplished list was silently dropped, closing the parent's plan as
    done over a no-op sub-run (measured 2026-09-25: a directive ending in
    ']' did exactly that)."""
    from player.multi_sequence.worflow_interpreter_modules.executor_modules \
        import orchestrator_ops as ops

    prose, remaining = ops.OrchestratorMixin._orchestrator_split_remaining(
        'The goal could not be advanced.\n'
        '[ORCH-REMAINING] ["[Analyze the current state, choose", '
        '"explain each step clearly.]"]')
    assert remaining == ['[Analyze the current state, choose',
                         'explain each step clearly.]']
    assert prose == 'The goal could not be advanced.'


def test_a_doubled_handback_marker_still_parses():
    """A composed tool result carries the marker TWICE ('Result:' and
    'Output:' sections of the same sub-run).  An end-anchored greedy regex
    merged both arrays into one unparseable blob, the handback was silently
    dropped, and the parent closed a no-op sub-run as 'span complete' —
    while a correct read had already been produced (measured 2026-09-25
    live: web_check described the page, then the run kept going and closed
    falsely)."""
    from player.multi_sequence.worflow_interpreter_modules.executor_modules \
        import orchestrator_ops as ops

    marker = ('[ORCH-REMAINING] ["Check what is visible on the current '
              'screen", "Click on my network button"]')
    text = ('Tool ID: 0x1\nResult: could not advance.\n' + marker
            + '\nLast Node: 0x2 (output)\nOutput: could not advance.\n'
            + marker)
    prose, remaining = ops.OrchestratorMixin._orchestrator_split_remaining(text)
    assert remaining == ['Check what is visible on the current screen',
                         'Click on my network button']
    assert '[ORCH-REMAINING]' not in prose, 'every marker occurrence is stripped'
    assert 'Last Node: 0x2 (output)' in prose


def test_step_gate_description_confirms_the_neural_pick(
        tmp_path, monkeypatch):
    """The human description CONFIRMS the pick: when the top candidate's own
    description carries every object the step names and the runner-up's does
    not, the runner-up's proximity is noise.  Measured live 2026-09-24 on the
    shipped linkedin chains: 'click on my network' read 0.641 against "clicks
    on my network button" while the unrelated home chain read 0.620 — the
    margin rule alone declined a perfect match (0.021 < 0.10)."""
    import AI.laya_client as lc
    net = _write_chain(tmp_path / 'my network.json',
                       {'description': 'clicks on "my network" button'})
    home = _write_chain(tmp_path / 'homelinkedin.json',
                        {'description': 'click the home button on linkedin.'})
    h = _Harness()
    c1 = _chain_node('0xc1', 'My network chain', net)
    c2 = _chain_node('0xc2', 'Home chain', home)
    h.workflow_graph = {'0xc1': c1, '0xc2': c2}
    chains = h._orchestrator_collect_chains(
        {'inputs': [{'from_node': '0xc1', 'input_port': 'chains'},
                    {'from_node': '0xc2', 'input_port': 'chains'}]})
    scores = {'my network': 0.641, 'home': 0.620}

    monkeypatch.setattr(lc, 'available', lambda: True)
    monkeypatch.setattr(
        lc, 'noul',
        lambda state, instructions: next(
            s for key, s in scores.items() if key in instructions))

    got = h._orchestrator_route_chain_direct(
        'click on my network', chains, [], set())
    assert got is not None and got['chain_id'] == '0xc1'

    # Ambiguity is real when BOTH descriptions carry the object: the margin
    # decides, and a 0.021 margin declines.
    both = h._orchestrator_route_chain_direct(
        'click on my network and the home', chains, [], set())
    assert both is None


def test_a_nested_brain_is_handed_only_the_directives_in_its_scope():
    """A sub-brain receives the tasks its DESCRIPTION covers, not the whole
    remaining plan: each following directive is put to the same typed question
    that picked the brain for the current step, and the group grows while the
    answer is that brain.  Unknown / engine-down stops it at the current
    directive; without the Laya picker there is no cheap description read, so
    the tail is handed over unchanged (the sub-brain partitions it itself)."""
    from player.multi_sequence.worflow_interpreter_modules.executor_modules \
        import orchestrator_ops as ops

    class _P(ops.OrchestratorMixin):
        def __init__(self, picks):
            self.picks = list(picks)

        def _orchestrator_laya_worker(self, goal, brains, trace_text, state):
            return self.picks.pop(0) if self.picks else None

    brains = [{'name': 'LinkedIn brain',
               'description': 'interacts with linkedin.com'},
              {'name': 'Vision brain',
               'description': 'looks at the screen'}]
    directives = ['open linkedin', 'click on my messages',
                  'peek at the screen', 'click on the jobs button']

    p = _P([0, 1])          # same brain for #2, another one for #3
    assert p._orchestrator_scope_group(
        0, directives, brains, 'laya', '', '') == [
        'open linkedin', 'click on my messages']

    p2 = _P([None])
    assert p2._orchestrator_scope_group(
        0, directives, brains, 'laya', '', '') == ['open linkedin']

    p3 = _P([])
    assert p3._orchestrator_scope_group(
        0, directives, brains, 'llm', '', '') == directives


def test_step_gate_object_filter_beats_a_coin_flip_score(
        tmp_path, monkeypatch):
    """The step's objects decide who MAY serve it, the scorer decides who does.
    Measured live 2026-09-24 on the 7 wired chains: for 'press my network
    button' the scorer picked the WRONG chain by 0.001 — home 0.656 over
    'clicks on "my network" button' 0.655 — while only the right chain named
    the step's object at all."""
    import AI.laya_client as lc
    net = _write_chain(tmp_path / 'my network.json',
                       {'description': 'clicks on "my network" button'})
    home = _write_chain(tmp_path / 'homelinkedin.json',
                        {'description': 'click the home button on linkedin.'})
    h = _Harness()
    c1 = _chain_node('0xc1', 'My network chain', net)
    c2 = _chain_node('0xc2', 'Home chain', home)
    h.workflow_graph = {'0xc1': c1, '0xc2': c2}
    chains = h._orchestrator_collect_chains(
        {'inputs': [{'from_node': '0xc1', 'input_port': 'chains'},
                    {'from_node': '0xc2', 'input_port': 'chains'}]})
    scores = {'my network': 0.655, 'home': 0.656}

    monkeypatch.setattr(lc, 'available', lambda: True)
    monkeypatch.setattr(
        lc, 'noul',
        lambda state, instructions: next(
            s for key, s in scores.items() if key in instructions))

    got = h._orchestrator_route_chain_direct(
        'press my network button', chains, [], set())
    assert got is not None and got['chain_id'] == '0xc1', got


def test_a_chain_serves_every_step_its_description_names():
    """A chain is the cerebellum, not one action: a routine chain covers
    several consecutive directives, and the plan advances by the span its own
    description names — computed, never assumed.  A step-sized chain advances
    exactly one, and a directive the description does not name stops the
    walk."""
    from player.multi_sequence.worflow_interpreter_modules.executor_modules \
        import orchestrator_ops as ops

    class _P(ops.OrchestratorMixin):
        def __init__(self):
            self.workflow_graph = {}

    p = _P()
    routine = {'name': 'open and click chain',
               'description': 'opens linkedin and clicks on my messages',
               'examples': []}
    step_sized = {'name': 'jobs chain',
                  'description': 'clicks on the "jobs" button',
                  'examples': []}
    directives = ['open linkedin', 'click on my messages', 'press the home '
                  'button']

    assert p._orchestrator_chain_span(
        routine, directives, 0, frozenset()) == 2
    assert p._orchestrator_chain_span(
        routine, directives, 1, frozenset()) == 1
    # A description that names none of the FOLLOWING steps stops at 1.
    assert p._orchestrator_chain_span(
        step_sized, directives, 0, frozenset()) == 1


def test_step_gate_scores_every_wired_chain_and_the_floor_decides(
        tmp_path, monkeypatch):
    """The port is the human's action space: every wired chain is scored for
    every directive — nothing is silently withheld — and alignment comes from
    the floor.  Measured 2026-09-24: for 'click on the home button' the
    messages chain read 0.506 (the tiny model's noise band) and the run
    followed it while the floor was 0.5."""
    import AI.laya_client as lc
    msg = _write_chain(tmp_path / 'messages.json',
                       {'description': 'clicks on the messages button'})
    h = _Harness()
    c = _chain_node('0xc1', 'Messages chain', msg)
    h.workflow_graph = {'0xc1': c}
    chains = h._orchestrator_collect_chains(
        {'inputs': [{'from_node': '0xc1', 'input_port': 'chains'}]})
    seen = []

    monkeypatch.setattr(lc, 'available', lambda: True)
    monkeypatch.setattr(
        lc, 'noul',
        lambda state, instructions: seen.append(instructions) or 0.52)

    assert h._orchestrator_route_chain_direct(
        'click on the home button', chains, [], set()) is None
    assert seen, 'the wired chain WAS scored — no hidden veto'

    # A confident read for its own object still routes.
    monkeypatch.setattr(lc, 'noul', lambda state, instructions: 0.8)
    got = h._orchestrator_route_chain_direct(
        'click on the messages', chains, [], set())
    assert got is not None and got['chain_id'] == '0xc1'


def test_step_gate_floor_rejects_the_noise_band(tmp_path, monkeypatch):
    """A weak score below the floor declines: measured legit picks 0.625-0.81,
    the noise band at or below ~0.51 (a wrong 'messages' read at 0.506 ran on
    2026-09-24 because the floor was 0.5)."""
    import AI.laya_client as lc
    from player.multi_sequence.worflow_interpreter_modules.executor_modules \
        import orchestrator_ops as ops
    msg = _write_chain(tmp_path / 'messages.json',
                       {'description': 'clicks on the messages button'})
    h = _Harness()
    c = _chain_node('0xc1', 'Messages chain', msg)
    h.workflow_graph = {'0xc1': c}
    chains = h._orchestrator_collect_chains(
        {'inputs': [{'from_node': '0xc1', 'input_port': 'chains'}]})

    monkeypatch.setattr(lc, 'available', lambda: True)
    monkeypatch.setattr(lc, 'noul',
                        lambda state, instructions:
                        ops._CHAIN_GATE_MIN - 0.06)
    assert h._orchestrator_route_chain_direct(
        'click on the messages', chains, [], set()) is None

    monkeypatch.setattr(lc, 'noul',
                        lambda state, instructions:
                        ops._CHAIN_GATE_MIN + 0.06)
    assert h._orchestrator_route_chain_direct(
        'click on the messages', chains, [], set()) is not None


def test_step_gate_routes_a_reaching_step_whose_only_objects_are_glue(
        tmp_path, monkeypatch):
    """'Open LinkedIn.com' must reach the chain that navigates there.

    The step's verb ('open') and the URL fragment ('com') are glue, not
    objects, and the site word ('linkedin') is library-generic because every
    chain names it — so the step names NO object and the scorer's top pick
    decides on the floor alone.  Measured live 2026-10-02 on the shipped
    linkedin chains: navigate 0.751 vs jobs 0.712 (margin 0.039), which the
    object-margin rule alone refused, killing the run 'no_worker' despite the
    'navigate to linkedin' chain being wired."""
    import AI.laya_client as lc
    nav = _write_chain(tmp_path / 'Linkedin_nav.json',
                       {'description': 'navigate to linkedin on the browser'})
    jobs = _write_chain(tmp_path / 'jobs.json',
                        {'description': 'click on the jobs button on linkedin'})
    search = _write_chain(
        tmp_path / 'linkedin_search.json',
        {'description': 'click the search bar in linkedin, ask the user for '
                        'input, press enter'})
    enter = _write_chain(tmp_path / 'press_enter.json',
                         {'description': 'press enter key'})
    h = _Harness()
    nodes = [
        _chain_node('0xn', 'Linkedin_nav', nav),
        _chain_node('0xj', 'jobs', jobs),
        _chain_node('0xs', 'linkedin_search', search),
        _chain_node('0xe', 'press_enter', enter),
    ]
    h.workflow_graph = {n['id']: n for n in nodes}
    chains = h._orchestrator_collect_chains(
        {'inputs': [{'from_node': n['id'], 'input_port': 'chains'}
                    for n in nodes]})
    scores = {
        'navigate to linkedin': 0.751,
        'click on the jobs button': 0.712,
        'click the search bar': 0.257,
        'press enter key': 0.003,
    }

    monkeypatch.setattr(lc, 'available', lambda: True)
    monkeypatch.setattr(
        lc, 'noul',
        lambda state, instructions: next(
            s for key, s in scores.items() if key in instructions))

    got = h._orchestrator_route_chain_direct(
        'Open LinkedIn.com', chains, [], set())
    assert got is not None and got['chain_id'] == '0xn', got


def test_gate_routes_a_correct_chain_despite_a_parenthetical_gloss(
        tmp_path, monkeypatch):
    """A parenthetical aside must not sink a correct match under the floor.

    Measured 2026-10-03 on the shipped library: 'click on the home button for
    linkedin (goes to base page)' scored 0.252 against 'go to the home page',
    but 0.5611 without the aside — the gloss alone cost 0.31 and the gate
    declined the right chain, so a chains-only level did nothing.  The signal
    is now scored with the aside stripped, and that clears the floor.
    """
    import AI.laya_client as lc
    home = _write_chain(
        tmp_path / 'linkedin home.json',
        {'description': 'click on the home button for linkedin '
                        '(goes to base page)'})
    h = _Harness()
    c = _chain_node('0xh', 'linkedin home', home)
    h.workflow_graph = {'0xh': c}
    chains = h._orchestrator_collect_chains(
        {'inputs': [{'from_node': '0xh', 'input_port': 'chains'}]})

    # The aside is gone from the text the gate scores AND routes by.
    assert '(goes to base page)' not in chains[0]['signal']
    assert 'click on the home button for linkedin' in chains[0]['signal']

    seen = {}

    def _noul(state, instructions):
        seen['hyp'] = instructions
        return 0.5611          # the measured score for this pair, aside gone

    monkeypatch.setattr(lc, 'available', lambda: True)
    monkeypatch.setattr(lc, 'noul', _noul)

    got = h._orchestrator_route_chain_direct(
        'go to the home page', chains, [], set())
    assert got is not None and got['chain_id'] == '0xh', got
    assert '(goes to base page)' not in seen['hyp']


def test_gate_waits_for_a_cold_laya_instead_of_silently_declining(
        tmp_path, monkeypatch):
    """A cold Laya must be WAITED for, not silently declined — no timeout.

    Measured 2026-10-03 13:09: the gate launched the daemon, the model was
    still loading, and the gate returned None with NO log line — so a
    chains-only level with the right chain wired died 'no_worker' at step 0.
    The gate NEEDS the engine to route, so while it is genuinely 'loading' it
    keeps waiting, then routes.  Only an engine that is NOT coming up declines.
    """
    import AI.laya_client as lc
    nav = _write_chain(tmp_path / 'Linkedin_nav.json',
                       {'description': 'navigate to linkedin on the browser'})
    h = _Harness()
    node = _chain_node('0xn', 'Linkedin_nav', nav)
    h.workflow_graph = {node['id']: node}
    chains = h._orchestrator_collect_chains(
        {'inputs': [{'from_node': '0xn', 'input_port': 'chains'}]})

    state = {'polls': 0}

    def _status():
        state['polls'] += 1
        return 'ready' if state['polls'] >= 2 else 'loading'

    monkeypatch.setattr(lc, 'status', _status)
    monkeypatch.setattr(lc, 'available', lambda: state['polls'] >= 2)
    monkeypatch.setattr(lc, 'ensure_running', lambda: False)  # grace expired
    monkeypatch.setattr(lc, 'noul', lambda state, instructions: 0.9)

    got = h._orchestrator_route_chain_direct(
        'Open LinkedIn.com', chains, [], set())
    assert got is not None and got['chain_id'] == '0xn', got

    # A genuinely absent engine (never 'loading') still declines — the wait
    # only tolerates a daemon that is really coming up.
    monkeypatch.setattr(lc, 'status', lambda: 'down')
    monkeypatch.setattr(lc, 'available', lambda: False)
    assert h._orchestrator_route_chain_direct(
        'Open LinkedIn.com', chains, [], set()) is None


def test_chain_signal_keeps_the_description_and_adds_learned_examples():
    """A library chain's routing text is description + accumulated examples: an
    example can only ADD signal, never erase the description."""
    from player.multi_sequence.worflow_interpreter_modules.executor_modules \
        import orchestrator_ops as ops

    sig = ops._orchestrator_chain_signal(
        'navigate to linkedin on the browser',
        ['Open LinkedIn.com', 'go to linkedin'], learned=False)
    assert 'navigate to linkedin on the browser' in sig
    assert 'Open LinkedIn.com' in sig and 'go to linkedin' in sig
    # No examples -> the description stands alone.
    assert ops._orchestrator_chain_signal('click the jobs button', [], False) \
        == 'click the jobs button'
    # A learned artifact keeps examples-only (its description is narration).
    assert ops._orchestrator_chain_signal(
        'Learned from a verified run: 1) jobs; 2) search',
        ['Find a monitor'], learned=True) == 'Find a monitor'


def test_route_examples_append_is_bounded_deduped_and_valid(tmp_path):
    """Verified steps accumulate on the chain file: newest kept, dupes dropped,
    oldest evicted, and the file stays valid JSON."""
    from player.multi_sequence.worflow_interpreter_modules.executor_modules \
        import orchestrator_ops as ops

    f = _write_chain(tmp_path / 'jobs.json',
                     {'description': 'click on the jobs button on linkedin'})
    assert ops._orchestrator_append_route_examples(f, ['Click the jobs button'])
    # Whitespace/case-only duplicate -> no change, no write.
    assert not ops._orchestrator_append_route_examples(
        f, ['  click  the jobs button '])
    for i in range(6):
        ops._orchestrator_append_route_examples(f, ['step %d' % i])

    cfg = json.loads(open(f, encoding='utf-8').read())
    ex = cfg['routing']['examples']
    assert len(ex) == ops._CHAIN_ROUTING_EXAMPLES      # bounded
    assert ex[-1] == 'step 5'                          # newest kept
    assert 'Click the jobs button' not in ex           # oldest evicted
    assert len(ex) == len({e.lower() for e in ex})     # all unique


def test_verified_steps_become_route_examples_only_on_done(tmp_path, monkeypatch):
    """Reinforcement is PER STEP and only from a run that closed 'done': the
    chain that served a verified directive gets that directive as a routing
    example, and a step the verify REJECTED (or a non-'done' run) is never
    credited — the run's single final rating is not the signal."""
    jobs = _write_chain(tmp_path / 'jobs.json',
                        {'description': 'click on the jobs button on linkedin'})
    nav = _write_chain(tmp_path / 'Linkedin_nav.json',
                       {'description': 'navigate to linkedin on the browser'})
    h = _Harness()
    nodes = [_chain_node('0xj', 'jobs', jobs),
             _chain_node('0xn', 'Linkedin_nav', nav)]
    h.workflow_graph = {n['id']: n for n in nodes}
    chains = h._orchestrator_collect_chains(
        {'inputs': [{'from_node': n['id'], 'input_port': 'chains'}
                    for n in nodes]})
    monkeypatch.setenv('LOOPER_ORCH_ROUTES', 'on')

    trace = [
        {'n': 1, 'brain': 'jobs', 'brain_id': '0xj',
         'step': 'Click the jobs button', 'ok': True},
        {'n': 2, 'brain': 'Linkedin_nav', 'brain_id': '0xn',
         'step': 'Open LinkedIn.com', 'ok': False},   # verify REJECTED it
    ]

    # A run that did NOT close 'done' teaches nothing.
    h._orchestrator_record_route_examples(chains, trace, 'no_worker')
    assert 'routing' not in json.loads(open(jobs, encoding='utf-8').read())

    # 'done' -> only the VERIFIED step lands, on its own chain.
    h._orchestrator_record_route_examples(chains, trace, 'done')
    assert json.loads(open(jobs, encoding='utf-8').read())[
        'routing']['examples'] == ['Click the jobs button']
    assert 'routing' not in json.loads(open(nav, encoding='utf-8').read())

    # Kill switch: nothing is written with LOOPER_ORCH_ROUTES=off.
    monkeypatch.setenv('LOOPER_ORCH_ROUTES', 'off')
    h._orchestrator_record_route_examples(
        chains,
        [{'brain_id': '0xn', 'step': 'go to linkedin', 'ok': True}], 'done')
    assert 'routing' not in json.loads(open(nav, encoding='utf-8').read())


def test_step_objects_strip_verbs_and_url_fragments():
    """What a step NAMES once glue is gone: a verb or a URL fragment is not an
    object, so a bare navigation names nothing and the scorer decides."""
    from player.multi_sequence.worflow_interpreter_modules.executor_modules \
        import orchestrator_ops as ops

    lib = frozenset({'linkedin'})          # every chain names the site
    assert ops._orchestrator_objects('go to linkedin.com', lib) == set()
    assert ops._orchestrator_objects('Open LinkedIn.com', lib) == set()
    # The object nouns survive; only the verb and glue fall away.
    assert ops._orchestrator_objects('Click the jobs button', frozenset()) \
        == {'job', 'button'}
    assert ops._orchestrator_objects('press enter key', frozenset()) \
        == {'enter', 'key'}


def test_parse_directives_shape():
    """The parser shapes the MODEL's list — one line, one directive — and it
    never cuts a line into pieces (a cut step is a fragment the gate cannot
    connect to the chain that serves it)."""
    from player.multi_sequence.worflow_interpreter_modules.executor_modules \
        import orchestrator_ops as ops
    parse = ops.OrchestratorMixin._orchestrator_parse_directives
    # A realistic library corpus: the verbs and UI glue its chains share are
    # generic FOR IT (the library classifies its own vocabulary — see
    # ops._orchestrator_library_generic), so 'click'/'open'/'page' never
    # decide an object match.
    lib = frozenset({'click', 'open', 'navigate', 'go', 'search', 'page',
                     'browser'})

    # Bullets, numbering and duplicates are stripped; order is kept.
    assert parse("- 1. open linkedin\n2) click messages\nopen linkedin",
                 'goal', lib) == ['open linkedin', 'click messages']
    # Reasoning blocks are dropped.
    assert parse('<think>internal monologue</think>open linkedin', 'goal',
                 lib) == ['open linkedin']
    # Nothing usable -> ONE directive: the goal, untouched (the ladder's
    # picker / scoper / done-probe own it from there — nothing decomposes it
    # by patterns).
    assert parse('', 'the whole goal', lib) == ['the whole goal']
    assert parse('x' * 400, 'the whole goal', lib) == ['the whole goal']
    assert parse('', 'navigate to linkedin, click on my network and then click '
                 'on the home button', lib) == [
        'navigate to linkedin, click on my network and then click on the '
        'home button']
    # Same action re-worded collapses to one (object dedupe across the plan).
    assert parse('navigate to my messages\nclick on my messages\n'
                 'click on the jobs button', 'goal', lib) == [
        'navigate to my messages', 'click on the jobs button']
    # The cap holds.
    assert len(parse('\n'.join(f'action {i}' for i in range(20)), 'g', lib)) \
        == ops._ORCH_MAX_DIRECTIVES
    # Planner narration never becomes a step — and it does not break the
    # object-dedupe of the real steps around it: the echoed 3rd line collapses
    # onto the 1st.
    assert parse(
        'navigate to linkedin.com, click on my network, then click on my messages\n'
        'However, following the specific format requested:\n'
        'open linkedin.com, click on my network, then click on my messages',
        'g', lib) == [
        'navigate to linkedin.com, click on my network, then click on my messages']
    # A long line is a paragraph, not an action.
    assert parse('open linkedin ' + 'x' * 400, 'goal', lib) == ['goal']


def test_direct_chains_serve_steps_in_sequence(tmp_path, monkeypatch):
    """Chains are STEP-sized: the gate routes ONE per iteration (argmax over
    the UNUSED candidates, margin-gated) and the loop replans after each —
    the learned library's whole point.  A used chain is never offered again."""
    import AI.laya_client as lc
    go = _write_chain(tmp_path / 'go.json',
                      {'description': 'navigates to linkedin.com'})
    jobs = _write_chain(tmp_path / 'jobs.json',
                        {'description': 'clicks on the "jobs" button'})

    b = _brain('0xb1', 'Browser brain')  # present, must NOT run
    c1 = _chain_node('0xc1', 'Go chain', go)
    c2 = _chain_node('0xc2', 'Jobs chain', jobs)
    orch = _orch_with_ports(brains=[b], chains=[c1, c2])
    h = _make(orch, [b, c1, c2], goal='open linkedin then click the jobs button')
    _sealed_chain_identity(h, tmp_path)
    calls = []

    monkeypatch.setattr(lc, 'available', lambda: True)

    def _noul(state, instructions):
        if 'accomplished what it was asked' in instructions:
            return 0.9                     # per-step verification
        if 'fully achieved' in instructions:
            return 0.9                     # chains-port done-probe
        if 'whole request end to end' in instructions:
            return 0.1                     # step-sized chains: no whole-job fit
        fresh = '(none yet)' in state      # no steps on the trace yet
        if 'navigates to linkedin' in instructions:
            return 0.9 if fresh else 0.1   # step 1: go to linkedin
        if 'clicks on the "jobs"' in instructions:
            return 0.2 if fresh else 0.9   # step 2: click jobs
        return 0.1

    monkeypatch.setattr(lc, 'noul', _noul)

    def _chain(node, stop_flag=None, *a, **k):
        calls.append(node.get('id'))
        h.set_variable('chain_tool_last_result_text', f"{node.get('id')} done")
        return '__done__'

    h._execute_chain_import_node = _chain
    h.llm_script = ['DONE', 'final answer']

    h._execute_orchestrator_node(orch, None)

    assert calls == ['0xc1', '0xc2'], 'both steps served by deterministic chains'
    trace = json.loads(h.get_variable('node_orch1_trace'))
    assert [t['n'] for t in trace] == [1, 2]
    assert all(t['ok'] is True for t in trace)
    assert h.executed == [], 'no brain was dispatched'


def test_direct_chain_gate_declines_an_ambiguous_step(tmp_path, monkeypatch):
    """Measured ambiguity (compound-goal states the model cannot order land at
    margins 0.03-0.06): the gate must NOT guess — the brain loop takes it."""
    import AI.laya_client as lc
    a = _write_chain(tmp_path / 'a.json', {'description': 'clicks on the "jobs" button'})
    b = _write_chain(tmp_path / 'b.json', {'description': 'clicks on the "messages" button'})

    br = _brain('0xb1', 'Browser brain')
    c1 = _chain_node('0xc1', 'Jobs chain', a)
    c2 = _chain_node('0xc2', 'Messages chain', b)
    orch = _orch_with_ports(brains=[br], chains=[c1, c2])
    h = _make(orch, [br, c1, c2])
    calls = []

    monkeypatch.setattr(lc, 'available', lambda: True)

    def _noul(state, instructions):
        if 'accomplished what it was asked' in instructions:
            return 0.9
        return 0.77 if 'jobs' in instructions else 0.74  # margin 0.03

    monkeypatch.setattr(lc, 'noul', _noul)
    _orig = h._execute_chain_import_node

    def _wrapped(node, stop_flag=None):
        calls.append(node.get('id'))
        return _orig(node, stop_flag)

    h._execute_chain_import_node = _wrapped
    h.brain_results = {'0xb1': 'done by brain'}
    h.llm_script = ['0', 'do the thing', 'DONE', 'S']

    h._execute_orchestrator_node(orch, None)

    assert '0xc1' not in calls and '0xc2' not in calls
    assert h.executed == ['0xb1']


def test_the_direct_chain_gate_ignores_ambient_state_words(
        tmp_path, monkeypatch):
    """A word the observed state already shows describes the environment,
    not the task: it must not confirm a chain, and a step whose non-ambient
    objects NO chain's description names is declined (measured 2026-09-25:
    'Open Chrome browser' matched 'go to linkedin' on the word 'chrome'
    from the window title and navigated the user's open Gmail tab to
    linkedin.com)."""
    import AI.laya_client as lc
    go = _write_chain(tmp_path / 'go.json',
                      {'description': 'navigates to linkedin.com on chrome'})
    c1 = _chain_node('0xc1', 'Go chain', go)
    h = _Harness()
    h.workflow_graph = {'0xc1': c1}
    chains = h._orchestrator_collect_chains(
        {'inputs': [{'from_node': '0xc1', 'input_port': 'chains'}]})

    monkeypatch.setattr(lc, 'available', lambda: True)
    monkeypatch.setattr(lc, 'noul', lambda state, instructions: 0.69)

    state = ("Desktop now: 'Recibidos (11,330) - x@gmail.com - Gmail - "
             "Google Chrome' (process: chrome.exe)")
    assert h._orchestrator_route_chain_direct(
        'Open Chrome browser', chains, None, set(), state=state) is None, \
        'the ambient word chrome cannot confirm a chain'

    # Control: a step whose object the chain DOES describe still routes.
    got = h._orchestrator_route_chain_direct(
        'go to linkedin.com', chains, None, set(), state=state)
    assert got is not None and got['chain_id'] == '0xc1'


def test_direct_chain_gate_rejection_falls_through_to_brains(tmp_path, monkeypatch):
    import AI.laya_client as lc
    lib = _write_chain(tmp_path / 'lib_chain.json', {'description': 'opens jobs'})

    b = _brain('0xb1', 'Browser brain')
    c = _chain_node('0xc1', 'Jobs chain', lib)
    orch = _orch_with_ports(brains=[b], chains=[c])
    h = _make(orch, [b, c])
    calls = []

    monkeypatch.setattr(lc, 'available', lambda: True)
    monkeypatch.setattr(
        lc, 'choice', lambda state, instructions, criteria: next(iter(criteria)))
    # Below _CHAIN_GATE_MIN: the entailment confirm rejects the route.
    monkeypatch.setattr(lc, 'noul', lambda state, instructions: 0.1)
    _orig = h._execute_chain_import_node

    def _wrapped(node, stop_flag=None):
        calls.append(node.get('id'))
        return _orig(node, stop_flag)

    h._execute_chain_import_node = _wrapped
    h.brain_results = {'0xb1': 'done by brain'}
    h.llm_script = ['0', 'do the thing', 'DONE', 'S']

    h._execute_orchestrator_node(orch, None)

    assert '0xc1' not in calls, 'a rejected route must not run the deterministic chain'
    assert h.executed == ['0xb1']
    assert h.get_variable('node_orch1_output') == 'S'


def test_direct_chain_stays_closed_without_the_engine(tmp_path, monkeypatch):
    import AI.laya_client as lc
    lib = _write_chain(tmp_path / 'lib_chain.json', {'description': 'opens jobs'})

    b = _brain('0xb1', 'Browser brain')
    c = _chain_node('0xc1', 'Jobs chain', lib)
    orch = _orch_with_ports(brains=[b], chains=[c])
    h = _make(orch, [b, c])
    calls = []
    probes = []

    # Engine unavailable AND not startable (kill switch / no exe / no model).
    monkeypatch.setattr(lc, 'available', lambda: False)
    monkeypatch.setattr(lc, 'ensure_running', lambda: False)
    monkeypatch.setattr(lc, 'choice', lambda *a, **k: probes.append(1))
    monkeypatch.setattr(lc, 'noul', lambda *a, **k: probes.append(1))
    _orig = h._execute_chain_import_node

    def _wrapped(node, stop_flag=None):
        calls.append(node.get('id'))
        return _orig(node, stop_flag)

    h._execute_chain_import_node = _wrapped
    h.brain_results = {'0xb1': 'done by brain'}
    h.llm_script = ['0', 'do the thing', 'DONE', 'S']

    h._execute_orchestrator_node(orch, None)

    assert probes == [], 'no Laya probe may be made while the engine is off'
    assert '0xc1' not in calls, 'the fast path stays closed without the engine'
    assert h.executed == ['0xb1']


def test_direct_chain_gate_starts_the_unloaded_engine(tmp_path, monkeypatch):
    """Laya is a burst resource (load -> use -> unload): at step 1 the daemon
    is normally DOWN.  The chains gate may start it (~2 s) — the fast path
    must still route, or the whole chains port dies with the unload policy."""
    import AI.laya_client as lc
    lib = _write_chain(
        tmp_path / 'lib_chain.json',
        {'description': 'opens the jobs page',
         'routing': {'examples': ['open the jobs page on linkedin']}})

    b = _brain('0xb1', 'Browser brain')  # present, must NOT run
    c = _chain_node('0xc1', 'Jobs chain', lib)
    orch = _orch_with_ports(brains=[b], chains=[c])
    h = _make(orch, [b, c], goal='open the jobs page on linkedin')
    calls = []
    started = []

    monkeypatch.setattr(lc, 'available', lambda: False)  # unloaded after its last burst
    monkeypatch.setattr(lc, 'ensure_running', lambda: started.append(1) or True)
    monkeypatch.setattr(
        lc, 'choice', lambda state, instructions, criteria: next(iter(criteria)))
    monkeypatch.setattr(lc, 'noul', lambda state, instructions: 0.9)

    def _chain(node, stop_flag=None, *a, **k):
        calls.append(node.get('id'))
        h.set_variable('chain_tool_last_result_text', 'jobs page open')
        return '__done__'

    h._execute_chain_import_node = _chain
    h.llm_script = ['final answer']

    h._execute_orchestrator_node(orch, None)

    assert started, 'the gate must relaunch the unloaded daemon'
    assert calls == ['0xc1'], 'the frozen chain runs; no brain is dispatched'
    assert h.executed == []


def test_verify_step_may_relaunch_the_unloaded_engine(monkeypatch):
    """allow_start (picker=laya runs / the direct-chain path) relaunches the
    unloaded daemon; the picker=llm rule keeps the old no-launch behaviour."""
    import AI.laya_client as lc
    h = _Harness()
    started = []

    monkeypatch.setattr(lc, 'available', lambda: False)
    monkeypatch.setattr(lc, 'ensure_running', lambda: started.append(1) or True)
    monkeypatch.setattr(lc, 'noul', lambda state, instructions: 0.9)

    assert h._orchestrator_verify_step('g', 's', 'r', allow_start=True) is True
    assert started == [1]
    assert h._orchestrator_verify_step('g', 's', 'r') is None
    assert started == [1]


def test_freeze_writes_and_wires_a_learned_chain(tmp_path, monkeypatch):
    import AI.laya_client as lc
    monkeypatch.delenv('LOOPER_LEARN', raising=False)

    # Descriptions share the goal's object ('monitor') — the freeze requires
    # every executed step to BELONG to the request (coverage gate).
    lib1 = _write_chain(tmp_path / 'lib1.json',
                        {'sequences': [], 'description': 'opens the monitor search'})
    lib2 = _write_chain(tmp_path / 'lib2.json',
                        {'sequences': [], 'description': 'clicks the cheapest monitor'})
    orch_file = tmp_path / 'ORCHESTRATOR.json'
    orch_file.write_text(json.dumps({
        'orchestrator_nodes': [{'node_id': 'orch1', 'connections': []}],
        'chain_import_nodes': [],
    }), encoding='utf-8')

    b1 = _brain('0xb1', 'Brain one', lib1)
    b2 = _brain('0xb2', 'Brain two', lib2)
    orch = _orch_node([b1, b2])
    h = _make(orch, [b1, b2])
    h.chain_identity = str(orch_file)
    monkeypatch.setattr(lc, 'available', lambda: True)
    monkeypatch.setattr(lc, 'noul', lambda state, instructions: 0.9)
    h.brain_results = {'0xb1': 'opened', '0xb2': 'clicked'}
    h.llm_script = ['0', 'open it', '1', 'click it', 'DONE', 'S']

    h._execute_orchestrator_node(orch, None)

    learned = list((tmp_path / 'learned').glob('*.json'))
    assert len(learned) == 1, learned
    payload = json.loads(learned[0].read_text(encoding='utf-8'))
    assert payload['learned'] is True
    # Composition only: one import per executed step, in run order.
    assert [n['chain_file_path'] for n in payload['chain_import_nodes']] \
        == [lib1, lib2]
    assert payload['routing']['examples'] == ['Find the cheapest monitor']
    assert payload['input_nodes'][0]['agent_modifiable'] is True

    # Wired into the running chain file, on the orchestrator's 'chains' port.
    wired = json.loads(orch_file.read_text(encoding='utf-8'))
    assert len(wired['chain_import_nodes']) == 1
    conn = wired['chain_import_nodes'][0]['connections'][0]
    assert (conn['input_port'], conn['target_node_id']) == ('chains', 'orch1')
    assert (tmp_path / 'ORCHESTRATOR.json.bak').exists()


def test_freeze_refuses_steps_that_do_not_belong_to_the_request(
        tmp_path, monkeypatch):
    """Coverage gate: every executed step must share an object with the
    request, or the artifact is never written — a detached plan used to be
    frozen as its goal's artifact and then hijack that goal's future matches
    (measured 2026-09-25: 'go to linkedin / my network / homelinkedin' as
    the artifact for 'get context off the website ...')."""
    import AI.laya_client as lc
    monkeypatch.delenv('LOOPER_LEARN', raising=False)

    lib1 = _write_chain(tmp_path / 'lib1.json', {'sequences': []})  # 'Brain one'
    lib2 = _write_chain(tmp_path / 'lib2.json', {'sequences': []})  # 'Brain two'
    orch_file = tmp_path / 'ORCHESTRATOR.json'
    orch_file.write_text(json.dumps({
        'orchestrator_nodes': [{'node_id': 'orch1', 'connections': []}],
        'chain_import_nodes': [],
    }), encoding='utf-8')

    b1 = _brain('0xb1', 'Brain one', lib1)
    b2 = _brain('0xb2', 'Brain two', lib2)
    orch = _orch_node([b1, b2])
    h = _make(orch, [b1, b2])       # goal: 'Find the cheapest monitor'
    h.chain_identity = str(orch_file)
    monkeypatch.setattr(lc, 'available', lambda: True)
    monkeypatch.setattr(lc, 'noul', lambda state, instructions: 0.9)
    h.brain_results = {'0xb1': 'opened', '0xb2': 'clicked'}
    h.llm_script = ['0', 'open it', '1', 'click it', 'DONE', 'S']

    h._execute_orchestrator_node(orch, None)

    assert not list((tmp_path / 'learned').glob('*.json')), \
        'steps sharing nothing with the request must not freeze'
    assert json.loads(orch_file.read_text(encoding='utf-8'))[
        'chain_import_nodes'] == []


def test_freeze_compiles_a_direct_chain_run(tmp_path, monkeypatch):
    """The step-wise fast path feeds the learning loop: a run served entirely
    by chains from the 'chains' port, all verified True and ended done, must
    compile into a learned chain (the step lookup covers both brain lists and
    direct-chain lists)."""
    import AI.laya_client as lc
    monkeypatch.delenv('LOOPER_LEARN', raising=False)

    lib1 = _write_chain(tmp_path / 'lib1.json',
                        {'description': 'navigates to linkedin'})
    lib2 = _write_chain(tmp_path / 'lib2.json',
                        {'description': 'clicks the jobs button'})
    orch_file = tmp_path / 'ORCHESTRATOR.json'
    orch_file.write_text(json.dumps({
        'orchestrator_nodes': [{'node_id': 'orch1', 'connections': []}],
        'chain_import_nodes': [],
    }), encoding='utf-8')

    c1 = _chain_node('0xc1', 'Go chain', lib1)
    c2 = _chain_node('0xc2', 'Jobs chain', lib2)
    orch = _orch_with_ports(chains=[c1, c2])
    h = _make(orch, [c1, c2], goal='open linkedin then click the jobs button')
    h.chain_identity = str(orch_file)

    monkeypatch.setattr(lc, 'available', lambda: True)

    def _noul(state, instructions):
        if 'accomplished what it was asked' in instructions:
            return 0.9                     # both steps verify True
        if 'fully achieved' in instructions:
            return 0.9                     # chains-port done-probe
        if 'whole request end to end' in instructions:
            return 0.1                     # no single chain does the whole job
        fresh = '(none yet)' in state
        if 'navigates to linkedin' in instructions:
            return 0.9 if fresh else 0.1
        if 'clicks the jobs button' in instructions:
            return 0.2 if fresh else 0.9
        return 0.1

    monkeypatch.setattr(lc, 'noul', _noul)

    def _chain(node, stop_flag=None, *a, **k):
        h.set_variable('chain_tool_last_result_text', f"{node.get('id')} ok")
        return '__done__'

    h._execute_chain_import_node = _chain
    h.llm_script = ['DONE', 'S']

    h._execute_orchestrator_node(orch, None)

    learned = list((tmp_path / 'learned').glob('*.json'))
    assert len(learned) == 1, learned
    payload = json.loads(learned[0].read_text(encoding='utf-8'))
    assert [n['chain_file_path'] for n in payload['chain_import_nodes']] \
        == [lib1, lib2]
    assert payload['routing']['examples'] == [
        'open linkedin then click the jobs button']


def test_freeze_refuses_a_run_that_copied_a_worker_description(
        tmp_path, monkeypatch):
    """A step whose text copies a wired chain's own description means the
    run executed reference material, not the user's request — it must never
    be frozen as that goal's artifact (measured 2026-09-25: a learned
    chain's 'Learned from a verified run: ...' description became a plan
    line, the detached run was frozen under the user's goal, and the
    artifact then matched that same goal as the whole job)."""
    import AI.laya_client as lc
    monkeypatch.delenv('LOOPER_LEARN', raising=False)

    lib1 = _write_chain(tmp_path / 'lib1.json',
                        {'description': 'open the file and click the save button'})
    lib2 = _write_chain(tmp_path / 'lib2.json', {'sequences': []})
    orch_file = tmp_path / 'ORCHESTRATOR.json'
    orch_file.write_text(json.dumps({
        'orchestrator_nodes': [{'node_id': 'orch1', 'connections': []}],
        'chain_import_nodes': [],
    }), encoding='utf-8')

    b1 = _brain('0xb1', 'Brain one', lib1)
    b2 = _brain('0xb2', 'Brain two', lib2)
    orch = _orch_node([b1, b2])
    h = _make(orch, [b1, b2])
    h.chain_identity = str(orch_file)
    monkeypatch.setattr(lc, 'available', lambda: True)
    monkeypatch.setattr(lc, 'noul', lambda state, instructions: 0.9)
    h.brain_results = {'0xb1': 'opened', '0xb2': 'clicked'}
    # The scoped step for brain 1 IS the chain's own description (the copy).
    h.llm_script = ['0', 'open the file and click the save button',
                    '1', 'click it', 'DONE', 'S']

    h._execute_orchestrator_node(orch, None)

    assert not list((tmp_path / 'learned').glob('*.json')), \
        'nothing may be frozen'
    assert json.loads(orch_file.read_text(encoding='utf-8'))[
        'chain_import_nodes'] == []


def test_freeze_refuses_a_chain_that_does_not_host_the_orchestrator(tmp_path, monkeypatch):
    """The artifact belongs next to the chain that HOSTS this orchestrator: a
    runtime id that is not in the source file (harness, recreated node) must
    skip the freeze — not write a learned chain into a file that never ran it.
    Measured twice (2026-09-23/24): tests leaked learned files and imports into
    the shipped ORCHESTRATOR.json this way."""
    import AI.laya_client as lc
    monkeypatch.delenv('LOOPER_LEARN', raising=False)
    lib1 = _write_chain(tmp_path / 'lib1.json', {'sequences': []})
    lib2 = _write_chain(tmp_path / 'lib2.json', {'sequences': []})
    orch_file = tmp_path / 'ORCHESTRATOR.json'
    orch_file.write_text(json.dumps({
        'orchestrator_nodes': [{'node_id': '0xsomebody-else', 'connections': []}],
        'chain_import_nodes': [],
    }), encoding='utf-8')

    b1 = _brain('0xb1', 'Brain one', lib1)
    b2 = _brain('0xb2', 'Brain two', lib2)
    orch = _orch_node([b1, b2])                     # runtime id is 'orch1'
    h = _make(orch, [b1, b2])
    h.chain_identity = str(orch_file)
    monkeypatch.setattr(lc, 'available', lambda: True)
    monkeypatch.setattr(lc, 'noul', lambda state, instructions: 0.9)
    h.brain_results = {'0xb1': 'opened', '0xb2': 'clicked'}
    h.llm_script = ['0', 'open it', '1', 'click it', 'DONE', 'S']

    h._execute_orchestrator_node(orch, None)

    assert not (tmp_path / 'learned').exists(), 'nothing may be frozen'
    assert json.loads(orch_file.read_text(encoding='utf-8'))['chain_import_nodes'] == []


def test_freeze_wiring_never_writes_a_dangling_edge(tmp_path, monkeypatch):
    """The wiring must land on an orchestrator that EXISTS in the chain file:
    with a mismatched runtime id (stale node, harness) it wires to the file's
    own orchestrator instead of writing a target the builder will drop."""
    import AI.laya_client as lc
    monkeypatch.delenv('LOOPER_LEARN', raising=False)
    lib = _write_chain(tmp_path / 'lib.json', {'sequences': []})
    learned = tmp_path / 'learned_x.json'
    learned.write_text(json.dumps({'description': 'x'}), encoding='utf-8')
    orch_file = tmp_path / 'ORCHESTRATOR.json'
    orch_file.write_text(json.dumps({
        'orchestrator_nodes': [{'node_id': '0xreal', 'connections': []}],
        'chain_import_nodes': [],
    }), encoding='utf-8')

    h = _Harness()
    h.chain_identity = str(orch_file)
    assert h._orchestrator_attach_learned_chain(
        str(learned), {'id': 'stale-id', 'type': 'orchestrator'}) is True

    wired = json.loads(orch_file.read_text(encoding='utf-8'))
    assert len(wired['chain_import_nodes']) == 1
    conn = wired['chain_import_nodes'][0]['connections'][0]
    assert conn['target_node_id'] == '0xreal', conn
    assert conn['input_port'] == 'chains'


def test_freeze_wiring_refuses_when_no_orchestrator_matches(tmp_path, monkeypatch):
    """Two orchestrators in the file and no id match: keep the artifact, wire
    nothing — a wrong edge is worse than a hand-wired one."""
    monkeypatch.delenv('LOOPER_LEARN', raising=False)
    learned = tmp_path / 'learned_y.json'
    learned.write_text(json.dumps({'description': 'y'}), encoding='utf-8')
    orch_file = tmp_path / 'ORCHESTRATOR.json'
    orch_file.write_text(json.dumps({
        'orchestrator_nodes': [{'node_id': '0xa', 'connections': []},
                               {'node_id': '0xb', 'connections': []}],
        'chain_import_nodes': [],
    }), encoding='utf-8')

    h = _Harness()
    h.chain_identity = str(orch_file)
    assert h._orchestrator_attach_learned_chain(
        str(learned), {'id': 'stale-id', 'type': 'orchestrator'}) is False
    assert json.loads(orch_file.read_text(encoding='utf-8'))['chain_import_nodes'] == []
    assert learned.exists(), 'the artifact itself is never thrown away'


def test_freeze_wiring_targets_an_llm_orchestrator_tools_port(tmp_path, monkeypatch):
    """The Orchestrator is now an LLM-node switch: its action port is 'tools',
    not 'chains'.  Reading only orchestrator_nodes left the learned chain on
    disk but UNWIRED, and a 'chains' edge on an LLM node is dropped at build
    time (measured 2026-10-02: a chains-only run produced no wire at all)."""
    monkeypatch.delenv('LOOPER_LEARN', raising=False)
    learned = tmp_path / 'learned_llm.json'
    learned.write_text(json.dumps({'description': 'x'}), encoding='utf-8')
    orch_file = tmp_path / 'SYSTEM.json'
    orch_file.write_text(json.dumps({
        'llm_nodes': [{'node_id': '0xllm', 'orchestrator_mode': True,
                       'connections': []}],
        'chain_import_nodes': [],
    }), encoding='utf-8')

    h = _Harness()
    h.chain_identity = str(orch_file)
    assert h._orchestrator_attach_learned_chain(
        str(learned), {'id': '0xllm', 'type': 'llm'}) is True

    wired = json.loads(orch_file.read_text(encoding='utf-8'))
    conn = wired['chain_import_nodes'][0]['connections'][0]
    assert conn['target_node_id'] == '0xllm', conn
    assert conn['input_port'] == 'tools', conn


def test_chains_port_done_probe_sees_the_step_that_failed_verification(
        tmp_path, monkeypatch):
    """A direct chain that RAN but failed its per-step verdict must stay on the
    directive's trace: the chains-only done-probe reads that trace.  A slice
    taken BEFORE the append read EMPTY, so the probe never ran (no verdict was
    even logged) and the level closed 'no_worker' though the chains port alone
    had served the goal (measured 2026-10-02)."""
    import AI.laya_client as lc
    monkeypatch.delenv('LOOPER_LEARN', raising=False)

    lib1 = _write_chain(tmp_path / 'go.json',
                        {'description': 'navigates to linkedin'})
    lib2 = _write_chain(tmp_path / 'jobs.json',
                        {'description': 'clicks the jobs button'})
    orch_file = tmp_path / 'ORCHESTRATOR.json'
    orch_file.write_text(json.dumps({
        'orchestrator_nodes': [{'node_id': 'orch1', 'connections': []}],
        'chain_import_nodes': [],
    }), encoding='utf-8')

    c1 = _chain_node('0xc1', 'Go chain', lib1)
    c2 = _chain_node('0xc2', 'Jobs chain', lib2)
    orch = _orch_with_ports(chains=[c1, c2])
    h = _make(orch, [c1, c2],
              goal='navigate to linkedin then click the jobs button')
    h.chain_identity = str(orch_file)
    h.directive_plan = ['navigate to linkedin', 'click the jobs button']

    monkeypatch.setattr(lc, 'available', lambda: True)
    monkeypatch.setattr(lc, 'ensure_running', lambda: True)

    def _noul(state, instructions):
        if 'accomplished what it was asked' in instructions:
            return 0.1 if 'jobs' in str(state) else 0.9   # step 2 fails
        if 'fully achieved' in instructions:
            return 0.9                                    # chains-port probe
        return 0.1
    monkeypatch.setattr(lc, 'noul', _noul)

    # Deterministic routing: the gate's scoring is not under test here.
    def _route(goal, chains, trace=None, used=None, state=None):
        used = used or set()
        want = '0xc2' if 'jobs' in str(goal) else '0xc1'
        for c in chains:
            if c['chain_id'] == want and c['chain_id'] not in used:
                return c
        return None
    h._orchestrator_route_chain_direct = _route

    def _chain(node, stop_flag=None, *a, **k):
        h.set_variable('chain_tool_last_result_text', f"{node.get('id')} ok")
        return '__done__'
    h._execute_chain_import_node = _chain

    h._execute_orchestrator_node(orch, None)

    # The probe judged the step that failed verification, so the level closed
    # structurally complete ('done') instead of 'no_worker'.
    assert orch.get('_orchestrator_fulfilled') is True


def test_attach_resolves_the_id_the_builder_actually_keeps(tmp_path, monkeypatch):
    """The built orchestrator node has NO top-level 'id' (keys: connections /
    data / inputs / type) — its id lives in data['node_id'].  Reading only
    node['id'] silently resolved to '' and made every attach log "no chain file
    to edit" without wiring (measured 2026-09-24: a perfect 3-step run froze its
    learned chain to disk and never attached it, which the user saw as the
    chains port staying empty)."""
    monkeypatch.delenv('LOOPER_LEARN', raising=False)
    learned = tmp_path / 'learned_id.json'
    learned.write_text(json.dumps({'description': 'x'}), encoding='utf-8')
    orch_file = tmp_path / 'ORCHESTRATOR.json'
    orch_file.write_text(json.dumps({
        'orchestrator_nodes': [{'node_id': '0xreal', 'connections': []}],
        'chain_import_nodes': [],
    }), encoding='utf-8')

    h = _Harness()
    h.chain_identity = str(orch_file)
    # Exactly the builder's shape: no top-level id key.
    assert h._orchestrator_attach_learned_chain(
        str(learned), {'type': 'orchestrator',
                       'data': {'node_id': '0xreal'}}) is True
    cfg = json.loads(orch_file.read_text(encoding='utf-8'))
    wired = [n for n in cfg['chain_import_nodes']
             if 'learned_id' in str(n.get('chain_file_path'))]
    assert len(wired) == 1
    assert wired[0]['connections'][0]['target_node_id'] == '0xreal'
    assert wired[0]['connections'][0]['input_port'] == 'chains'


def test_learned_artifact_is_pure_ascii(tmp_path, monkeypatch):
    """A learned chain must load through EVERY reader: the GUI's load path used
    the locale codec, and a single '→' in the description made the file
    unreadable there ("can't decode byte 0x9d", measured 2026-09-24).  The
    writer escapes all non-ASCII, so the artifact is ASCII JSON on disk while
    the decoded text keeps its characters."""
    monkeypatch.delenv('LOOPER_LEARN', raising=False)
    a = _write_chain(tmp_path / 'a.json', {'sequences': []})
    b = _write_chain(tmp_path / 'b.json', {'sequences': []})
    orch_file = tmp_path / 'ORCHESTRATOR.json'
    orch_file.write_text('{}', encoding='utf-8')

    h = _Harness()
    h.chain_identity = str(orch_file)
    goal = 'open caf\u00e9 \u2014 then the jobs board'
    written = h._orchestrator_write_learned_chain(
        goal, [(str(a), 'one \u2192 two'), (str(b), 'two \u2014 three')])
    assert written, 'the artifact is written'
    raw = open(written, 'rb').read()
    assert all(byte < 128 for byte in raw), \
        'learned artifacts must be pure ASCII on disk'
    assert json.loads(raw.decode('utf-8'))['routing']['examples'] == [goal]


def test_freeze_rebuilds_its_own_artifact_for_the_same_goal(tmp_path, monkeypatch):
    """ONE artifact per goal: re-running the same task rebuilds the learned
    chain in place instead of adding timestamped twins (four identical files
    accumulated on 2026-09-23)."""
    import AI.laya_client as lc
    monkeypatch.delenv('LOOPER_LEARN', raising=False)
    lib1 = _write_chain(tmp_path / 'lib1.json',
                        {'sequences': [], 'description': 'opens the monitor search'})
    lib2 = _write_chain(tmp_path / 'lib2.json',
                        {'sequences': [], 'description': 'clicks the cheapest monitor'})
    orch_file = tmp_path / 'ORCHESTRATOR.json'
    orch_file.write_text(json.dumps({
        'orchestrator_nodes': [{'node_id': 'orch1', 'connections': []}],
        'chain_import_nodes': [],
    }), encoding='utf-8')

    b1 = _brain('0xb1', 'Brain one', lib1)
    b2 = _brain('0xb2', 'Brain two', lib2)
    orch = _orch_node([b1, b2])
    monkeypatch.setattr(lc, 'available', lambda: True)
    monkeypatch.setattr(lc, 'noul', lambda state, instructions: 0.9)

    for _run in (1, 2):
        h = _make(orch, [b1, b2])
        h.chain_identity = str(orch_file)
        h.brain_results = {'0xb1': 'opened', '0xb2': 'clicked'}
        h.llm_script = ['0', 'open it', '1', 'click it', 'DONE', 'S']
        h._execute_orchestrator_node(orch, None)

    assert len(list((tmp_path / 'learned').glob('*.json'))) == 1
    wired = json.loads(orch_file.read_text(encoding='utf-8'))
    assert len(wired['chain_import_nodes']) == 1, 'never wired twice'


def test_freeze_skips_unverified_single_step_and_stopped_runs(tmp_path, monkeypatch):
    import AI.laya_client as lc
    monkeypatch.delenv('LOOPER_LEARN', raising=False)

    lib1 = _write_chain(tmp_path / 'lib1.json', {'sequences': []})
    orch_file = tmp_path / 'ORCHESTRATOR.json'
    orch_file.write_text(json.dumps({
        'orchestrator_nodes': [{'node_id': 'orch1', 'connections': []}],
        'chain_import_nodes': [],
    }), encoding='utf-8')

    monkeypatch.setattr(lc, 'available', lambda: True)
    # Verdict False: a failed verification disqualifies the freeze.
    monkeypatch.setattr(lc, 'noul', lambda state, instructions: 0.1)

    b1 = _brain('0xb1', 'Brain one', lib1)
    orch = _orch_node([b1])
    h = _make(orch, [b1])
    h.chain_identity = str(orch_file)
    h.brain_results = {'0xb1': 'ran'}
    h.llm_script = ['0', 'do it', 'DONE', 'S']

    h._execute_orchestrator_node(orch, None)

    assert not (tmp_path / 'learned').exists(), 'nothing may be frozen'
    wired = json.loads(orch_file.read_text(encoding='utf-8'))
    assert wired['chain_import_nodes'] == []


def test_read_chain_routing_examples(tmp_path):
    """The router's real signal: verbatim past requests, normalized and capped."""
    h = _Harness()
    p = tmp_path / 'with_routing.json'
    p.write_text(json.dumps({'routing': {'examples': [
        '  open  the jobs page ', 'second example']}}), encoding='utf-8')
    assert h._orchestrator_read_chain_routing(str(p)) == [
        'open the jobs page', 'second example']

    capped = tmp_path / 'many.json'
    capped.write_text(json.dumps({'routing': {
        'examples': [f'request {i}' for i in range(20)]}}), encoding='utf-8')
    assert len(h._orchestrator_read_chain_routing(str(capped))) == 5

    plain = tmp_path / 'plain.json'
    plain.write_text(json.dumps({'description': 'x'}), encoding='utf-8')
    assert h._orchestrator_read_chain_routing(str(plain)) == []
    assert h._orchestrator_read_chain_routing(str(tmp_path / 'gone.json')) == []


def test_freeze_respects_the_kill_switch(tmp_path, monkeypatch):
    import AI.laya_client as lc
    monkeypatch.setenv('LOOPER_LEARN', 'off')

    lib1 = _write_chain(tmp_path / 'lib1.json', {'sequences': []})
    lib2 = _write_chain(tmp_path / 'lib2.json', {'sequences': []})
    orch_file = tmp_path / 'ORCHESTRATOR.json'
    orch_file.write_text(json.dumps({
        'orchestrator_nodes': [{'node_id': 'orch1', 'connections': []}],
        'chain_import_nodes': [],
    }), encoding='utf-8')

    monkeypatch.setattr(lc, 'available', lambda: True)
    monkeypatch.setattr(lc, 'noul', lambda state, instructions: 0.9)

    b1 = _brain('0xb1', 'Brain one', lib1)
    b2 = _brain('0xb2', 'Brain two', lib2)
    orch = _orch_node([b1, b2])
    h = _make(orch, [b1, b2])
    h.chain_identity = str(orch_file)
    h.brain_results = {'0xb1': 'a', '0xb2': 'b'}
    h.llm_script = ['0', 'open it', '1', 'click it', 'DONE', 'S']

    h._execute_orchestrator_node(orch, None)

    assert not (tmp_path / 'learned').exists()
