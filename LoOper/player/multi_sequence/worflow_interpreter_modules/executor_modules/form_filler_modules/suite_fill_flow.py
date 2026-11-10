"""The node loop: fill, verify, skip, abort, tables."""

import json

from ._test_support import (
    _Harness,
    _node,
    _wire,
)


def test_the_source_is_read_from_the_ctx_in_port_not_the_exec_variable():
    """The source must travel over the ctx_in DATA PORT (the Context pool's
    ctx_out the node was wired to), not the exec-edge variables - the port
    value wins when present, the variables are only the fallback."""
    h = _Harness()

    class _PS:
        def get_output(self, cid, nid, port):
            return "FROM-PORT" if (nid, port) == ("ctx1", "ctx_out") else None

    h.port_store = _PS()
    h.set_variable("node_ctx1_output", "FROM-VARIABLE")
    node = {"inputs": [{"from_node": "ctx1", "input_port": "ctx_in",
                        "output_type": "ctx_out"}]}
    assert h._ff_gather_source_text(node) == "FROM-PORT"

    # No port value -> the exec-edge variables are still the fallback.
    h.port_store = type("PS", (), {"get_output": lambda *a: None})()
    assert h._ff_gather_source_text(node) == "FROM-VARIABLE"


def test_the_source_is_pulled_from_a_passive_context_node():
    """A Context node is PASSIVE: it never runs in the exec graph (the main
    loop even marks it skipped as unreachable because its edges are data-only),
    so the form filler must PULL its served material on demand via `_ctx_serve`
    instead of waiting for an execution that never happens.  Live: the source
    read 0 chars, so every field was asked."""
    h = _Harness()
    h.workflow_graph = {"ctx1": {"type": "context", "data": {}, "inputs": []}}
    served = []
    h._ctx_serve = lambda nid, stop_flag=None: (served.append(nid),
                                                "RESUME TEXT")[1]
    node = {"inputs": [{"from_node": "ctx1", "input_port": "ctx_in",
                        "output_type": "ctx_out"}]}
    assert h._ff_gather_source_text(node) == "RESUME TEXT"
    assert served == ["ctx1"]


def test_a_user_verified_answer_is_authoritative(monkeypatch):
    """A hand-corrected answer is never put to the engine that was wrong.

    Live: the user fixed a learned answer, Laya's verdict called it a misfit,
    the record was re-derived and the corrected field came back wrong on the
    next run.  An AUTO record the source cannot show is dropped outright - the
    engine cannot ground what is not there - while a SHORT record ('Yes'),
    which the source can neither prove nor disprove, is still its call."""
    from AI import laya_hooks

    h = _Harness()
    cfg = h._ff_cfg({"instruction": "fill"})
    calls = {"n": 0}

    def _verdict(*a, **k):
        calls["n"] += 1
        return False

    monkeypatch.setattr(laya_hooks, "value_accuracy_or", _verdict)

    h._ff_last_match_conf = 0.9
    assert h._ff_reuse_confidence("English level*", "Intermedio", cfg, "ctx",
                                  manual=True) == 1.0
    assert calls["n"] == 0            # no engine verdict on a verified answer

    h._ff_last_match_conf = 0.9
    assert h._ff_reuse_confidence("English level*", "Intermedio", cfg,
                                  "ctx") == 0.0
    assert calls["n"] == 0            # nowhere in the source: not even asked

    h._ff_last_match_conf = 0.9
    assert h._ff_reuse_confidence("Authorized to work?", "Yes", cfg,
                                  "RESUME TEXT") == 0.0
    assert calls["n"] == 1            # an AUTO short record is still revisable


def test_a_record_the_source_holds_verbatim_skips_the_engine_verdict(monkeypatch):
    """A stored answer the source contains LITERALLY is correct by definition.

    Live: 'benitezrodrigo169@gmail.com' sat in the trusted document at char 228
    while the calibrated statement scored p=0.416 - the markdown links around
    the address alone moved the same judgement to 0.55 - so a CORRECT record was
    declared a misfit, re-probed through a full ComoRAG sweep and lost.  A
    verbatim copy is not a judgement call; a short word or an address the
    source does NOT hold still goes to the engine."""
    from AI import laya_hooks

    h = _Harness()
    cfg = h._ff_cfg({"instruction": "fill"})
    calls = {"n": 0}

    def _verdict(*a, **k):
        calls["n"] += 1
        return False

    monkeypatch.setattr(laya_hooks, "value_accuracy_or", _verdict)
    source = ("# Rodrigo David Benitez\n"
              "[benitezrodrigo169@gmail.com](mailto:benitezrodrigo169@gmail.com)\n"
              "Buenos Aires, Argentina")

    h._ff_last_match_conf = 0.9
    assert h._ff_reuse_confidence("Email address*",
                                  "benitezrodrigo169@gmail.com",
                                  cfg, source) == 1.0
    assert calls["n"] == 0        # the source already proves it

    # A short word occurs everywhere in a big pool and proves nothing.
    h._ff_last_match_conf = 0.9
    assert h._ff_reuse_confidence("Authorized to work?", "Yes",
                                  cfg, source) == 0.0
    # An address the source does not hold is not a judgement call either: an
    # AUTO record nobody can point at is dropped without the engine.
    h._ff_last_match_conf = 0.9
    assert h._ff_reuse_confidence("Email address*", "someoneelse@gmail.com",
                                  cfg, source) == 0.0
    assert calls["n"] == 1


