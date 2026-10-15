"""Laya grades — fail-open verdicts for the builder's micro-decisions.

Run:  python -m pytest LoOper/tests/test_builder_laya_grades.py
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from player.builder import laya_grades as lg  # noqa: E402


def _mock_laya(monkeypatch, noul=None, choice=None, available=True):
    """Stub the embedded Laya client (test_orchestrator_node pattern)."""
    import AI.laya_client as lc
    monkeypatch.setattr(lc, 'available', lambda: available)
    monkeypatch.setattr(lc, 'ensure_running', lambda: available)
    monkeypatch.setattr(lc, 'noul',
                        (lambda state, instructions: noul) if noul is not None
                        else (lambda state, instructions: None))
    monkeypatch.setattr(lc, 'choice',
                        (lambda state, instructions, criteria: choice)
                        if choice is not None
                        else (lambda state, instructions, criteria: None))
    return lc


def test_grade_recording_passes_above_floor(monkeypatch):
    _mock_laya(monkeypatch, noul=0.9)
    assert lg.grade_recording('click apply', 'web click on Easy Apply') is True


def test_grade_recording_fails_below_floor(monkeypatch):
    _mock_laya(monkeypatch, noul=0.2)
    assert lg.grade_recording('click apply', 'typed an email') is False


def test_grade_recording_none_when_unavailable(monkeypatch):
    _mock_laya(monkeypatch, available=False)
    assert lg.grade_recording('x', 'y') is None


def test_grade_description_passes(monkeypatch):
    _mock_laya(monkeypatch, noul=0.8)
    assert lg.grade_description('opens linkedin jobs', 'navigates and lists') is True


def test_pick_node_type_returns_a_pick(monkeypatch):
    _mock_laya(monkeypatch, choice='web_sequence')
    assert lg.pick_node_type('click the apply button') == 'web_sequence'


def test_pick_node_type_none_on_bad_pick(monkeypatch):
    _mock_laya(monkeypatch, choice='teleport')  # outside the offered set
    assert lg.pick_node_type('x') is None


def test_pick_node_type_offers_purposes_and_state(monkeypatch):
    """The node type is picked from real PURPOSE descriptions, over a premise
    that carries the goal and the canvas."""
    seen = {}
    import AI.laya_client as lc
    monkeypatch.setattr(lc, 'available', lambda: True)
    monkeypatch.setattr(lc, 'ensure_running', lambda: True)

    def _choice(state, instructions, criteria):
        seen['state'] = state
        seen['criteria'] = criteria
        return 'llm'

    monkeypatch.setattr(lc, 'choice', _choice)
    lines = []
    got = lg.pick_node_type('build a bot that logs into linkedin',
                            'Canvas:\n(empty)', [], trace=lines.append)
    assert got == 'llm'
    assert all(v.strip() for v in seen['criteria'].values())  # real purposes
    assert 'linkedin' in seen['state']                        # goal reaches Laya
    assert 'Canvas' in seen['state']
    assert lines and 'node type choice: offered' in lines[-1]


def test_planning_verdicts_are_traced(monkeypatch):
    """Every Laya planning verdict lands in the trace with its probability."""
    _mock_laya(monkeypatch, noul=0.9)
    lines = []
    assert lg.wants_to_ask('g', 'c', [], trace=lines.append) is True
    assert 'ask-probe' in lines[0] and 'ASK' in lines[0]

    _mock_laya(monkeypatch, noul=0.1)
    lines = []
    assert lg.is_done('g', 'c', ['[create_node] created'],
                      trace=lines.append) is False
    assert 'done-probe' in lines[0] and 'keep building' in lines[0]


def test_grades_never_raise_when_laya_import_fails(monkeypatch):
    # Every grade is fail-open: even a broken client returns None.
    import builtins
    real_import = builtins.__import__

    def _boom(name, *args, **kwargs):
        if name == 'AI.laya_client' or name == 'AI':
            raise ImportError('no laya')
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', _boom)
    assert lg.grade_recording('x', 'y') is None
    assert lg.pick_node_type('x') is None
    assert lg.grade_chain_match('g', 'x', 'y', 'z') is None


def test_grade_chain_match_reads_and_traces(monkeypatch):
    """The composer's match grade: one noul pass, traced with its
    probability and read over the RUNTIME gate's framing."""
    seen = {}
    import AI.laya_client as lc
    monkeypatch.setattr(lc, 'available', lambda: True)
    monkeypatch.setattr(lc, 'ensure_running', lambda: True)

    def _noul(state, instructions):
        seen['state'] = state
        seen['instructions'] = instructions
        return 0.83

    monkeypatch.setattr(lc, 'noul', _noul)
    lines = []
    p = lg.grade_chain_match('scrape google', 'navigate to linkedin',
                             'Linkedin_nav',
                             'navigate to linkedin on the browser',
                             trace=lines.append)
    assert p == 0.83
    # The premise carries the goal + the NEXT STEP; the hypothesis names the
    # chain's capability — the shape the runtime gate was calibrated on.
    assert 'Goal: scrape google' in seen['state']
    assert 'Next step: navigate to linkedin' in seen['state']
    assert seen['instructions'] == \
        'The next step toward the goal is to navigate to linkedin on the '\
        'browser.'
    assert lines and 'chain match' in lines[-1] and '0.83' in lines[-1]


def test_grade_chain_match_none_when_unavailable(monkeypatch):
    _mock_laya(monkeypatch, available=False)
    lines = []
    assert lg.grade_chain_match('g', 'x', 'y', 'z', trace=lines.append) is None
    assert lines and 'unavailable' in lines[-1]
