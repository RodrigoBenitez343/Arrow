"""A form pass costs the same per field, however many fields it has.

Two regression guards, both read from ``AI/engine_metrics.py``:

  - a HELD pass never releases the scorer between fields (the bug that made
    every field pay a ~2 s Laya cold load), while an unheld one still does;
  - the per-field samples stay flat across ten fields instead of climbing.

Run:  python -m pytest LoOper/tests/test_form_field_cost_is_flat.py
"""

import AI.engine_metrics as metrics
import AI.laya_client as laya
from player.multi_sequence.worflow_interpreter_modules.executor_modules \
    .form_filler_modules.suite_crag import _cfg, _harness


def _quiet_metrics(monkeypatch):
    metrics.reset()
    monkeypatch.delenv("LOOPER_ENGINE_METRICS", raising=False)
    monkeypatch.setattr(metrics, "_append", lambda line: None)


def _no_embed(h):
    """Never start the real embedding server: ranking falls back to lexical."""
    h._ff_embed_call = lambda cfg: (lambda texts: [])


def _releases(h):
    calls = []
    h.ev.release = lambda: calls.append(1)
    return calls


def _halves(values):
    mid = len(values) // 2
    first = values[:mid] or values
    second = values[mid:] or values
    return (sum(first) / len(first), sum(second) / len(second))


def test_a_held_pass_never_releases_between_fields(monkeypatch):
    _quiet_metrics(monkeypatch)
    h = _harness({"benitezrodrigo169@gmail.com": 0.9})
    _no_embed(h)
    calls = _releases(h)
    monkeypatch.setattr(laya, "held", lambda: True)
    cfg = _cfg(h)

    for index in range(10):
        h._ff_retrieve("Email address %d" % index, cfg, h.pool, [])

    assert calls == [], "a held engine must survive every field"
    assert metrics.count("engine_unload") == 0, \
        "nothing was torn down between fields"


def test_an_unheld_call_still_releases_the_scorer(monkeypatch):
    _quiet_metrics(monkeypatch)
    h = _harness({"benitezrodrigo169@gmail.com": 0.9})
    _no_embed(h)
    calls = _releases(h)
    monkeypatch.setattr(laya, "held", lambda: False)
    cfg = _cfg(h)

    for index in range(3):
        h._ff_retrieve("Email address %d" % index, cfg, h.pool, [])

    assert len(calls) == 3, "a standalone retrieval still frees the pool"


def test_per_field_cost_is_flat(monkeypatch):
    _quiet_metrics(monkeypatch)
    h = _harness({"benitezrodrigo169@gmail.com": 0.9})
    _no_embed(h)
    monkeypatch.setattr(laya, "held", lambda: True)
    cfg = _cfg(h)

    for index in range(10):
        h._ff_retrieve("Email address %d" % index, cfg, h.pool, [])

    series = metrics.series("form_field")
    assert len(series) == 10, "one sample per scored field"
    first, second = _halves(series)
    assert second <= first * 2 + 1.0, (
        "per-field ms must not trend upward: %r" % (series,))