def test_a_record_is_the_answer_not_a_string_to_paste():
    """A stored record answers the QUESTION; it does not certify a string for
    every occurrence of that question.  The same ask is a textarea on one form
    and a length-capped input on the next, and a record that THIS control
    cannot hold is handed to the extractor as context (re-fitted), never typed
    in raw - the wording belongs to the occurrence it was written for."""
    h = _Harness()
    long_record = (
        "Forward-deployed AI/ML engineer who takes AI from prototype to "
        "production in regulated, on-prem and air-gapped environments, and "
        "owns the last mile across speech, translation and RAG tooling.")

    # A plain single-line text input takes it as it stands.
    assert h._ff_fit_record(
        "Role", "DevOps engineer", {"tag": "input", "type": "text"},
        "text") == "DevOps engineer"
    # A textarea does not: the wording belongs to the other occurrence.
    assert h._ff_fit_record(
        "Role", long_record, {"tag": "textarea"}, "text") == ""
    # Neither does a length-capped control.
    assert h._ff_fit_record(
        "Role", long_record,
        {"tag": "input", "type": "text", "maxlength": 100}, "text") == ""
    # A band learned on a choice question is scalarized for a number box.
    assert h._ff_fit_record(
        "Years of experience", "2-5 years",
        {"tag": "input", "type": "number"}, "text") == "2"


def test_a_verified_label_matches_a_reworded_question():
    """The same question reworded or translated must hit the verified record -
    a pure cosine gate missed it and the field was asked again."""
    h = _Harness()
    cfg = h._ff_cfg({"instruction": "fill"})
    pool = {
        "please provide your salary expectations in usd in a monthly basis":
            "6000",
    }
    got, key = h._ff_learned_match(
        "¿Cuál es tu remuneración bruta pretendida para el rol?*", pool, cfg,
        manual_keys=set(pool))
    assert got == ""                       # no word overlap with that label
    got, key = h._ff_learned_match(
        "Please provide your salary expectations (USD per month)*", pool, cfg,
        manual_keys=set(pool))
    assert got == "6000" and key in pool


def test_one_question_is_one_record_however_it_is_worded(monkeypatch):
    """The same question in two wordings must not become two records with two
    answers (live: "how much experience you have with cloud providers like
    aws?" and "how many years of experience do you have with aws?" each got
    their own entry, and they disagreed).

    Laya answers the same-question question; the node's LLM is only its
    fallback, so the engine verdict is what decides here."""
    from AI import laya_hooks

    h = _Harness()
    cfg = h._ff_cfg({"instruction": "fill"})
    h._ff_pass_cfg = cfg
    h._ff_verified = []
    h._ff_new_learned = []
    h._ff_canon_cache = {}
    canonical = "how many years of experience do you have with aws"
    h._ff_known_answers = {canonical: "6"}

    def _same(label, cands, fallback=None, agree=None):
        return cands[0]

    monkeypatch.setattr(laya_hooks, "same_question_or", _same)

    reworded = "how much experience you have with cloud providers like aws?"
    # An AUTO answer that disagrees is NOT learned: the pool keeps its answer.
    h._ff_learn(reworded, "3", "pool")
    assert h._ff_new_learned == []

    # A USER answer replaces the SAME record instead of adding a second one.
    h._ff_learn(reworded, "7", "pool", manual=True)
    assert h._ff_verified == [(canonical, "7")]


def test_a_question_asked_in_another_language_links_to_the_same_record(monkeypatch):
    """Same question, ANOTHER LANGUAGE, same answer: the pool keeps ONE record.

    The translated label shares no word with the stored one, so the lexical
    prefilter never offered the record and the answer was stored twice, once
    per language.  A record that already holds the SAME answer is a candidate.
    """
    from AI import laya_hooks

    h = _Harness()
    cfg = h._ff_cfg({"instruction": "fill"})
    h._ff_pass_cfg = cfg
    h._ff_verified = []
    h._ff_new_learned = []
    h._ff_canon_cache = {}
    canonical = "please provide your salary expectations in usd"
    h._ff_known_answers = {canonical: "6000"}

    def _same(label, cands, fallback=None, agree=None):
        return cands[0]

    monkeypatch.setattr(laya_hooks, "same_question_or", _same)

    spanish = "cual es tu remuneracion bruta pretendida para el rol?"
    h._ff_learn(spanish, "6000", "pool")
    assert h._ff_new_learned == []      # already answered, in the other language

    # A DIFFERENT answer is never silently folded into that record.
    h._ff_learn(spanish, "9000", "pool")
    assert h._ff_new_learned == []      # dropped: the pool keeps "6000"


def test_unrelated_questions_never_pay_a_verdict(monkeypatch):
    """The lexical prefilter keeps the same-question verdict off both the
    engine and the model for every field that shares nothing with the store."""
    from AI import laya_hooks

    h = _Harness()
    cfg = h._ff_cfg({"instruction": "fill"})
    calls = []

    def _same(*a, **k):
        calls.append(a)
        return ""

    monkeypatch.setattr(laya_hooks, "same_question_or", _same)
    h._ff_llm_call = lambda *a, **k: calls.append(a) or "1"
    known = {"please provide your salary expectations in usd": "6000"}

    assert h._ff_canonical_label("LinkedIn Profile*", known, cfg) == ""
    assert calls == []


def test_learned_answers_are_pushed_into_the_wired_context_node():
    """The form filler PUSHES this pass's material into the Context pool it is
    wired to (ctx_out -> ctx_in), so the learned answers persist even though the
    passive context node never executes - otherwise the audit dialog showed no
    Learned entries."""
    h = _Harness()
    pushed = []
    h._ctx_serve = lambda nid, stop_flag=None: (pushed.append(nid), "")[1]
    _wire(h, fields=[{"id": "f0", "label": "First name"}], reads=["Rodrigo"])
    node = _node()
    node["connections"] = {
        "output": [],
        "ctx_out": [{"node_id": "ctx1", "input_port": "ctx_in"}],
    }
    h._execute_form_filling_node(node, None)
    assert "ctx1" in pushed


def test_zero_fields_is_noop():
    h = _Harness()
    _wire(h, fields=[])
    nxt = h._execute_form_filling_node(_node(), None)
    assert nxt == "__done__"
    assert h.writes == []
    summary = json.loads(h.get_variable("node_ff1_output"))
    assert summary["fields_total"] == 0
    assert summary["filled"] == 0 and summary["failed"] == 0


