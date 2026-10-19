"""Orchestrator honesty fixes: stop-reason text, sub-word routing overlap,
goal-ledger accumulation, and unfulfilled-run failure verdicts.

Run:  python -m pytest LoOper/tests/test_orch_honesty.py
"""
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from player.multi_sequence.worflow_interpreter_modules.executor_modules.core import (  # noqa: E402
    WorkflowExecutor,
)
from player.multi_sequence.worflow_interpreter_modules.executor_modules.dependencies import (  # noqa: E402
    DependenciesMixin,
)
from player.multi_sequence.worflow_interpreter_modules.executor_modules.orchestrator_ops import (  # noqa: E402
    OrchestratorMixin,
    _orchestrator_word_overlap,
)
from player.agentic_ops import goal_ledger  # noqa: E402
from test_orchestrator_node import _Harness  # noqa: E402


# ---------------------------------------------------------------------------
# Sub-word object overlap (direct-chain gate)
# ---------------------------------------------------------------------------

def test_word_overlap_names_fused_filename_object():
    assert _orchestrator_word_overlap({'google'}, {'searchgoogle'})
    assert _orchestrator_word_overlap({'searchgoogle'}, {'google'})


def test_word_overlap_ignores_glue_and_unrelated():
    # len < 4 never routes ('new', 'com', 'nlp' are glue, not objects).
    assert not _orchestrator_word_overlap({'new'}, {'searchgoogle'})
    assert not _orchestrator_word_overlap({'nlp'}, {'searchgoogle'})
    assert not _orchestrator_word_overlap({'linkedin'}, {'navigate'})


# ---------------------------------------------------------------------------
# Honest stop text
# ---------------------------------------------------------------------------

def test_stop_text_names_the_gate_reason():
    h = _Harness()
    h._orch_last_note = (
        "no chain's description names the step's objects (google, nlp, new)")
    text = h._orchestrator_stop_text('search NLP news on google', 'no_worker')
    assert 'The goal could not be advanced' in text
    assert 'google' in text
    assert 'No worker served it' in text
    assert 'Wire a brain' in text


def test_stop_text_unknown_reason_still_reports_it():
    h = _Harness()
    text = h._orchestrator_stop_text('do the thing', 'unserved')
    assert 'stop reason: unserved' in text


# ---------------------------------------------------------------------------
# Goal ledger: append (not replace), extra fields, env override
# ---------------------------------------------------------------------------

def test_record_activation_appends_trace_and_fields(tmp_path, monkeypatch):
    monkeypatch.setenv('LOOPER_GOAL_LEDGER', str(tmp_path / 'goals.json'))
    g = goal_ledger.start_goal('take a note')
    assert goal_ledger.record_activation(
        g['id'], [{'step': 'a', 'ok': True}], 'open')
    assert goal_ledger.record_activation(
        g['id'], [{'step': 'b', 'ok': True}], 'blocked',
        stop_reason='no_worker', last_error='nobody served it')
    saved = goal_ledger.load_goals()
    assert len(saved) == 1
    assert [t.get('step') for t in saved[0]['trace']] == ['a', 'b']
    assert saved[0]['status'] == 'blocked'
    assert saved[0]['stop_reason'] == 'no_worker'
    assert saved[0]['last_error'] == 'nobody served it'


def test_start_goal_persists(tmp_path, monkeypatch):
    monkeypatch.setenv('LOOPER_GOAL_LEDGER', str(tmp_path / 'goals.json'))
    goal_ledger.start_goal('plan the trip')
    assert goal_ledger.get_active_goal()['text'] == 'plan the trip'


# ---------------------------------------------------------------------------
# Unfulfilled orchestrator fails the run (no 'route' handoff wired)
# ---------------------------------------------------------------------------

class _FakeLLM:
    def __init__(self):
        self.variables = {}

    def get_variable(self, name, default=None):
        return self.variables.get(name, default)

    def set_variable(self, name, value):
        self.variables[name] = value

    def get_all_variables(self):
        return dict(self.variables)


class _StubFallback:
    pass


def _orch_graph():
    return {
        'O1': {
            'id': 'O1',
            'type': 'orchestrator',
            'data': {'goal': 'search nlp news on google',
                     'connections': {}},
            'connections': {},
            'inputs': [],
        },
    }


def _make_executor(graph):
    ex = WorkflowExecutor(graph, None, _FakeLLM(), _StubFallback())
    ex.chain_id = 'orch_honesty'
    ex._context_db = None
    return ex


def test_unfulfilled_orchestrator_without_route_fails_run(monkeypatch):
    graph = _orch_graph()
    ex = _make_executor(graph)

    def _unfulfilled(node, stop_flag=None, *a, **k):
        node['_orchestrator_fulfilled'] = False
        return '__done__'

    monkeypatch.setattr(ex, '_execute_orchestrator_node', _unfulfilled)
    assert ex.execute_workflow() is False


def test_fulfilled_orchestrator_completes_run(monkeypatch):
    graph = _orch_graph()
    ex = _make_executor(graph)

    def _fulfilled(node, stop_flag=None, *a, **k):
        node['_orchestrator_fulfilled'] = True
        return '__done__'

    monkeypatch.setattr(ex, '_execute_orchestrator_node', _fulfilled)
    assert ex.execute_workflow() is True
