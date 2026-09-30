"""The node loop: fill, verify, skip, abort, tables."""

import json

from ._test_support import (
    _Harness,
    _node,
    _wire,
)


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


def test_field_with_no_evidence_is_not_swept_again_on_the_next_pass(caplog):
    """The chain loops back into this node whenever a field does not land, and
    every pass re-ran the FULL ComoRAG sweep for every field: one field burned
    512s, the same 4-field page was handled four identical times, and the
    user's ESC then discarded the whole sweep.  Against UNCHANGED sources a
    field that already returned no evidence is re-used, not re-probed - and it
    is answered the last-resort N/A once."""
    h = _Harness()
    node = _node()
    _wire(h, fields=[{"id": "f0", "label": "Email"}], evidence="",
          llm_value="N/A", reads=["N/A"])

    h._execute_form_filling_node(node, None)
    assert h.probes == ["Email"]             # the first pass DOES probe
    assert json.loads(h.get_variable("node_ff1_output"))["answered_na"] == 1

    h.llm_calls.clear()
    caplog.set_level("INFO")
    h._execute_form_filling_node(node, None)
    assert h.probes == ["Email"]             # NOT probed a second time
    assert json.loads(h.get_variable("node_ff1_output"))["answered_na"] == 1
    assert "re-using the outcome" in caplog.text


def test_field_memory_is_dropped_when_the_sources_change():
    """A learned answer changes the sources, so the field MUST be retried
    instead of being remembered as unanswerable."""
    h = _Harness()
    _wire(h, fields=[{"id": "f0", "label": "Email"}], evidence="")
    h._execute_form_filling_node(_node(rag_documents=["a.txt"]), None)
    assert h.probes == ["Email"]
    h._execute_form_filling_node(_node(rag_documents=["b.txt"]), None)
    assert h.probes == ["Email", "Email"]   # different sources -> re-probed