def test_field_without_evidence_is_never_asked_of_the_model():
    """Nothing retrieved: the model is NOT asked.  It used to be, and it filled
    the gap with a guess - live, three Yes/No questions were answered 'No'
    against an empty evidence block.  The last-resort N/A is what lands now;
    with the policy off the field is left alone."""
    h = _Harness()
    _wire(h, fields=[{"id": "f0", "label": "Email"}], evidence="",
          llm_value="N/A", reads=["N/A"])
    h._execute_form_filling_node(_node(), None)
    summary = json.loads(h.get_variable("node_ff1_output"))
    assert summary["filled"] == 1 and summary["answered_na"] == 1
    assert h.llm_calls == [], "no evidence means no generation"
    assert h.writes == [("f0", "N/A")]

    h2 = _Harness()
    _wire(h2, fields=[{"id": "f0", "label": "Email"}], evidence="",
          llm_value="N/A")
    h2._execute_form_filling_node(_node(answer_no=False, answer_na=False), None)
    assert json.loads(h2.get_variable("node_ff1_output"))["skipped"] == 1
    assert h2.writes == []
    assert h2.llm_calls == []


def test_already_filled_field_is_skipped_by_the_harness():
    """Skipping is the HARNESS's rule, not the model's: a field the page
    already fills is left untouched - no probe, no model call, no write - and
    the existing value is reported.  The model is only ever asked to answer."""
    h = _Harness()
    _wire(h, fields=[{"id": "f0", "label": "Phone number"}],
          current="+54 11 5494 0148")
    h._execute_form_filling_node(_node(), None)
    summary = json.loads(h.get_variable("node_ff1_output"))
    assert summary["skipped"] == 1 and summary["filled"] == 0
    assert h.writes == [] and h.llm_calls == [] and h.probes == []
    assert summary["fields"][0]["value"] == "+54 11 5494 0148"


def test_a_choice_never_keeps_an_answer_that_is_not_one_of_its_options():
    """A CHOICE control can only HOLD one of the page's OWN options.  A value
    that is not one of them - a question the pool echoed back, a poisoned
    learned record, a prose reply - has NO control to land in: it was written,
    the read-back echoed it, the field was reported "filled" while the page
    held nonsense, and that value was LEARNED so every later run replayed it
    (live: a multi-choice field written with its own question, and with
    ANOTHER question's text).  Snap it onto an option or leave the field."""
    question = ("Which option best describes your experience with AWS and "
                "Machine Learning?")
    h = _Harness()
    # The RENDERED shape the enumerator hands over: ONE control per option,
    # each carrying its own option text as `label` and the group question.
    _wire(h, fields=[
        {"id": "f0", "label": "I use AWS services daily",
         "question": question + "*", "kind": "choice", "type": "radio",
         "tag": "input", "name": "g1", "group": "n:g1",
         "index": 0, "type_index": 0},
        {"id": "f1", "label": "I never used AWS",
         "question": question + "*", "kind": "choice", "type": "radio",
         "tag": "input", "name": "g1", "group": "n:g1",
         "index": 1, "type_index": 1},
    ])
    # The extraction hands back the QUESTION itself.
    h._ff_extract = lambda *a, **k: question
    h._execute_form_filling_node(_node(), None)
    summary = json.loads(h.get_variable("node_ff1_output"))
    assert len(summary["fields"]) == 1          # the group merged to ONE field
    assert summary["fields"][0]["status"] == "skipped"
    assert summary["fields"][0]["value"] == ""
    assert h.writes == []              # nothing was written into the field
    assert h._ff_new_learned == []     # ...and nothing was learned from it


def test_a_choice_off_list_answer_is_snapped_onto_a_real_option():
    """The answer only has to MEAN an option: a short label (before ':' or
    '(') or containment maps onto the page's OWN option text, so the write
    still lands on a real control."""
    h = _Harness()
    _wire(h, fields=[
        {"id": "f0", "label": "Básico: conozco la sintaxis",
         "question": "English level*", "kind": "choice", "type": "radio",
         "tag": "input", "name": "g2", "group": "n:g2",
         "index": 0, "type_index": 0},
        {"id": "f1", "label": "Avanzado: creo y despliego modelos",
         "question": "English level*", "kind": "choice", "type": "radio",
         "tag": "input", "name": "g2", "group": "n:g2",
         "index": 1, "type_index": 1},
    ], reads=["Básico: conozco la sintaxis"])
    h._ff_extract = lambda *a, **k: "Básico"
    h._execute_form_filling_node(_node(), None)
    assert h.writes == [("f0", "Básico: conozco la sintaxis")]


def test_a_learned_record_holding_its_own_question_is_not_reused():
    """A record whose VALUE is the field's OWN question is not an answer: the
    pre-fix runs learned exactly that (they wrote the question into the field
    once the option list had collapsed to it), and reusing it kept every later
    run replaying the question.  Such a record is ignored and the field is
    answered afresh."""
    question = ("Which option best describes your experience with AWS and "
                "Machine Learning?")
    h = _Harness()
    _wire(h, fields=[
        {"id": "f0", "label": "I use AWS services daily",
         "question": question + "*", "kind": "choice", "type": "radio",
         "tag": "input", "name": "g3", "group": "n:g3",
         "index": 0, "type_index": 0},
        {"id": "f1", "label": "I never used AWS",
         "question": question + "*", "kind": "choice", "type": "radio",
         "tag": "input", "name": "g3", "group": "n:g3",
         "index": 1, "type_index": 1},
    ], reads=["I use AWS services daily"])
    # The POOL carries the poisoned record: label -> the question itself.
    from .common import _ff_render_corrections
    h._ff_gather_source_text = lambda node: _ff_render_corrections(
        [(question + "*", question)])
    h._ff_retrieve = lambda *a, **k: "7+ years of ML experience with AWS."
    h._ff_extract = lambda *a, **k: "I use AWS services daily"
    h._execute_form_filling_node(_node(), None)
    assert h.writes == [("f0", "I use AWS services daily")]
    assert question not in [w[1] for w in h.writes]


