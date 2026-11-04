"""Retrieval through CRAG: verdict, verbatim strips, correction, fallbacks."""

import os

from AI.crag import RetrievalEvaluator
from AI.crag.evaluator import cache as score_cache

from ._test_support import (
    _Harness,
    _node,
)


class _FakeEvaluator(RetrievalEvaluator):
    """Scores a passage from a lookup table; records what it was asked."""

    name = "fake"
    namespace = "fake-suite"

    def __init__(self, table, default=-1.0):
        super().__init__(cache_enabled=True)
        self.table = dict(table)
        self.default = default
        self.queries = []

    def _score_one(self, query, text):
        if query not in self.queries:
            self.queries.append(query)
        for key, value in self.table.items():
            if key in text:
                return value
        return self.default


def _harness(table=None, default=-1.0, pool=None):
    score_cache().clear()
    h = _Harness()
    h.ev = _FakeEvaluator(table or {}, default)
    h._ff_crag_evaluator = lambda cfg, sf: h.ev
    h.queries = []
    h.pool = DOC if pool is None else pool
    return h


def _cfg(h, **overrides):
    data = {"instruction": "fill the form"}
    data.update(overrides)
    return h._ff_cfg(data)


DOC = (
    "# Rodrigo Benitez\nEmail: benitezrodrigo169@gmail.com\n"
    "Phone: +54 11 5498-0148\nLocation: Buenos Aires, Argentina\n\n"
    "## Experience\nBuilt internal Python automations and reporting scripts "
    "for a language services company over five years, then shipped "
    "local-first LLM tooling for international clients as a freelancer.\n\n"
    "## Education\nSoftware Engineering, self taught, learned by shipping "
    "production systems rather than by coursework.\n"
)


def test_a_correct_verdict_keeps_the_verbatim_strip(tmp_path):
    """The extractor gets SOURCE TEXT, not a paraphrase: CRAG's refinement
    keeps strips, so an exact email survives character-for-character."""
    h = _harness({"benitezrodrigo169@gmail.com": 0.92})
    cfg = _cfg(h)
    got = h._ff_retrieve("Email address*", cfg, h.pool, [])
    assert "benitezrodrigo169@gmail.com" in got
    assert got.startswith("[Retrieved knowledge")
    assert h._ff_last_action == "correct"
    assert h._ff_last_route.startswith("CRAG: correct")
    assert h._ff_last_kept >= 1


def test_an_incorrect_verdict_with_nothing_external_returns_nothing():
    """A wrong retrieval must NOT be answered from: the pack is empty, so the
    field takes the existing "nothing retrieved" path (ask the user) instead
    of being filled from material the evaluator rejected."""
    h = _harness(default=0.05)
    cfg = _cfg(h, crag_external=False)
    got = h._ff_retrieve("Passport number*", cfg, h.pool, [])
    assert got == ""
    assert h._ff_last_action == "incorrect"
    assert "CRAG: incorrect" in h._ff_last_route


def test_an_ambiguous_verdict_combines_internal_and_external_knowledge():
    """Upstream's flag 1 writes 'Knowledge1: <refined> [sep] Knowledge2:
    <external>'.  The external store here is the pool's own learned stream."""
    pool = (
        "# Resume\nLocation: Buenos Aires, Argentina\n\n"
        "[[FORM_VERIFIED]]\n## Earliest start date\n"
        "Available from the first of April\n[[/FORM_VERIFIED]]\n"
    )
    h = _harness({"Buenos Aires": 0.50,
                  "springframework": 0.0}, default=0.05, pool=pool)
    cfg = _cfg(h, crag_external=True, crag_rewrite=True)
    h._ff_llm_call = lambda prompt, system, c, sf: "start date, availability"
    got = h._ff_retrieve("Earliest start date*", cfg, h.pool, [])
    assert "Knowledge1:" in got
    assert "Knowledge2:" in got


def test_the_head_block_is_always_a_candidate(tmp_path):
    """A short label ('First name', 'City') embeds poorly, so the contact block
    can rank below topical segments.  It is force-included, and the corpus is
    SEGMENTS (one value per line) - the unit this evaluator is calibrated on."""
    doc = tmp_path / "resume.txt"
    doc.write_text(DOC, encoding="utf-8")
    h = _harness({"Email": 0.9})
    cfg = _cfg(h)
    entries = h._ff_crag_corpus(cfg, "", [str(doc)])
    assert len(entries) > 2
    segments = h._ff_crag_candidates("First name", entries, cfg)
    assert segments
    assert segments[0]["text"].startswith("Rodrigo Benitez")
    # Every candidate is ONE value-bearing segment, never a 250-char window:
    # the evaluator's score collapses once unrelated text follows the value.
    assert all(len(s["text"]) <= 200 for s in segments)


