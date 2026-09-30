"""ESC / stop-flag aborts must not be reported as node failures.

Regression for the misreport seen in the LinkedIn Easy-Apply runs: an imported
chain was stopped by the user's ESC while the form filler was mid-flight.  Every
node had completed successfully and the log contained ZERO warnings and ZERO
real errors, yet the parent chain_import node was reported as
``execution failed, stopping workflow`` and the whole run returned False.

A user cancel is not a failure.  The loop-top stop handler already returns True
for it; the node-returned-None path must agree.

Run:  python -m pytest LoOper/tests/test_abort_vs_node_failure.py
"""

import pytest

from player.multi_sequence.worflow_interpreter_modules.executor_modules.core import (
    WorkflowExecutor,
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


def _make_executor(monkeypatch, state):
    """A real WorkflowExecutor over a 1-node graph whose node returns None."""
    graph = {
        'n1': {
            'type': 'code',
            'id': 'n1',
            'node_id': 'n1',
            'data': {'code': 'result = 1', 'output_variable': 'result'},
            'connections': {},
            'inputs': [],
        },
    }
    ex = WorkflowExecutor(graph, None, _FakeLLM(), _StubFallback())
    ex.chain_id = 'test_chain'
    ex._context_db = None

    def _node_returns_none(node, stop_flag=None, *a, **k):
        # The user hits ESC while this node is running.
        if state.get('abort'):
            state['stop'] = True
        return None

    monkeypatch.setattr(ex, '_execute_code_node', _node_returns_none)
    return ex


def test_node_none_with_stop_flag_is_an_abort_not_a_failure(monkeypatch):
    state = {'stop': False, 'abort': True}
    ex = _make_executor(monkeypatch, state)
    failed_calls = []

    result = ex.execute_workflow(
        stop_flag=lambda: state['stop'],
        on_node_failed=lambda *a: failed_calls.append(a),
    )

    assert result is True, "a user cancel must not be reported as a failure"
    assert failed_calls == [], "an aborted node must not be flagged as broken"


def test_node_none_without_stop_flag_is_still_a_failure(monkeypatch):
    state = {'stop': False, 'abort': False}
    ex = _make_executor(monkeypatch, state)
    failed_calls = []

    result = ex.execute_workflow(
        stop_flag=lambda: state['stop'],
        on_node_failed=lambda *a: failed_calls.append(a),
    )

    assert result is False, "a real node failure must still fail the workflow"
    assert len(failed_calls) == 1
    assert failed_calls[0][0] == 'n1'
