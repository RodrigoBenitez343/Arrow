"""A graph loop-back that ran no work node is a dead end, not a loop.

Regression for the LinkedIn run: after the submit->'handle sent popup' leg
ended, the post-popup 'validation form chain' run walked its ladder
(submit? F -> review? F -> pages? F -> 'education') and the 'education' output's
back-edge to 'submit?' was treated as a repeatable loop.  Every pass executed
ONLY outputs/conditionals, and the engine re-armed (un-skipped) the review/next/
submit legs each pass - a loop with nothing to repeat.  The run span 10
iterations until the user pressed ESC.

A pass that ran no work node cannot progress; the chain must finish.

Run:  python -m pytest LoOper/tests/test_loop_deadend_guard.py
"""

from player.multi_sequence.worflow_interpreter_modules.executor_modules.core import (
    WorkflowExecutor,
)


class _FakeLLM:
    def __init__(self):
        self.variables = {}
        self.workflow_graph = None

    def get_variable(self, name, default=None):
        return self.variables.get(name, default)

    def set_variable(self, name, value):
        self.variables[name] = value

    def get_all_variables(self):
        return dict(self.variables)


def _node(ntype):
    return {'type': ntype, 'connections': {}, 'inputs': [], 'data': {}}


def _exec(graph, executed):
    ex = WorkflowExecutor(graph, None, _FakeLLM(), None)
    ex._loop_pass_exec = set(executed)
    return ex


def _graph(types):
    return {nid: _node(t) for nid, t in types.items()}


def test_decision_only_pass_is_a_dead_end():
    g = _graph({
        'COND': 'conditional',
        'OUT_A': 'output',
        'OUT_B': 'output',
    })
    ex = _exec(g, ['OUT_A', 'COND', 'OUT_B'])
    assert ex._loop_pass_is_dead_end('COND') is True


def test_pass_with_work_node_is_a_real_loop():
    g = _graph({
        'COND': 'conditional',
        'OUT_A': 'output',
        'NEXT': 'web_sequence',
        'FILL': 'form_filler',
    })
    ex = _exec(g, ['OUT_A', 'COND', 'NEXT', 'FILL'])
    assert ex._loop_pass_is_dead_end('COND') is False


def test_non_conditional_target_never_dead_ends():
    g = _graph({'COND': 'conditional', 'OUT_A': 'output', 'SEQ': 'sequence'})
    ex = _exec(g, ['OUT_A'])
    assert ex._loop_pass_is_dead_end('SEQ') is False


def test_empty_pass_is_a_dead_end():
    g = _graph({'COND': 'conditional'})
    ex = _exec(g, [])
    assert ex._loop_pass_is_dead_end('COND') is True
