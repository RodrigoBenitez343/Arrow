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


def test_field_without_evidence_asks_the_model_for_a_none_answer():
    """Nothing retrieved: the MODEL is asked ONCE, with the control's own
    options in the prompt, for the answer that means none / no - and the
    last-resort N/A is what lands when even that yields nothing.  With the
    policy off the field is left alone."""
    h = _Harness()
    _wire(h, fields=[{"id": "f0", "label": "Email"}], evidence="",
          llm_value="N/A", reads=["N/A"])
    h._execute_form_filling_node(_node(), None)
    summary = json.loads(h.get_variable("node_ff1_output"))
    assert summary["filled"] == 1 and summary["answered_na"] == 1
    assert len(h.llm_calls) == 1     # one call, and it is made with no evidence
    assert h.writes == [("f0", "N/A")]

    h2 = _Harness()
    _wire(h2, fields=[{"id": "f0", "label": "Email"}], evidence="",
          llm_value="N/A")
    h2._execute_form_filling_node(_node(answer_no=False, answer_na=False), None)
    assert json.loads(h2.get_variable("node_ff1_output"))["skipped"] == 1
    assert h2.writes == []


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


def test_verify_reasks_this_field_once_on_empty_readback():
    h = _Harness()
    # First read-back is empty -> one re-ask; second read-back lands.
    _wire(h, fields=[{"id": "f0", "label": "Email"}],
          reads=["", "a@b.com"], llm_value="a@b.com")
    h._execute_form_filling_node(_node(), None)
    # initial extract + exactly one re-ask + the semantic verdict (an untyped
    # field's answer is judged by meaning as well as by the read-back)
    assert len(h.llm_calls) == 3
    assert len(h.writes) == 2           # initial write + one rewrite
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

    def _probe(label, cfg, src, docs=None, stop_flag=None, repair_hint=""):
        # The user hits ESC while THIS field's probe is running.
        h.probes.append(label)
        state["stop"] = True
        return "evidence text"

    h._ff_probe = _probe
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

    def _probe(label, cfg, src, docs=None, stop_flag=None, repair_hint=""):
        h._ff_last_route = "comoRAG: 2 composed fact(s) + 1 verbatim excerpt(s)"
        h._ff_last_facts = 2
        h._ff_last_excerpts = 1
        return "evidence text"
    h._ff_probe = _probe

    caplog.set_level("INFO")
    h._execute_form_filling_node(_node(), None)
    text = caplog.text
    # The node-parameter and source tables...
    assert "Form Filler Parameters" in text
    assert "ComoRAG consolidation" in text
    assert "Form Filler Sources" in text
    # ...and the enumerated fields...
    assert "Form Fields to Fill (1)" in text
    assert "Form Field Evidence: Email" in text
    # ...the per-field diagnostic table, carrying the route/facts/excerpts...
    assert "Form Field: Email" in text
    assert "Retrieval route" in text
    assert "comoRAG: 2 composed fact(s) + 1 verbatim excerpt(s)" in text
    assert "Facts / excerpts" in text and "2 / 1" in text
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


def test_switch_without_evidence_is_answered_by_the_model():
    """A toggle is a boolean whatever its label reads: with nothing to ground a
    'Yes' the model is asked for the control's own none answer, and 'No' lands
    (a real value), not a switch left switching on nothing."""
    h = _Harness()
    _wire(h, fields=[{"id": "s0", "label": "I agree to the terms",
                      "kind": "switch", "tag": "button"}], evidence="",
          llm_value="No", reads=["No"])
    h._execute_form_filling_node(_node(), None)
    summary = json.loads(h.get_variable("node_ff1_output"))
    assert summary["filled"] == 1
    assert h.writes == [("s0", "No")]


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