def test_a_page_value_holding_markdown_is_refilled_with_its_plain_form():
    """Live: a learned '**1**' was written into a digits-only question, read
    back verbatim, and then skipped on every later pass - the semantic rail
    agrees the value answers the field while the site rejects it.  A page value
    that still carries markup is not a fill, so the field is filled again."""
    label = "¿Cuántos años de experiencia tienes con Modelo de datos?*"
    h = _Harness()
    _wire(h, fields=[{"id": "f0", "label": label, "kind": "text",
                      "tag": "input"}],
          current="**1**", llm_value="1", reads=["1"])
    # The POOL carries the poisoned record: the answer learned with markers.
    from .common import _ff_render_corrections
    h._ff_gather_source_text = lambda node: _ff_render_corrections(
        [(label, "**1**")])
    h._execute_form_filling_node(_node(), None)
    summary = json.loads(h.get_variable("node_ff1_output"))
    assert h.writes == [("f0", "1")]      # NOT skipped as 'already filled'
    assert summary["fields"][0]["status"] == "filled"
    assert summary["fields"][0]["value"] == "1"


def test_a_combo_holding_the_answer_is_committed_not_skipped():
    """Holding a typeahead's option TEXT is not a COMMITTED selection: the
    widget validates against its OWN list, so the form can look filled and
    still refuse to advance (live: a learned city left in a typeahead, 'Next'
    did nothing and the chain looped).  The node COMMITS the pick (type ->
    click the option) before it declares the field done; a control that will
    not commit falls through to the normal write instead of being skipped."""
    opts = ["Ezeiza, Buenos Aires Province, Argentina",
            "Buenos Aires, Buenos Aires Province, Argentina"]
    h = _Harness()
    _wire(h, fields=[{"id": "loc", "label": "Location (city)*",
                      "kind": "combo", "options": opts}],
          current=opts[0], llm_value=opts[0], reads=[opts[0]])
    commits = []
    h._ff_commit_combo = lambda field, value, sf, driver=None: (
        commits.append(value), opts[0])[1]
    h._execute_form_filling_node(_node(), None)
    summary = json.loads(h.get_variable("node_ff1_output"))
    assert commits == [opts[0]]                       # the pick was committed
    assert summary["skipped"] == 1 and h.writes == []  # ...and nothing churned
    assert summary["fields"][0]["value"] == opts[0]

    # A combo the page will NOT commit is no longer skipped - it falls through
    # to the normal write (the old behaviour left the form stuck on 'Next').
    h2 = _Harness()
    _wire(h2, fields=[{"id": "loc", "label": "Location (city)*",
                       "kind": "combo", "options": opts}],
          current=opts[0], llm_value=opts[0], reads=[opts[0]])
    h2._ff_commit_combo = lambda field, value, sf, driver=None: ""
    h2._execute_form_filling_node(_node(), None)
    assert h2.writes == [("loc", opts[0])]


def test_a_prefilled_select_is_corrected_from_the_context():
    """A <select> shows its FIRST option until someone picks, so a pre-filled
    value there is usually the page's own DEFAULT.  It is re-resolved from the
    context and overwritten when it differs (observed: 'Which location are you
    applying for?' sat on 'Colombia' while the resume says Argentina)."""
    h = _Harness()
    _wire(h, fields=[{"id": "loc",
                      "label": "Which location are you applying for?",
                      "kind": "select", "tag": "select",
                      "options": ["Colombia", "Argentina", "Uruguay"]}],
          evidence="Buenos Aires, Argentina", llm_value="Argentina",
          reads=["Argentina"], current="Colombia")
    h._execute_form_filling_node(_node(), None)
    assert h.writes == [("loc", "Argentina")]
    assert json.loads(h.get_variable("node_ff1_output"))["filled"] == 1


def test_a_select_that_already_holds_the_answer_is_left_alone():
    """The same control holding the SAME option the context resolves to is not
    re-written (no churn, no re-ask)."""
    h = _Harness()
    _wire(h, fields=[{"id": "loc",
                      "label": "Which location are you applying for?",
                      "kind": "select", "tag": "select",
                      "options": ["Colombia", "Argentina"]}],
          evidence="Buenos Aires, Argentina", llm_value="Argentina",
          reads=["Argentina"], current="Argentina")
    h._execute_form_filling_node(_node(), None)
    assert h.writes == []
    assert json.loads(h.get_variable("node_ff1_output"))["skipped"] == 1


def test_a_prefilled_text_field_is_still_left_untouched():
    """Only LIST-BACKED controls are re-resolved: a text field the page already
    fills stays untouched (the original skip rule)."""
    h = _Harness()
    _wire(h, fields=[{"id": "nm", "label": "First name", "kind": "text"}],
          evidence="Rodrigo", llm_value="Rodrigo", reads=["Rodrigo"],
          current="Rodrigo")
    h._execute_form_filling_node(_node(), None)
    assert h.writes == []
    assert json.loads(h.get_variable("node_ff1_output"))["skipped"] == 1


