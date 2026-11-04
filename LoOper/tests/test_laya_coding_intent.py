"""Laya decides whether a request is a coding instruction or a question.

The keyword heuristic (`_is_question`) stays only as the fallback for when the
engine is not shipped/built; the panel asks Laya first, off the UI thread.
"""

import player.code_agent_ops.panel.mixin_agent as mixin_agent  # noqa: F401
import AI.laya_hooks as hooks


def test_fallback_runs_when_engine_is_silent(monkeypatch):
    monkeypatch.setattr(hooks, 'coding_intent', lambda text: None)
    calls = []
    verdict = hooks.coding_intent_or(
        'can you build me an app?', lambda: calls.append(1) or True)
    assert verdict is True and calls == [1]


def test_engine_verdict_wins_over_the_heuristic(monkeypatch):
    monkeypatch.setattr(hooks, 'coding_intent', lambda text: False)
    verdict = hooks.coding_intent_or('write a parser', lambda: True)
    assert verdict is False           # a question, whatever the wording says


def test_engine_exception_falls_back(monkeypatch):
    def boom(text):
        raise RuntimeError('engine died')
    monkeypatch.setattr(hooks, 'coding_intent', boom)
    assert hooks.coding_intent_or('x', lambda: False) is False


def test_empty_request_is_not_classified():
    # An empty body never reaches the engine (no wasted forward pass).
    assert hooks.coding_intent('   ') is None


def test_complexity_is_none_without_an_engine(monkeypatch):
    import AI.laya_client as lc
    monkeypatch.setattr(lc, 'choice', lambda *a, **k: None)
    assert hooks.task_complexity('big.py: a GUI with threads') is None
    assert hooks.task_complexity('') is None


def test_complexity_passes_only_known_grades(monkeypatch):
    import AI.laya_client as lc
    monkeypatch.setattr(lc, 'choice', lambda *a, **k: 'complex')
    assert hooks.task_complexity('big.py: a GUI') == 'complex'
    monkeypatch.setattr(lc, 'choice', lambda *a, **k: 'banana')
    assert hooks.task_complexity('x.py: y') is None


def test_intent_worker_survives_a_missing_engine(monkeypatch):
    """The worker must emit something even when Laya cannot start."""
    import os
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PyQt5.QtWidgets import QApplication
    from player.code_agent_ops.execution import _IntentWorker

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(hooks, 'coding_intent', lambda text: None)
    seen = []
    w = _IntentWorker('build me')
    w.finished_intent.connect(seen.append)
    w.run()
    assert seen == [None]
    assert app is not None