def test_a_repair_run_retrieves_for_the_required_format():
    """A repair keeps the field's question and adds the format the page
    demands; the formatter model is what the retrieved strip must satisfy."""
    h = _harness({"May 2023": 0.8})
    cfg = _cfg(h)
    h._ff_retrieve("Start date*", cfg, h.pool, [], repair_hint="MM/YYYY")
    assert h.ev.queries
    assert "MM/YYYY" in h.ev.queries[0]
    assert "Start date" in h.ev.queries[0]


def test_a_field_is_scored_once_per_pass():
    """The field loop asks for the same label twice (fill + verify); the
    pipeline must not pay a second scored pass for it."""
    h = _harness({"benitezrodrigo169@gmail.com": 0.9})
    cfg = _cfg(h)
    h._ff_retrieve("Email address*", cfg, h.pool, [])
    forwards = h.ev.forwards
    again = h._ff_retrieve("Email address*", cfg, h.pool, [])
    assert h.ev.forwards == forwards
    assert "benitezrodrigo169@gmail.com" in again


def test_the_learned_answer_leads_the_pack():
    """The user's own answer for THIS field outranks retrieved material: they
    chose the wording, and the extractor may only adapt it."""
    h = _harness({"benitezrodrigo169@gmail.com": 0.9})
    cfg = _cfg(h)
    got = h._ff_retrieve("Email address*", cfg, h.pool, [],
                         learned_hint="work@example.com")
    assert got.startswith("[LEARNED")
    assert "benitezrodrigo169@gmail.com" in got


def test_an_esc_press_aborts_before_scoring():
    h = _harness({"email": 0.9})
    cfg = _cfg(h)
    h._ff_abort = True
    assert h._ff_retrieve("Email*", cfg, h.pool, [], stop_flag=lambda: True) == ""
    assert h.ev.forwards == 0


def test_the_engine_falls_back_to_embeddings_when_laya_is_down(monkeypatch):
    """No probe loop to fall back to: the scored pass survives on embeddings,
    and only if those fail too does retrieval go plain single-pass."""
    from AI import laya_client
    from AI.crag import EmbeddingEvaluator, make_evaluator

    monkeypatch.setattr(laya_client, "available", lambda: False)
    monkeypatch.setattr(laya_client, "ensure_running", lambda: False)
    evaluator = make_evaluator("auto",
                               embed=lambda texts: [[1.0, 0.0]] * len(texts))
    assert isinstance(evaluator, EmbeddingEvaluator)
    assert evaluator.score("q", "a passage") == 1.0


def test_the_form_filler_no_longer_imports_the_comorag_engine():
    """The probe consolidation is GONE, not merely unused: no module of the
    form filler may reach for the ComoRAG engine again."""
    here = os.path.dirname(os.path.abspath(__file__))
    offenders = []
    markers = ("comorag_engine", "ProbeBasedConsolidator",
               "import comorag", "from AI.comorag")
    for name in sorted(os.listdir(here)):
        if not name.endswith(".py") or name.startswith("suite_"):
            continue
        with open(os.path.join(here, name), "r", encoding="utf-8") as f:
            body = f.read()
        if any(marker in body for marker in markers):
            offenders.append(name)
    assert offenders == []


def test_the_node_fills_a_field_from_a_real_document_through_crag(tmp_path):
    """End to end through the node loop: a real document is chunked, the
    evaluator scores it, CRAG keeps the verbatim strip and the extractor writes
    the value.  Nothing here is stubbed except the engine calls."""
    doc = tmp_path / "resume.md"
    doc.write_text(DOC, encoding="utf-8")
    h = _harness({"benitezrodrigo169@gmail.com": 0.93}, default=0.05)
    # No embeddings endpoint in a test: the candidate ranking falls back to
    # lexical overlap, which is what a broken embedding server does in the app.
    h._ff_embed_call = lambda cfg: (lambda texts: [])
    # The pool the node is wired to (a Context node serves it at runtime).
    h._ff_gather_source_text = lambda node: doc.read_text(encoding="utf-8")
    h._ff_llm_call = lambda prompt, system, cfg, sf: (
        "benitezrodrigo169@gmail.com")
    h._ff_enumerate_web = lambda cfg, sf: [
        {"id": "f0", "label": "Email address", "tag": "input",
         "kind": "text"}]
    h._ff_enumerate_desktop = h._ff_enumerate_web
    writes = []
    h._ff_write_web = lambda field, val, sf, driver=None: (
        writes.append((field.get("id"), val)) or True)
    h._ff_write_desktop = h._ff_write_web
    h._ff_read_web = lambda field, sf, driver=None: "benitezrodrigo169@gmail.com"
    h._ff_read_desktop = h._ff_read_web
    h._ff_current_value = lambda field, sf, driver=None, **kw: ""
    h._ff_field_state = lambda field, sf, driver=None: {
        "found": True, "valid": True, "message": ""}
    h._ff_mark_processing = lambda *a, **k: None
    h._ff_enable_overlay = lambda *a, **k: None

    h._execute_form_filling_node(
        _node(max_fields=1, crag_evaluator="embed"), None)

    assert writes == [("f0", "benitezrodrigo169@gmail.com")]
    assert h._ff_last_action == "correct"
    assert h._ff_last_route.startswith("CRAG: correct")