def test_a_wrong_prefilled_value_is_refilled_when_laya_finds_it_inaccurate(monkeypatch):
    """The 'already filled' skip must be SEMANTIC, not blind: the page can hold
    a stale answer, or the read resolved a SIBLING control (the required
    'Linkedin Profile Url' read as the EMAIL).  Laya judges whether the value
    actually answers the field - accurate -> skip; inaccurate -> refill.

    Uses a plain value on purpose: the accuracy pass is form-agnostic and does
    NOT consult the evidence, so it must not be entangled with the exact-value
    grounding rule (URLs / emails must come from the SOURCE document)."""
    from AI import laya_hooks
    monkeypatch.setattr(laya_hooks, "value_accuracy_or",
                        lambda field, value, evidence="", fallback=None: False)
    h = _Harness()
    _wire(h, fields=[{"id": "f0", "label": "Current city*"}],
          current="Springfield", evidence="Lives in Riverside.",
          llm_value="Riverside", reads=["Riverside"])
    h._execute_form_filling_node(_node(), None)
    assert h.writes == [("f0", "Riverside")]


def test_an_accurate_prefilled_value_is_skipped_when_laya_confirms_it(monkeypatch):
    """A value Laya confirms answers the field is left alone - no probe, no
    model call, no write - so a correct pre-filled field costs only one verdict."""
    from AI import laya_hooks
    monkeypatch.setattr(laya_hooks, "value_accuracy_or",
                        lambda field, value, evidence="", fallback=None: True)
    h = _Harness()
    _wire(h, fields=[{"id": "f0", "label": "Current city*"}],
          current="Riverside")
    h._execute_form_filling_node(_node(), None)
    assert h.writes == [] and h.probes == [] and h.llm_calls == []
    assert json.loads(h.get_variable("node_ff1_output"))["skipped"] == 1


def test_a_value_literally_in_the_source_is_accepted_without_laya(monkeypatch):
    """The TRUSTED SOURCE (the context pool wired into ctx_in) is the root
    truth: a value it literally contains is already correct - no engine verdict
    needed - so the field is skipped."""
    from AI import laya_hooks
    monkeypatch.setattr(laya_hooks, "value_accuracy_or",
                        lambda field, value, evidence="", fallback=None: None)
    h = _Harness()
    _wire(h, fields=[{"id": "f0", "label": "Current city*"}],
          current="Riverside")
    node = _node()
    node["inputs"] = [{"from_node": "ctx1", "input_port": "ctx_in"}]
    h.set_variable("node_ctx1_output", "Lives in Riverside.")
    h._execute_form_filling_node(node, None)
    assert h.writes == []
    assert json.loads(h.get_variable("node_ff1_output"))["skipped"] == 1


def test_one_field_filled_and_bound_by_runtime():
    h = _Harness()
    _wire(h, fields=[{"id": "f0", "label": "Email"}], value="a@b.com",
          reads=["a@b.com"], llm_value="a@b.com")
    h._execute_form_filling_node(_node(), None)
    # The write received the enumerate-provided field id + the model's value —
    # the model never supplied a selector or coordinate.
    assert h.writes == [("f0", "a@b.com")]
    summary = json.loads(h.get_variable("node_ff1_output"))
    assert summary["filled"] == 1
    assert summary["fields"][0]["value"] == "a@b.com"


def test_an_empty_readback_retries_the_write_not_the_answer():
    """A read-back that comes back EMPTY is a WRITE failure.

    The live log says 'target unresolved or not editable' - the control never
    took the value.  Re-generating the answer cost ~30 s and CHANGED it ('No'
    where 'Yes' was meant), so the same answer is written again instead.
    """
    h = _Harness()
    # First read-back is empty -> one re-write; second read-back lands.
    _wire(h, fields=[{"id": "f0", "label": "Email"}],
          reads=["", "a@b.com"], llm_value="a@b.com")
    h._execute_form_filling_node(_node(), None)
    # initial extract + the semantic verdict - NO second generation.
    assert len(h.llm_calls) == 2
    assert len(h.writes) == 2           # initial write + one rewrite
    assert all(value == "a@b.com" for _fid, value in h.writes), \
        "the retried write carries the SAME answer"
    summary = json.loads(h.get_variable("node_ff1_output"))
    assert summary["filled"] == 1


def test_choice_field_is_filled_with_an_option_the_page_has():
    """A radio group is ONE question with N options; the runtime only ever
    writes an option LABEL the page offers, so the write has a control to hit."""
    h = _Harness()
    _wire(h, fields=[
        {"id": "r0", "label": "Yes", "kind": "choice",
         "question": "Sponsorship?", "group": "n:sponsor"},
        {"id": "r1", "label": "No", "kind": "choice",
         "question": "Sponsorship?", "group": "n:sponsor"},
    ], value="Yes", reads=["Yes"], llm_value="Yes")
    h._execute_form_filling_node(_node(), None)
    assert h.writes == [("r0", "Yes")]
    summary = json.loads(h.get_variable("node_ff1_output"))
    assert summary["filled"] == 1


def test_choice_readback_that_is_not_the_option_is_not_a_fill():
    """A non-empty read-back is not proof of a correct SELECTION: the click
    resolved to another control (the page holds several with the same label),
    so the field must NOT be reported as filled."""
    h = _Harness()
    _wire(h, fields=[
        {"id": "r0", "label": "A1 (Beginner)", "kind": "choice",
         "question": "English level?", "group": "n:level"},
        {"id": "r1", "label": "B2 (Upper-Intermediate)", "kind": "choice",
         "question": "English level?", "group": "n:level"},
    ], value="B2 (Upper-Intermediate)", llm_value="B2 (Upper-Intermediate)",
        reads=["A1 (Beginner)", "A1 (Beginner)"])
    h._execute_form_filling_node(_node(), None)
    summary = json.loads(h.get_variable("node_ff1_output"))
    assert summary["filled"] == 0
    assert summary["failed"] == 1


def test_verify_failure_marks_field_failed():
    h = _Harness()
    # Read-back stays empty even after the re-ask -> failed (never a false fill).
    _wire(h, fields=[{"id": "f0", "label": "Email"}],
          reads=["", ""], llm_value="a@b.com")
    h._execute_form_filling_node(_node(), None)
    summary = json.loads(h.get_variable("node_ff1_output"))
    assert summary["filled"] == 0
    assert summary["failed"] == 1


