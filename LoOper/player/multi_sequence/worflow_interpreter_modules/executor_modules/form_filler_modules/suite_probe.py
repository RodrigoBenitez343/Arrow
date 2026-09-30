"""Retrieval: head, ComoRAG hooks, facts vs verbatim."""

from .common import (
    _ff_drop_foreign_qa,
    _ff_finding_lines,
)
from ._test_support import (
    _Harness,
)


def test_foreign_question_answer_blocks_are_dropped_from_the_evidence():
    """A learned-answers source holds '## <question>' blocks, so retrieval for a
    similarly-worded question pulled another question's ANSWER in and the model
    answered from it (live: 'How many years of work experience do you have with
    Artificial Intelligence (AI)?' answered '4 years' - the value learned for
    Solution Architecture).  Only this field's OWN block survives."""
    label = ("How many years of work experience do you have with Artificial "
             "Intelligence (AI)?*")
    text = (
        "## How many years of work experience do you have with Solution "
        "Architecture?\n4\n\n"
        "## How many years of work experience do you have with Presales?\n0\n\n"
        "## How many years of work experience do you have with Artificial "
        "Intelligence (AI)?*\n6\n\n"
        "- Built production speech and translation systems.\n"
    )
    got = _ff_drop_foreign_qa(text, label)
    assert "Solution Architecture" not in got
    assert "Presales" not in got
    assert "\n6" in got                     # this field's own answer stays
    assert "Built production speech" in got
    # A source with no question blocks is untouched.
    assert _ff_drop_foreign_qa("## Summary\nEngineer", label) == (
        "## Summary\nEngineer")


def test_findings_without_support_or_restating_the_query_are_dropped():
    """The composer must return a claim PLUS a Support line.  A claim that just
    restates the retrieval query, and a block with no Support at all, are
    unusable.  What the Support TEXT reads like is the relevance verdict's and
    the model's call - a phrase list is a language, not a rule."""
    from .probe import _ff_clean_findings
    query = "Where is the headquarters of the company located?"
    out = (
        "Key Finding: " + query + "\n"
        "Support: The location of the headquarters, worldwide.\n"
        "Key Finding: The desired salary is USD 4000 or more per month.\n"
        "Support: (This does not directly mention the salary.) Wait, there is "
        "no direct information; based on common knowledge it can be inferred.\n"
        "Key Finding: The candidate lives in Springfield.\n"
        "Support: I am based in Springfield (UTC-5), work remotely.\n"
        "Key Finding: A claim with no support line at all.\n"
    )
    got = _ff_clean_findings(out, query)
    assert "Springfield" in got
    assert "no support line" not in got
    assert "headquarters" not in got      # the query restated, not a finding
    assert got.count("Key Finding:") == 2  # ...the salary block is a finding now


def test_findings_the_model_markdown_bolded_are_kept():
    """This small model emits markdown-bolded labels ("**Key Finding:**",
    "**Support:**") - the shape that made the composer's output read as a
    "responder produced nothing" on a live run (0 composed facts on 2 of 3
    fields) because a bare startswith("key finding") matched nothing.  The
    parser now accepts the SAME shapes the engine's own parser does."""
    from .probe import _ff_clean_findings
    out = (
        "**Key Finding 1:** The candidate has hands-on experience with AWS "
        "Transcribe.\n"
        "**Support:** Multilingual transcription platform built on AWS "
        "Transcribe plus custom per-language ML models.\n"
        "## Key Findings\n"
        "Key Finding: The desired salary is USD 4500.\n"
        "Support: Desired salary: USD 4500 or above.\n"
    )
    got = _ff_clean_findings(out, "experience with AWS and DataOps tooling")
    assert "AWS Transcribe" in got
    assert "USD 4500" in got
    # A bolded claim with NO support line is still dropped.
    assert _ff_clean_findings(
        "**Key Finding:** nothing backs this up.\n", "") == ""


