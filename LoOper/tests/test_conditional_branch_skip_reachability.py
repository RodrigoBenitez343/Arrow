"""Branch-exclusive sweep must not leak through a loop-back into the gating conditional.

Regression for the LinkedIn run: ``validation form chain`` decides 'submit?'.  Its
FALSE leg eventually loops back into that same conditional ('checking if the form
asks for education' -> the submit decision), so the unchosen-branch BFS re-entered
the conditional and walked its TRUE leg, "reaching" the submit -> popup chain_import
on the CHOSEN branch.  That node was then marked as an unchosen-branch (skipped)
tool provider every run:

    [MARK-SKIP] Cond 0x13b3e955850: also skipped 1 unchosen-branch tool provider(s): ['0x13b3e8faa10']

(0x13b3e8faa10 = the 'handle sent popup' import).  It only ran because the advisory
SKIP-OVERRIDE happened to re-arm it; a chain whose submit node itself did not
re-arm it lost the popup path entirely.

Run:  python -m pytest LoOper/tests/test_conditional_branch_skip_reachability.py
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


def _node(ntype, connections=None, inputs=None):
    return {
        'type': ntype,
        'connections': connections or {},
        'inputs': inputs or [],
        'data': {},
    }


def _build_graph():
    """
    cond(true)  -> SUBMIT_OUT -> SUBMIT_SEQ -> POPUP_IMPORT   (chosen submit leg)
    cond(false) -> FALSE_OUT  -> FILL_OUT   -> FILL_IMPORT    (fill leg)
    FILL_OUT    -> LOOPBACK_OUT -> cond                        (back-edge into cond)
    """
    cond = 'COND'
    out = {'output_port': 'output', 'input_port': 'input'}
    return {
        cond: _node('conditional', {
            'true': [{'node_id': 'SUBMIT_OUT', 'input_port': 'input'}],
            'false': [{'node_id': 'FALSE_OUT', 'input_port': 'input'}],
        }),
        'SUBMIT_OUT': _node('output', {'output': [dict(out, target_node_id='SUBMIT_SEQ')]}),
        'SUBMIT_SEQ': _node('web_sequence', {'output': [dict(out, target_node_id='POPUP_IMPORT')]}),
        'POPUP_IMPORT': _node('chain_import', {}, [
            {'from_node': 'SUBMIT_SEQ', 'input_port': 'input'},
        ]),
        'FALSE_OUT': _node('output', {'output': [dict(out, target_node_id='FILL_OUT')]}),
        'FILL_OUT': _node('output', {
            'output': [
                dict(out, target_node_id='FILL_IMPORT'),
                dict(out, target_node_id='LOOPBACK_OUT'),
            ],
        }),
        'FILL_IMPORT': _node('chain_import', {}, [
            {'from_node': 'FILL_OUT', 'input_port': 'input'},
        ]),
        'LOOPBACK_OUT': _node('output', {'output': [dict(out, target_node_id=cond)]}),
    }


def _executor():
    ex = WorkflowExecutor(_build_graph(), None, _FakeLLM(), None)
    return ex


def test_chosen_branch_popup_import_is_not_skipped():
    ex = _executor()
    ex._mark_skipped('COND', 'true', 'false')
    assert 'POPUP_IMPORT' not in ex.skipped_nodes, (
        "popup import on the CHOSEN submit branch must never be swept as unchosen: "
        f"skipped={sorted(ex.skipped_nodes)}"
    )


def test_unchosen_fill_branch_is_still_skipped():
    ex = _executor()
    ex._mark_skipped('COND', 'true', 'false')
    assert 'FILL_IMPORT' in ex.skipped_nodes, (
        "the fill-branch import on the unchosen leg must still be skipped: "
        f"skipped={sorted(ex.skipped_nodes)}"
    )


def test_reverse_choice_mirrors():
    ex = _executor()
    ex._mark_skipped('COND', 'false', 'true')
    assert 'FILL_IMPORT' not in ex.skipped_nodes
    assert 'POPUP_IMPORT' in ex.skipped_nodes


# ---------------------------------------------------------------------------
# Pinned to the real chain: 'validation form chain.json'.  Its submit decision
# 0x13b3e955850 loops back from the education leg (0x262de773cd0 -> 0x13b3e955850),
# and the two legs end at:  submit? TRUE  -> submit application.json -> 'handle
# sent popup' import 0x13b3e8faa10 (terminal, ends the chain);  submit? FALSE ->
# ... -> 'filling the form' -> 'form filler - persistent' import 0x13b39209390
# (terminal, loops).  The sweep must mirror the branch actually taken.
# ---------------------------------------------------------------------------
_REAL = {
    '0x13b3e955850': ('conditional', {'true': ['0x262d5947a90'], 'false': ['0x262d9b730d0']},
                      ['0x262d359e990', '0x262de773cd0']),
    '0x13b3e913950': ('conditional', {'true': ['0x262de7ef010'], 'false': ['0x262de772250']},
                      ['0x262d9b730d0']),
    '0x13b3ea815d0': ('conditional', {'true': ['0x262d9b7c2d0'], 'false': ['0x262de773cd0']},
                      ['0x262de772250']),
    '0x13b39209390': ('chain_import', {}, ['0x262d594b3d0']),
    '0x13b3e8faa10': ('chain_import', {}, ['0x13b3cec24d0']),
    '0x262d5947a90': ('output', {'output': ['0x13b3cec24d0']}, ['0x13b3e955850']),
    '0x262d359e990': ('output', {'output': ['0x13b3e955850']}, []),
    '0x262d9b730d0': ('output', {'output': ['0x13b3e913950']}, ['0x13b3e955850']),
    '0x262de7ef010': ('output', {'output': ['0x13b3cec2610']}, ['0x13b3e913950']),
    '0x262de772250': ('output', {'output': ['0x13b3ea815d0']}, ['0x13b3e913950']),
    '0x262d9b7c2d0': ('output', {'output': ['0x13b38454050']}, ['0x13b3ea815d0']),
    '0x262de773cd0': ('output', {'output': ['0x13b3e955850']}, ['0x13b3ea815d0']),
    '0x262d594b3d0': ('output', {'output': ['0x13b39209390']}, ['0x13b38454050', '0x13b3cec2610']),
    '0x13b38454050': ('web_sequence', {'output': ['0x262d594b3d0']}, ['0x262d9b7c2d0']),
    '0x13b3cec2610': ('web_sequence', {'output': ['0x262d594b3d0']}, ['0x262de7ef010']),
    '0x13b3cec24d0': ('web_sequence', {'output': ['0x13b3e8faa10']}, ['0x262d5947a90']),
}


def _real_executor():
    graph = {}
    for nid, (t, outs, ins) in _REAL.items():
        graph[nid] = {
            'type': t,
            'connections': {
                port: [{'output_port': port, 'target_node_id': x, 'input_port': 'input'}
                       for x in targets]
                for port, targets in outs.items()
            },
            'inputs': [{'from_node': f, 'input_port': 'input'} for f in ins],
            'data': {},
        }
    return WorkflowExecutor(graph, None, _FakeLLM(), None)


def test_real_chain_submit_leg_keeps_popup_and_ends():
    ex = _real_executor()
    ex._mark_skipped('0x13b3e955850', 'true', 'false')
    assert '0x13b3e8faa10' not in ex.skipped_nodes, "submit leg must keep handle-sent-popup alive"
    assert '0x13b39209390' in ex.skipped_nodes, "fill leg must be skipped when submitting"


def test_real_chain_fill_leg_keeps_form_filler_import():
    ex = _real_executor()
    ex._mark_skipped('0x13b3e955850', 'false', 'true')
    assert '0x13b39209390' not in ex.skipped_nodes
    assert '0x13b3e8faa10' in ex.skipped_nodes