def test_verify_off_accepts_write_without_readback():
    h = _Harness()
    _wire(h, fields=[{"id": "f0", "label": "Email"}], value="x",
          reads=[""], llm_value="x")
    h._execute_form_filling_node(_node(verify=False), None)
    summary = json.loads(h.get_variable("node_ff1_output"))
    assert summary["filled"] == 1
    assert h.reads == []                # verify disabled -> no read-back
    assert len(h.llm_calls) == 2        # extract + semantic verdict


def test_include_filter_limits_processed_fields():
    h = _Harness()
    _wire(h, fields=[{"id": "f0", "label": "Email"},
                     {"id": "f1", "label": "Phone"}], value="v",
          reads=["v", "v"], llm_value="v")
    h._execute_form_filling_node(_node(fields_include="email"), None)
    assert h.probes == ["Email"]
    assert h.writes == [("f0", "v")]


def test_esc_stops_the_node_immediately_mid_field(caplog):
    """ESC must abort the RUNNING node - not wait for the field (probe +
    extract + judge, then a repair pass) to finish.  The stop is latched by the
    model-call funnel and every step consults it, so the interrupted field is
    never written and no second pass starts."""
    h = _Harness()
    state = {"stop": False}
    _wire(h, fields=[{"id": "a", "label": "First"},
                     {"id": "b", "label": "Second"},
                     {"id": "c", "label": "Third"}],
          value="v", reads=["v", "v", "v"], llm_value="v")

    def _probe(label, cfg, src, docs=None, stop_flag=None, repair_hint="",
               learned_hint="", field=None):
        # The user hits ESC while THIS field's probe is running.
        h.probes.append(label)
        state["stop"] = True
        return "evidence text"

    h._ff_retrieve = _probe
    caplog.set_level("INFO")
    h._execute_form_filling_node(_node(), lambda: state["stop"])

    summary = json.loads(h.get_variable("node_ff1_output"))
    assert summary["aborted"] is True
    assert h.writes == []                     # the running field is not written
    assert "STOPPED BY USER (ESC)" in caplog.text
    # No repair pass after an abort, and the later fields were never probed.
    assert "Form Repair Pass" not in caplog.text
    assert h.probes == ["First"]


def test_field_diagnostics_are_logged_as_a_table(caplog):
    """Every field must leave the same boxy view the LLM node leaves: the
    parameters table, the field list, the evidence, the per-field table and the
    summary - so a run can be analysed field by field."""
    h = _Harness()
    _wire(h, fields=[{"id": "f0", "label": "Email"}], value="a@b.com",
          reads=["a@b.com"], llm_value="a@b.com")

    def _probe(label, cfg, src, docs=None, stop_flag=None, repair_hint="",
               learned_hint="", field=None):
        h._ff_last_route = "CRAG: correct, 2 strip(s) kept of 2 scored"
        h._ff_last_action = "correct"
        h._ff_last_kept = 2
        return "evidence text"
    h._ff_retrieve = _probe

    caplog.set_level("INFO")
    h._execute_form_filling_node(_node(), None)
    text = caplog.text
    # The node-parameter and source tables...
    assert "Form Filler Parameters" in text
    assert "Retrieval (CRAG)" in text
    assert "Form Filler Sources" in text
    # ...and the enumerated fields...
    assert "Form Fields to Fill (1)" in text
    assert "Form Field Evidence: Email" in text
    # ...the per-field diagnostic table, carrying the route/facts/excerpts...
    assert "Form Field: Email" in text
    assert "Retrieval route" in text
    assert "CRAG: correct, 2 strip(s) kept of 2 scored" in text
    assert "CRAG verdict / strips" in text and "correct / 2 kept" in text
    # ...and the closing summary + output block.
    assert "Form Filler Summary" in text
    assert "Form Filler Output" in text


def test_prompt_valued_select_is_corrected_from_the_context():
    """A <select> sitting on a prompt entry whose VALUE attribute is not empty
    is no longer left as the page's own state: a list-backed control is
    re-resolved and the context's answer overwrites the prompt (observed: a
    location select sitting on 'Selecciona una opción' / 'Colombia' while the
    resume names another country)."""
    h = _Harness()
    _wire(h, fields=[{"id": "c0", "label": "Código del país",
                      "kind": "select",
                      "options": ["Selecciona una opción", "Ireland (+353)"]}],
          value="Ireland (+353)", reads=["Ireland (+353)"],
          llm_value="Ireland (+353)")
    del h._ff_current_value                 # use the REAL skip rule, not the stub
    h._ff_field_state = lambda f, sf, driver=None: {
        "found": True, "value": "Selecciona una opción", "valid": True,
        "message": ""}
    h._execute_form_filling_node(_node(), None)
    summary = json.loads(h.get_variable("node_ff1_output"))
    assert summary["filled"] == 1 and summary["skipped"] == 0
    assert h.writes == [("c0", "Ireland (+353)")]


def test_a_switch_without_evidence_is_never_answered_no():
    """A toggle is a boolean, but 'No' is still a fact nobody stated.

    With no evidence the old rail wrote 'No' (the control's own none answer),
    which is exactly what an eligibility/consent question must never be told on
    a guess.  Nothing is asserted now: the last-resort N/A lands instead.
    """
    h = _Harness()
    _wire(h, fields=[{"id": "s0", "label": "I agree to the terms",
                      "kind": "switch", "tag": "button"}], evidence="",
          llm_value="No", reads=["N/A"])
    h._execute_form_filling_node(_node(), None)
    summary = json.loads(h.get_variable("node_ff1_output"))
    assert h.llm_calls == [], "no evidence means no generation"
    assert "No" not in [value for _fid, value in h.writes], \
        "a negative is a fact, and no source stated one"
    assert summary["answered_na"] == 1