def test_fact_responder_composes_findings_and_only_fails_on_empty():
    """The responder must COMPOSE a finding (so the cycle counts) - a
    respond-or-decline design stalled the fact loop and made the ComoRAG
    consolidation look bypassed."""
    h = _Harness()
    cfg = h._ff_cfg({"instruction": "fill"})
    seen = {}

    def _llm(prompt, system, c, f):
        seen["prompt"] = prompt
        return "Key Finding: 5 years of Python.\nSupport: ..."

    h._ff_llm_call = _llm
    fact = h._ff_fact_answer("Experience", "Skills", "ev text", cfg, None)
    assert fact.startswith("Key Finding: 5 years")
    # Both the cycle probe and the field goal reach the model.
    assert "Skills" in seen["prompt"]
    assert "Experience" in seen["prompt"]
    # Only a truly empty reply returns None.
    h._ff_llm_call = lambda *a, **k: ""
    assert h._ff_fact_answer("Experience", "Skills", "ev", cfg, None) is None
    h._ff_llm_call = lambda *a, **k: "NONE"
    assert h._ff_fact_answer("Experience", "Skills", "ev", cfg, None) is None


def test_multi_cycle_probe_actually_drives_the_consolidator(monkeypatch):
    """probe_cycles > 1 must build the ComoRAG consolidator wired with the SAME
    hooks the LLM node uses (probe generator + responder + judge) and hand the
    extractor the composed facts AND the verbatim excerpts they came from."""
    import AI.comorag_engine as ce

    # Pin the embedded Laya engine as unavailable: this test drives the HOOKS
    # (wiring + the LLM fallback verdict), and with the engine shipped in-tree
    # a live noul call would answer for real.
    import AI.laya_client as lc
    monkeypatch.setattr(lc, "noul", lambda state, instructions: None)

    captured = {}

    class _FakeConsolidator:
        def __init__(self, **kw):
            captured["init"] = kw

        def consolidate(self, **kw):
            captured["call"] = kw
            # The engine invokes each hook once per cycle.
            captured["plan"] = kw["intent_planner"]("intent")
            captured["finding"] = kw["responder"]("Skills", "evidence slice")
            captured["probe"] = kw["probe_generator"]("intent", "facts")
            captured["synth"] = kw["response_generator"](
                "intent", "facts", "evidence")
            captured["verdict"] = kw["judge"]("goal", "facts", "evidence")
            return "Key Finding: X"

        @property
        def facts_pool(self):
            return ["Key Finding: X"]

        @property
        def evidence_pool(self):
            return ["RAW CHUNK TEXT"]

    monkeypatch.setattr(ce, "ProbeBasedConsolidator", _FakeConsolidator)

    def _llm(prompt, system, c, f):
        if "Question about '" in prompt:
            return "What programming languages does the candidate use?"
        if "Verdict:" in prompt:
            return "ANSWER: Python"
        return "Key Finding: X"

    h = _Harness()
    cfg = h._ff_cfg({"instruction": "fill", "probe_cycles": 3})
    h._ff_llm_call = _llm
    out = h._ff_probe_consolidate("Experience", "Experience", cfg,
                                  "upstream text", [])
    # The verbatim GROUND TRUTH leads and the paraphrased facts follow: a
    # paraphrase can INVENT an exact value (the fact pool held a mangled
    # LinkedIn URL where the source had the real one), so the excerpts must
    # never come second.
    assert "[Verbatim excerpts from the source documents" in out
    assert "RAW CHUNK TEXT" in out
    assert "[Distilled facts" in out
    assert "Key Finding: X" in out
    assert out.index("[Verbatim excerpts") < out.index("[Distilled facts")
    # The composed answer is a PARAPHRASE: it rides LAST so it can never
    # override the verbatim excerpts on an exact value (the invented-URL bug).
    assert "[Composed answer for this field" in out
    assert out.index("[Distilled facts") < out.index("[Composed answer")
    # The caller's stop flag rides into the engine (ESC must end a running
    # consolidation, not be ignored until the node finishes).
    assert "stop_flag" in captured["call"]
    assert captured["init"]["max_probes"] == 3
    assert captured["init"]["min_facts"] == 1
    # Chunking is MEASURED-small and the retrieval window has a floor: the
    # value chunk (contact block) ranks 2nd-3rd, so a 1-chunk setting could
    # never ground a field - the knob may raise top_k, never starve it.
    assert captured["init"]["chunk_size"] == 250
    assert captured["init"]["top_k"] >= 3
    # The SAME five hooks the LLM node wires - the intent planner and the
    # final synthesis were the two the form filler was missing.
    for hook in ("intent_planner", "probe_generator", "responder",
                 "response_generator", "judge"):
        assert callable(captured["call"][hook]), hook
    assert captured["plan"]
    assert captured["synth"]
    assert captured["call"]["context_sources"]
    # The GOAL is a task sentence, NOT the bare label: the engine's anti-echo
    # guard rejects a probe that rephrases the goal, and for a form field the
    # label IS the right query - passing it bare burned 4 critique-retries
    # ("reflecting") and then probed a random section.
    goal = captured["call"]["prompt"]
    assert "Experience" in goal and goal != "Experience"
    assert goal != h._ff_cfg({})["instruction"]
    # The generator yields a QUESTION for the next cycle; the judge a verdict.
    assert captured["probe"] == (
        "What programming languages does the candidate use?"
    )
    assert captured["verdict"].startswith("ANSWER:")