HEADER_DOC = (
    "# Rodrigo David Benitez\n\n"
    "**Forward Deployed AI Engineer | Applied AI/ML (GenAI, RAG, Local/Edge "
    "Inference) | Enterprise & Regulated Environments**\n\n"
    "Buenos Aires, Argentina \u00b7 +54 11 5498 0148 \u00b7 "
    "benitezrodrigo169@gmail.com\n\n"
    "## Professional Experience\n\n"
    "### IT Specialist & Developer \u2014 Homeland Language Services\n"
    "Oxnard, California, USA \u00b7 Remote \u00b7 Contract \u00b7 May 2021 \u2013 "
    "Jun 2022\n"
)


def test_the_retriever_holds_no_field_vocabulary():
    """What a question wants is SCORED by the engine, not looked up in a word
    list: a form in another language, or in a domain nobody wrote keywords
    for, has to read the same way as a job application."""
    from . import crag

    for name in ("_FF_CLASS_WORDS", "_FF_CLASS_PROBES", "_FF_SHAPE_TESTS",
                 "_ff_field_class", "_ff_value_shape"):
        assert not hasattr(crag, name), name


def test_the_probes_are_the_question_and_the_controls_own_words():
    """One-word labels embed poorly, so the control's own words ride along.
    Nothing else is invented: the retriever contributes no vocabulary."""
    from .crag import _ff_crag_probes

    probes = _ff_crag_probes("Location (city)*",
                             {"label": "Location (city)*", "kind": "combo",
                              "placeholder": "Start typing a city"})
    assert probes[0].startswith("Location")
    assert any("Start typing a city" in p for p in probes)
    assert not any("residence" in p for p in probes)
    assert 1 < len(probes) <= 2


def test_the_subject_lines_follow_the_score_not_the_document_order(tmp_path):
    """The header is a run of taglines BEFORE the contact line, so document
    order handed 'Location (city)' the taglines.  The engine's own answer score
    puts the line that answers the question first instead."""
    doc = tmp_path / "resume.md"
    doc.write_text(HEADER_DOC, encoding="utf-8")
    h = _harness({})
    h._ff_answer_scores = lambda question, texts: [
        0.9 if "Buenos Aires" in t else 0.05 for t in texts]
    cfg = _cfg(h)
    entries = h._ff_crag_corpus(cfg, "", [str(doc)])
    cand = h._ff_crag_candidates(
        "Location (city)*", entries, cfg,
        field={"id": "f", "label": "Location (city)*", "kind": "combo"})
    assert cand[0]["text"].startswith("Buenos Aires"), \
        "the line the engine scores as the answer takes the first slot"
    assert not any("Oxnard" in c["text"] for c in cand), \
        "a job-history city is not the candidate's own"


def test_a_single_valued_field_stays_inside_one_section(tmp_path):
    """A candidate set that mixes the header with an employer's rows is what let
    the extractor glue 'Buenos Aires' to 'Saint Paul'.  One value = one section;
    the header keeps its slots, which is where a name/phone/email/city lives."""
    doc = tmp_path / "resume.md"
    doc.write_text(HEADER_DOC, encoding="utf-8")
    h = _harness({})
    cfg = _cfg(h)
    entries = h._ff_crag_corpus(cfg, "", [str(doc)])
    cand = h._ff_crag_candidates(
        "Current employer", entries, cfg,
        field={"id": "f", "label": "Current employer", "kind": "text"})
    body_sections = {c["section"] for c in cand if not c.get("head")}
    assert len(body_sections) <= 1, "one single value, one section of evidence"


def test_a_company_question_reads_a_body_section_not_the_header(tmp_path):
    """When the subject block does not answer the question, the header cannot be
    its answer: the single-section rule locked such a field to the taglines and
    the employer became unreachable (live: verdict incorrect, 0 chars of
    evidence, on a resume whose first job row names it)."""
    doc = tmp_path / "resume.md"
    doc.write_text(HEADER_DOC, encoding="utf-8")
    h = _harness({"Homeland": 0.9})
    # No line of the header answers 'Current company' - the engine says so.
    h._ff_answer_scores = lambda question, texts: [0.02] * len(texts)
    cfg = _cfg(h)
    entries = h._ff_crag_corpus(cfg, "", [str(doc)])
    cand = h._ff_crag_candidates(
        "Current company", entries, cfg,
        field={"id": "f", "label": "Current company", "kind": "text"})
    body = [c for c in cand if not c.get("head")]
    assert any("Homeland" in (c.get("section") or "") for c in body), \
        "the employer's own section must be a candidate"