def test_a_field_validated_on_a_previous_pass_is_skipped_via_the_pool(caplog):
    """The chain loops back into this node; the memory that a field was already
    VALIDATED rides the CONTEXT POOL - a Context node wired to ctx_out stores
    the node's own output and serves it back on ctx_in.  On the next pass the
    field is skipped outright (no resolve, no probe, no write): the pool IS the
    memory, there is no node-local state."""
    h = _Harness()
    _wire(h, fields=[{"id": "f0", "label": "Email"}], evidence="a@b.com",
          llm_value="a@b.com", reads=["a@b.com"])
    prior = json.dumps({"mode": "web", "fields": [
        {"id": "f0", "label": "Email", "value": "a@b.com",
         "status": "filled"}]})
    node = _node()
    node["inputs"] = [{"from_node": "ctx1", "input_port": "ctx_in"}]
    h.set_variable("node_ctx1_output", prior)

    caplog.set_level("INFO")
    h._execute_form_filling_node(node, None)
    assert h.probes == [] and h.writes == []      # nothing was re-done
    assert "already handled on a previous pass (pool)" in caplog.text
    assert json.loads(h.get_variable("node_ff1_output"))["skipped"] == 1


def test_pool_memory_only_skips_fields_the_prior_pass_handled():
    """The pool memory is per field LABEL: a field the prior pass validated is
    skipped, while a field the prior pass never saw (the wizard advanced to a
    new step) is processed normally."""
    h = _Harness()
    _wire(h, fields=[{"id": "f0", "label": "Email"},
                     {"id": "f1", "label": "Phone"}],
          evidence="e", llm_value="v", reads=["v", "v"])
    prior = json.dumps({"fields": [
        {"id": "f0", "label": "Email", "value": "a@b.com",
         "status": "filled"}]})
    node = _node()
    node["inputs"] = [{"from_node": "ctx1", "input_port": "ctx_in"}]
    h.set_variable("node_ctx1_output", prior)

    h._execute_form_filling_node(node, None)
    # Only the UNSEEN field was probed / written.
    assert h.probes == ["Phone"]
    assert h.writes == [("f1", "v")]
    summary = json.loads(h.get_variable("node_ff1_output"))
    assert summary["skipped"] == 1 and summary["filled"] == 1


def test_a_pool_fact_fills_the_field_without_any_probe():
    """A field whose question the pool already tagged factually-correct is
    filled MECHANICALLY from the cache: the probe (ComoRAG) never runs and the
    model is never asked - the stored answer is reused."""
    h = _Harness()
    _wire(h, fields=[{"id": "f0", "label": "Email"}], evidence="ignored",
          llm_value="ignored", reads=["a@b.com"])
    # The pool carries a learned FACT for 'Email' (a sentinel block).
    prior = ("[[FORM_CORRECTIONS]]\n## Email\na@b.com\n"
             "[[/FORM_CORRECTIONS]]")
    node = _node()
    node["inputs"] = [{"from_node": "ctx1", "input_port": "ctx_in"}]
    h.set_variable("node_ctx1_output", prior)

    h._execute_form_filling_node(node, None)
    assert h.probes == []                 # ComoRAG was bypassed
    assert h.writes == [("f0", "a@b.com")]
    assert json.loads(h.get_variable("node_ff1_output"))["filled"] == 1


def test_a_combination_the_source_never_states_is_not_reused(caplog):
    """The stored 'Buenos Aires, Saint Paul' was learned from a pack that held TWO
    rows (the contact line and a 2018 employer).  Every fragment is real, so the
    verbatim rail and the entailment verdict both pass it - and the field keeps a
    value the document never states.  A compound record must appear AS WRITTEN."""
    h = _Harness()
    _wire(h, fields=[{"id": "f0", "label": "Location (city)"}],
          evidence="Buenos Aires, Argentina",
          llm_value="Buenos Aires, Argentina",
          reads=["Buenos Aires, Argentina"])
    pool = ("[[FORM_CORRECTIONS]]\n## Location (city)\nBuenos Aires, Saint Paul\n"
            "[[/FORM_CORRECTIONS]]\n\nBuenos Aires, Argentina\n"
            "Oxnard, California, USA")
    caplog.set_level("INFO")
    node = _node(ask_user=False)
    node["inputs"] = [{"from_node": "ctx1", "input_port": "ctx_in"}]
    h.set_variable("node_ctx1_output", pool)

    h._execute_form_filling_node(node, None)
    assert h.writes == [("f0", "Buenos Aires, Argentina")]
    assert "combination the source never states" in caplog.text


def test_a_record_the_source_does_not_hold_is_not_written():
    """A stale AUTO record ('PDF resume_rodrigo_benitez.pdf' for a 'Photo' field)
    rides in as context and the extractor echoes it back.  With nothing in the
    source to point at, the field is LEFT ALONE - never filled from a record the
    pool cannot ground."""
    h = _Harness()
    _wire(h, fields=[{"id": "f0", "label": "Photo"}], evidence="",
          llm_value="", reads=[])
    pool = ("[[FORM_CORRECTIONS]]\n## Photo\nPDF resume_rodrigo_benitez.pdf\n"
            "[[/FORM_CORRECTIONS]]\n\nRodrigo David Benitez\nBuenos Aires")
    node = _node(ask_user=False, answer_na=False)
    node["inputs"] = [{"from_node": "ctx1", "input_port": "ctx_in"}]
    h.set_variable("node_ctx1_output", pool)

    h._execute_form_filling_node(node, None)
    assert h.writes == []
    assert json.loads(h.get_variable("node_ff1_output"))["skipped"] == 1


