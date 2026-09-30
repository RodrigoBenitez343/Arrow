"""laya_hooks: Laya-first verdicts for the ComoRAG / form-filler pipelines.

Both pipelines used to ask their small LLM for the per-cycle relevance verdict
and the sufficiency assessment; the embedded Laya engine is now the DEFAULT
evaluator (LLM = fallback when the engine is unavailable).  These tests pin
the contract the engine and the wirings rely on: noul-threshold verdicts, the
exact verdict-line shape, and None-on-unavailable so the fallback engages.

Run:  python -m pytest LoOper/tests/test_laya_hooks.py
"""

import AI.laya_hooks as hooks


def _fake_noul(monkeypatch, probs):
    calls = {"calls": []}

    def _noul(state, instructions):
        calls["calls"].append((state, instructions))
        return probs.pop(0) if probs else None

    monkeypatch.setattr(hooks, "_noul", _noul)
    return calls


def test_relevance_verdict_per_finding(monkeypatch):
    calls = _fake_noul(monkeypatch, [0.9, 0.1])
    verdicts = hooks.relevance_verdict(
        "What is the city?", ["The city is Riverside.", "Born 01/02/1990."],
    )
    assert verdicts == [True, False]
    cs = calls["calls"]
    assert len(cs) == 2, "one noul question per finding"
    assert cs[0][0] == "The city is Riverside."
    assert cs[0][1] == "This text is relevant to: What is the city?"
    assert cs[1][1] == "This text is relevant to: What is the city?"


def test_relevance_verdict_unavailable_is_none(monkeypatch):
    _fake_noul(monkeypatch, [None])
    assert hooks.relevance_verdict("q", ["a", "b"]) is None
    assert hooks.relevance_verdict("q", []) is None


def test_sufficiency_stops_on_answer(monkeypatch):
    _fake_noul(monkeypatch, [0.9])
    facts = ("[cycle 1] probe: What is the city?\n"
             "The applicant lives in Riverside, Springfield.")
    verdict = hooks.sufficiency_verdict("Where does the applicant live?", facts, "ev")
    assert verdict is not None and verdict.startswith("ANSWER: The applicant")


def test_sufficiency_keeps_probing_on_missing(monkeypatch):
    _fake_noul(monkeypatch, [0.1])
    assert hooks.sufficiency_verdict("goal", "facts", "evidence") == "MISSING:"


def test_sufficiency_unavailable_and_empty(monkeypatch):
    _fake_noul(monkeypatch, [None])
    assert hooks.sufficiency_verdict("goal", "facts", "ev") is None
    _fake_noul(monkeypatch, [0.9])
    assert hooks.sufficiency_verdict("", "facts", "ev") is None
    assert hooks.sufficiency_verdict("goal", "", "") is None


def test_candidate_skips_probe_headers():
    facts = ("[cycle 1] probe: What is the city?\n"
             "The applicant lives in Riverside, Springfield.")
    assert hooks._candidate_from(facts, "").startswith("The applicant")


def test_wrappers_fall_back_only_when_unavailable(monkeypatch):
    _fake_noul(monkeypatch, [None])
    assert hooks.relevance_or("q", ["a"], fallback=lambda: [False]) == [False]
    assert hooks.sufficiency_or("t", "f", "e", fallback=lambda: "LLM") == "LLM"

    _fake_noul(monkeypatch, [0.9])
    touched = []
    verdict = hooks.relevance_or(
        "q", ["a"], fallback=lambda: touched.append(1) or [False],
    )
    assert verdict == [True]
    assert touched == [], "Laya answered: the fallback must stay untouched"

    def _boom(state, instructions):
        raise RuntimeError("boom")

    monkeypatch.setattr(hooks, "_noul", _boom)
    assert hooks.sufficiency_or("t", "f", "e", fallback=lambda: "LLM") == "LLM"


def test_choose_option_needs_a_confident_winner(monkeypatch):
    """A choice pick must clear BOTH the floor and a margin over the runner-up,
    and the verdict must say whether the engine could ANSWER at all - a REFUSAL
    is not the same as "engine down": the caller asks the user on a refusal but
    keeps its own rail when the engine was never consulted."""
    _fake_noul(monkeypatch, [0.9, 0.1])
    assert hooks.choose_option("Which level?", "Fluent", ["B1", "C2"]) == (0, True)

    # No clear winner -> the engine ANSWERED and refused.
    _fake_noul(monkeypatch, [0.60, 0.55])
    assert hooks.choose_option("Which level?", "Fluent", ["B1", "C2"]) == (None, True)

    # Below the floor -> refused.
    _fake_noul(monkeypatch, [0.40, 0.30])
    assert hooks.choose_option("Which level?", "Fluent", ["B1", "C2"]) == (None, True)

    # Engine unavailable / empty inputs -> NOT consulted.
    _fake_noul(monkeypatch, [None])
    assert hooks.choose_option("Which level?", "Fluent", ["B1", "C2"]) == (None, False)
    assert hooks.choose_option("Which level?", "", ["B1"]) == (None, False)
    assert hooks.choose_option("Which level?", "Fluent", []) == (None, False)