def test_raw_head_is_a_fallback_not_shipped_with_facts(monkeypatch, caplog):
    """The raw document head must NOT ride along with composed facts - it is a
    ~1500-char block unrelated to most questions (it poisoned the window).  It
    stays a fallback crutch for the single-pass route."""
    import AI.comorag_engine as ce
    from player.multi_sequence.llm_executor_resources import rag_utils as ru

    class _Fake:
        def __init__(self, **kw):
            pass

        def consolidate(self, **kw):
            return "Key Finding: Python\n\nevidence: RAW SLICE"

        @property
        def facts_pool(self):
            return ["Key Finding: Python"]

        @property
        def evidence_pool(self):
            return ["RAW SLICE"]

    monkeypatch.setattr(ce, "ProbeBasedConsolidator", _Fake)
    monkeypatch.setattr(ru, "build_rag_context_from_docs",
                        lambda **kw: "RAW SLICE")
    monkeypatch.setattr(ru, "build_rag_context", lambda **kw: ("", None, {}))
    monkeypatch.setattr(_Harness, "_ff_doc_texts",
                        lambda self, docs: ["RESUME HEAD"])

    h = _Harness()
    h._ff_llm_call = lambda *a, **k: "Key Finding: Python"

    caplog.set_level("INFO")
    on = h._ff_cfg({"instruction": "fill", "probe_cycles": 3,
                    "consolidate": True})
    out = h._ff_probe("Full name", on, "", ["resume.md"])
    assert "Key Finding: Python" in out
    assert "RAW SLICE" in out
    assert "RESUME HEAD" not in out
    # The route is a field-table row now (the plain one-liner is gone).
    assert h._ff_last_route.startswith("comoRAG")
    assert h._ff_last_facts == 1 and h._ff_last_excerpts == 1

    caplog.clear()
    off = h._ff_cfg({"instruction": "fill", "probe_cycles": 3,
                     "consolidate": False})
    raw = h._ff_probe("Full name", off, "", ["resume.md"])
    assert "RESUME HEAD" in raw and "RAW SLICE" in raw
    # An off toggle must never be invisible again.
    assert h._ff_last_route.startswith("single-pass")


def test_doc_head_is_skipped_when_the_slice_already_contains_it(monkeypatch):
    """The head is a fallback crutch for short labels; when the retrieved slice
    IS the header chunk, prepending it too handed the model the same header
    TWICE - the duplicated evidence users saw on every field."""
    from player.multi_sequence.llm_executor_resources import rag_utils as ru

    monkeypatch.setattr(ru, "build_rag_context", lambda **kw: ("", None, {}))
    monkeypatch.setattr(_Harness, "_ff_doc_texts",
                        lambda self, docs: ["# Alex David Doe\nhead line"])
    h = _Harness()
    off = h._ff_cfg({"instruction": "fill", "consolidate": False})

    # (a) the slice already carries the header -> no separate head block
    monkeypatch.setattr(
        ru, "build_rag_context_from_docs",
        lambda **kw: "SLICE\n# Alex David Doe\nhead line\ntail")
    raw = h._ff_probe("Full name", off, "", ["resume.md"])
    assert "[Source document start]" not in raw
    assert raw.count("# Alex David Doe") == 1

    # (b) the slice is unrelated -> the head still rides along (its purpose)
    monkeypatch.setattr(ru, "build_rag_context_from_docs",
                        lambda **kw: "SLICE ABOUT SOMETHING ELSE")
    raw2 = h._ff_probe("Full name", off, "", ["resume.md"])
    assert "[Source document start]" in raw2
    assert "# Alex David Doe" in raw2