def test_a_multiline_field_keeps_the_broad_sweep(tmp_path):
    """A textarea asks for prose, not one value: the one-section rule would cut
    the material a summary field needs."""
    doc = tmp_path / "resume.md"
    doc.write_text(HEADER_DOC, encoding="utf-8")
    h = _harness({})
    cfg = _cfg(h)
    entries = h._ff_crag_corpus(cfg, "", [str(doc)])
    cand = h._ff_crag_candidates(
        "Tell us about your experience", entries, cfg,
        field={"id": "f", "label": "Tell us about your experience",
               "kind": "textarea", "multiline": True})
    body = [c for c in cand if not c.get("head")]
    assert body, "the header cannot be the whole answer to a prose question"


def test_the_node_config_reads_the_crag_knobs():
    h = _Harness()
    cfg = h._ff_cfg(dict(_node()["data"],
                         crag_top_k=4, crag_decompose_mode="selection",
                         crag_upper_threshold=0.75, crag_lower_threshold=0.2,
                         crag_evaluator="embed"))
    assert cfg["crag_top_k"] == 4
    assert cfg["crag_decompose_mode"] == "selection"
    assert cfg["crag_upper_threshold"] == 0.75
    assert cfg["crag_lower_threshold"] == 0.2
    assert cfg["crag_evaluator"] == "embed"
    assert cfg["crag_enabled"] is True


def test_the_consolidation_ceiling_comes_from_the_node_config():
    """A CEILING on sweeps, not a quota: two by default, and the chain may
    raise it."""
    h = _Harness()
    assert h._ff_cfg({"instruction": "x"})["crag_cycles"] == 2
    assert h._ff_cfg({"instruction": "x", "crag_cycles": "3"})[
        "crag_cycles"] == 3


def test_a_correct_sweep_ends_the_consolidation():
    """The loop stops the moment a sweep is `correct`: the extra cycles exist
    for the fields the first sweep could not answer, and a field it DID answer
    must not pay for them."""
    h = _harness({"benitezrodrigo169@gmail.com": 0.92})
    cfg = _cfg(h)
    cycles = []
    real = h._ff_crag_cycle

    def spy(*args, **kwargs):
        cycles.append(kwargs.get("cycle"))
        return real(*args, **kwargs)
    h._ff_crag_cycle = spy

    pack = h._ff_retrieve("Email address*", cfg, h.pool, [])
    assert "benitezrodrigo169@gmail.com" in pack
    assert len(cycles) == 1, "an answered field costs ONE sweep"


BIG_DOC = "\n".join(
    ["# Person Name", "City, Country · +00 0000 0000"]
    + ["Fact line number %d about something unrelated \u2014 detail %d"
       % (i, i) for i in range(1, 19)])


def test_a_sweep_that_finds_nothing_goes_into_unscored_material(tmp_path):
    """Consolidation: after a sweep finds nothing, the next one is handed what
    the first already scored, so its budget lands on the part of the document
    nothing has looked at yet - and no segment is scored twice."""
    doc = tmp_path / "big.md"
    doc.write_text(BIG_DOC, encoding="utf-8")
    h = _harness(default=0.05)          # nothing ever answers
    cfg = _cfg(h, crag_cycles=2, crag_external=False)
    rounds = []
    real = h._ff_crag_candidates

    def spy(query, entries, c, field=None, skip=None):
        rows = real(query, entries, c, field=field, skip=skip)
        if skip is not None:
            rounds.append([e["text"] for e in rows])
        return rows
    h._ff_crag_candidates = spy

    h._ff_retrieve("Passport number*", cfg, "", [str(doc)])
    assert len(rounds) >= 2, "a sweep that found nothing must cycle"
    assert rounds[0] and rounds[1], "both sweeps must have material"
    assert not (set(rounds[0]) & set(rounds[1])), \
        "the second sweep must not re-score the first one's segments"


def test_the_best_sweep_wins_and_is_never_merged(tmp_path):
    """Two sweeps are two answers, not one: the pack comes from the sweep that
    scored highest, so a later sweep can never add its strips to an earlier
    one's (that merge is what let two sections answer one field)."""
    doc = tmp_path / "big.md"
    doc.write_text(BIG_DOC, encoding="utf-8")
    # Cycle 1 scores nothing on topic; the SECOND sweep's section does.
    h = _harness({"Fact line number 12": 0.9}, default=0.05)
    cfg = _cfg(h, crag_cycles=2, crag_external=False)
    pack = h._ff_retrieve("Passport number*", cfg, "", [str(doc)])
    assert "Fact line number 12" in pack
    assert h._ff_last_action == "correct"