def test_a_correct_fill_is_tagged_as_a_fact_for_the_pool():
    """A field the system FILLED correctly is tagged as a fact (so a later
    page/run reuses it); a failed / N/A field is NOT."""
    h = _Harness()
    _wire(h, fields=[{"id": "f0", "label": "Email"}], evidence="a@b.com",
          llm_value="a@b.com", reads=["a@b.com"])
    h._execute_form_filling_node(_node(), None)
    assert ("Email", "a@b.com") in h._ff_new_learned

    h2 = _Harness()
    _wire(h2, fields=[{"id": "f0", "label": "Photo"}], evidence="",
          llm_value="N/A", reads=["N/A"])
    h2._execute_form_filling_node(_node(), None)
    assert not any(lab == "Photo" for lab, _ in h2._ff_new_learned)


def test_a_verified_prefilled_field_is_tagged_as_a_fact():
    """A field the PAGE already held (skipped as already filled) is 'considered
    correct' too and MUST be learned - otherwise a run where every field was
    pre-filled (the common re-entry case) stores NO facts at all."""
    h = _Harness()
    _wire(h, fields=[{"id": "f0", "label": "First name"}], current="Rodrigo")
    h._execute_form_filling_node(_node(), None)
    assert json.loads(h.get_variable("node_ff1_output"))["skipped"] == 1
    assert ("First name", "Rodrigo") in h._ff_new_learned

    # An EMPTY skip (nothing retrieved) is NOT a fact.
    h2 = _Harness()
    _wire(h2, fields=[{"id": "f0", "label": "Location"}], evidence="")
    h2._execute_form_filling_node(_node(answer_no=False, answer_na=False), None)
    assert not any(lab == "Location" for lab, _ in h2._ff_new_learned)


def test_repeated_identical_passes_end_the_chain_instead_of_looping():
    """A field that never changes outcome makes the chain re-enter this node
    forever (live: 51 identical re-entries on one LinkedIn modal, ~10.7h).  Once
    the per-field outcome repeats identically the node must END the chain, not
    report success and loop again - and a DIFFERENT outcome (a real next wizard
    step) must reset the streak so a genuine multi-step flow is never cut."""
    h = _Harness()
    _wire(h, fields=[{"id": "f0", "label": "Linkedin Profile Url*"}],
          evidence="")
    node = _node(max_stall_passes=3, answer_no=False, answer_na=False)
    node["connections"] = {"output": [{"node_id": "next"}]}

    outs = [h._execute_form_filling_node(node, None) for _ in range(3)]
    assert outs == ["next", "next", "__done__"]

    # A DIFFERENT field set (the wizard advanced) resets the streak.
    h._ff_enumerate_web = lambda cfg, sf: [{"id": "f0", "label": "Step two"}]
    assert h._execute_form_filling_node(node, None) == "next"


def test_partial_combo_answer_is_typed_then_pinned_to_the_full_option(monkeypatch):
    """A typeahead renders options ONLY once text is typed, so a combo the scan
    saw as '(free text)' answers partial free text the page never commits.  The
    resolve must TYPE the answer to reveal the list, then pin it onto the FULL
    option (the city over the sibling province) - not write the partial value
    that made the SAME field loop forever."""
    from AI import laya_hooks
    h = _Harness()
    field = {"id": "f0", "label": "Location (city)*", "kind": "combo",
             "options": []}
    opts = ["Buenos Aires Province, Argentina",
            "Buenos Aires, Buenos Aires Province, Argentina"]
    typed = []
    h._ff_combo_options = lambda f, sf, driver=None, type_text=None: (
        typed.append(type_text), list(opts))[1]
    monkeypatch.setattr(laya_hooks, "choose_option_by_source",
                        lambda q, s, o: (1, True))
    got = h._ff_resolve_combo_option(
        field, "Buenos Aires", "Location: Buenos Aires, Argentina",
        "Location (city)*", None)
    assert typed == ["Buenos Aires"]     # the answer was typed to reveal the list
    assert got == "Buenos Aires, Buenos Aires Province, Argentina"


def test_partial_combo_answer_falls_back_to_the_short_label_rail(monkeypatch):
    """With Laya unavailable the deterministic short-label rail still pins
    'Buenos Aires' onto the option that STARTS with the city, not the sibling
    province entry."""
    from AI import laya_hooks
    h = _Harness()
    field = {"id": "f0", "label": "Location (city)*", "kind": "combo",
             "options": []}
    opts = ["Buenos Aires Province, Argentina",
            "Buenos Aires, Buenos Aires Province, Argentina"]
    h._ff_combo_options = lambda f, sf, driver=None, type_text=None: list(opts)
    monkeypatch.setattr(laya_hooks, "choose_option_by_source",
                        lambda q, s, o: (None, False))
    got = h._ff_resolve_combo_option(
        field, "Buenos Aires", "Location: Buenos Aires, Argentina",
        "Location (city)*", None)
    assert got == "Buenos Aires, Buenos Aires Province, Argentina"


def test_learned_combo_answer_without_evidence_is_still_pinned(monkeypatch):
    """A LEARNED answer arrives with NO evidence (the learned fast-path skips
    the probe, so `evidence` is '').  The field still asks for a SELECTION, so
    the learned VALUE must act as its own source and be pinned onto a real
    option - not written as free text the typeahead never commits."""
    from AI import laya_hooks
    h = _Harness()
    field = {"id": "f0", "label": "Location (city)*", "kind": "combo",
             "options": []}
    opts = ["Buenos Aires Province, Argentina",
            "Buenos Aires, Buenos Aires Province, Argentina"]
    h._ff_combo_options = lambda f, sf, driver=None, type_text=None: list(opts)
    seen = {}

    def _pick(q, s, o):
        seen["source"] = s
        return 1, True

    monkeypatch.setattr(laya_hooks, "choose_option_by_source", _pick)
    got = h._ff_resolve_combo_option(
        field, "Buenos Aires", "", "Location (city)*", None)
    assert seen["source"] == "Buenos Aires"   # the record IS the source
    assert got == "Buenos Aires, Buenos Aires Province, Argentina"
