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


def test_the_answer_candidate_comes_from_a_finding_not_a_raw_chunk():
    """The ANSWER payload is quoted back as the sweep's answer, so it must come
    from a COMPOSED finding: the longest line of the pool was an
    ``evidence:``-tagged raw chunk and the verdict shipped "evidence:
    1154980148) · benitezrodrigo169@gmail.com · linkedin.com/in/rodrigo-..." -
    a truncated URL that then fails the engine's grounding check."""
    facts = ("[cycle 1] probe: what is the candidate's email?\n"
             "Key Finding: the candidate's email is a@b.com\n"
             "Support: a@b.com\n\n---\n\n"
             "evidence: +54 11 5498 0148 · a@b.com · linkedin.com/in/a-very-")
    assert hooks._candidate_from(facts, "") == (
        "the candidate's email is a@b.com")
    # No composed finding at all -> the evidence is the only fallback.
    assert hooks._candidate_from("evidence: raw chunk only", "a@b.com") == (
        "a@b.com")


def test_absence_is_a_verdict_not_a_word_list(monkeypatch):
    """'No evidence' is a MEANING, and a word list can only know the phrasings
    someone remembered to add - "do not contain" walked past a list holding
    does not / did not / is not, entered the fact pool and cut a sweep at cycle
    1.  Laya decides; one calibrated pass, no prose parsed."""
    _fake_noul(monkeypatch, [0.93])
    assert hooks.absence_verdict(
        "We couldn't locate that anywhere in the file.") is True

    _fake_noul(monkeypatch, [0.07])
    assert hooks.absence_verdict(
        "The source of the photo is https://example.com/p.jpg") is False

    # Engine down / nothing to judge -> UNDECIDED, never a guess.
    _fake_noul(monkeypatch, [None])
    assert hooks.absence_verdict("The email is a@b.com") is None
    assert hooks.absence_verdict("") is None


def test_absence_retries_then_fails_loudly(monkeypatch):
    """Laya ships with the app: a verdict it could not give is a FAULT, not a
    reason to fall back to a phrase list.  Each attempt relaunches the daemon
    and the run stops with an actionable error after the last one."""
    attempts = {"n": 0}

    def _noul(state, instructions):
        attempts["n"] += 1
        return None

    monkeypatch.setattr(hooks, "_noul", _noul)
    monkeypatch.setattr(hooks.time, "sleep", lambda s: None)
    try:
        hooks.absence_required("The answers do not contain the photo source.")
    except hooks.LayaUnavailable as exc:
        assert "after 3 attempts" in str(exc)
    else:
        raise AssertionError("an unreachable engine must raise, not decide")
    assert attempts["n"] == 3, "a fault is retried before it is raised"

    # ONE answered attempt is enough.
    _fake_noul(monkeypatch, [0.9])
    assert hooks.absence_required("The answers do not contain it.") is True


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


def test_decide_choice_reads_the_material_and_falls_back_to_the_negative(monkeypatch):
    """A choice belongs to the MATERIAL, not to the extractor's sentence.

    Live: twelve resume chunks that never mention the company, and the model
    answered "Sí" to "¿Ha trabajado anteriormente en PedidosYa?" - the answer
    happened to BE one of the options, so it was written.  Measured on the
    bundled encoder: material that holds nothing scores 0.13, material that
    holds the fact 0.83.  When it holds nothing, the NEGATIVE option is the
    answer ("no evidence -> no"), identified by meaning, not by a word list.
    """

    def _noul(state, instruction):
        if "with the option:" in instruction:
            option = instruction.rsplit("with the option:", 1)[1].strip()
            if state.startswith("material with the fact"):
                return {"Yes": 0.83, "No": 0.76}.get(option, 0.20)
            return {"Yes": 0.13, "No": 0.11, "PDF": 0.47, "DOCX": 0.15}.get(
                option, 0.10)
        if instruction == hooks._NEGATIVE_OPTION_STATEMENT:
            return {"Yes": 0.71, "No": 0.94, "PDF": 0.12, "DOCX": 0.09}.get(
                state, 0.0)
        return 0.0

    monkeypatch.setattr(hooks, "_noul", _noul)

    # 1. the material states it -> that option
    assert hooks.decide_choice("Worked at PedidosYa?",
                               "material with the fact", ["Yes", "No"]) == (
        0, "source")
    # 2. the material says nothing -> the negative option, by meaning
    assert hooks.decide_choice("¿Ha trabajado en PedidosYa?",
                               "material without it", ["Yes", "No"]) == (
        1, "negative")
    # 3. nothing supports any option and none reads as the negative -> NONE, so
    #    the caller asks the user instead of guessing (a 2-option list that is
    #    not a yes/no).
    assert hooks.decide_choice("Which format?", "material without it",
                               ["PDF", "DOCX"]) == (None, "nothing")
    # 4. too many options for one scoring pass -> let the caller keep its rail.
    many = ["o%d" % i for i in range(hooks._MAX_CHOICE_OPTIONS + 1)]
    assert hooks.decide_choice("Pick one", "material", many) == (None, "toolarge")