def test_consolidation_toggle_gates_the_comorag_engine(monkeypatch):
    """ComoRAG is ON by default but togglable: with the toggle OFF the agent
    falls back to one single-pass retrieval (lighter, for a bigger model)."""
    import AI.comorag_engine as ce
    from player.multi_sequence.llm_executor_resources import rag_utils as ru

    built = {"n": 0}

    class _Fake:
        def __init__(self, **kw):
            built["n"] += 1

        def consolidate(self, **kw):
            return "fact"

        @property
        def facts_pool(self):
            return ["fact"]

        @property
        def evidence_pool(self):
            return []

    monkeypatch.setattr(ce, "ProbeBasedConsolidator", _Fake)
    monkeypatch.setattr(ru, "build_rag_context_from_docs", lambda **kw: "single")
    monkeypatch.setattr(ru, "build_rag_context", lambda **kw: ("", None, {}))

    h = _Harness()
    h._ff_llm_call = lambda *a, **k: "Key Finding: x"

    on = h._ff_cfg({"instruction": "fill", "probe_cycles": 3,
                    "consolidate": True})
    assert "fact" in h._ff_probe("Field", on, "upstream", ["doc.txt"])
    assert built["n"] == 1

    built["n"] = 0
    off = h._ff_cfg({"instruction": "fill", "probe_cycles": 3,
                     "consolidate": False})
    out = h._ff_probe("Field", off, "upstream", ["doc.txt"])
    assert built["n"] == 0        # the ComoRAG engine is never built...
    assert "single" in out        # ...single-pass retrieval is used instead


def test_findings_ship_without_their_support_quotes():
    """A finding's Support line is a VERBATIM copy of the excerpts shipped
    beside it, so shipping both printed the same sentences twice - the
    duplicated evidence in the field block - and burned window."""
    entries = [
        "[cycle 1] probe: what is the URL?\nKey Finding: the URL is X\n"
        "Support: verbatim sentence One\n",
        "Key Finding: the candidate speaks Python\n"
        "Support: verbatim sentence Two\n",
        "Support: only a quote, no finding\n",
    ]
    out = _ff_finding_lines(entries)
    assert out == ["Key Finding: the URL is X",
                   "Key Finding: the candidate speaks Python"]
    assert all("verbatim sentence" not in t for t in out)
    assert _ff_finding_lines([]) == []
    # A finding the composer re-emitted in a later cycle ships ONCE.
    repeated = [
        "[cycle 1] probe: q\nKey Finding: the candidate speaks Python\n",
        "[cycle 2] probe: q2\nKey Finding: The candidate speaks python!\n",
    ]
    assert _ff_finding_lines(repeated) == [
        "Key Finding: the candidate speaks Python"]


def test_repair_hint_is_folded_into_the_probe(monkeypatch):
    import AI.comorag_engine as ce

    captured = {}

    class _Fake:
        def __init__(self, **kw):
            pass

        def consolidate(self, **kw):
            captured.update(kw)
            return "fact"

        @property
        def facts_pool(self):
            return ["fact"]

        @property
        def evidence_pool(self):
            return []

    monkeypatch.setattr(ce, "ProbeBasedConsolidator", _Fake)
    h = _Harness()
    cfg = h._ff_cfg({"instruction": "fill", "probe_cycles": 3})
    h._ff_llm_call = lambda *a, **k: "Key Finding: x"
    out = h._ff_probe("Start date", cfg, "upstream text", [], None,
                      repair_hint="it must be a date")
    assert out
    assert "it must be a date" in captured["prompt"]
    assert "Start date" in captured["prompt"]


def test_fact_findings_target_the_field_not_the_section():
    """Facts must be composed toward the FIELD - unrelated section content is
    what poisoned the pool."""
    h = _Harness()
    cfg = h._ff_cfg({"instruction": "fill"})
    seen = {}

    def _llm(prompt, system, c, f):
        seen["prompt"] = prompt
        seen["system"] = system
        return "Key Finding: x"

    h._ff_llm_call = _llm
    h._ff_fact_answer("Start date", "Education", "ev", cfg, None)
    assert "Start date" in seen["prompt"]      # the field drives the findings
    assert "Education" in seen["prompt"]       # the section is only the route
    assert "DISCARD" in seen["prompt"]         # anti-poisoning instruction
    assert "form field" in seen["system"].lower()
