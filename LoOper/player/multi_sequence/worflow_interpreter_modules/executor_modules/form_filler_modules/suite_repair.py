"""The post-pass: validity, content problems, corrections."""

import json

from ._test_support import (
    _Harness,
    _node,
    _wire,
)


def test_repair_does_not_treat_an_unresolved_control_as_a_rejection():
    """'field not found' says nothing about the VALUE, so it must not start a
    re-probe/re-answer loop on a field the form already accepted (live: "Form
    Repair: First name* field not found (attempt 1/4)" on a good field)."""
    h = _Harness()
    field = {"id": "f", "label": "First name*", "kind": "text"}
    assert h._ff_repair_reason(
        field, "Alex",
        {"found": False, "value": "", "valid": False,
         "message": "field not found"}) == ""
    # A REAL page rejection still flags.
    assert h._ff_repair_reason(
        field, "Alex",
        {"found": True, "value": "Alex", "valid": False,
         "message": "Please enter a valid value"}) == (
        "Please enter a valid value")


def test_semantic_judge_never_passes_its_own_template_placeholder():
    """The judge is asked for '<number>: NO <short reason>'; a small model
    echoes the literal '<short reason>', which then became the repair hint and
    told the extractor nothing."""
    h = _Harness()
    h._ff_llm_call = lambda *a, **k: "1: NO <short reason>"
    cfg = h._ff_cfg({"instruction": "fill"})
    out = h._ff_semantic_reasons(
        [{"id": "a", "label": "Headline", "value": "v",
          "status": "filled"}],
        {"a": {"id": "a", "label": "Headline", "kind": "text"}},
        cfg, None)
    assert "short reason" not in out["a"]
    assert "does not answer" in out["a"]


def test_repair_flags_a_value_over_the_fields_character_limit():
    """A value longer than the field's own limit can NEVER be accepted, so the
    repair pass must flag it - the page showed "Invalid input 133/20" while the
    pass reported nothing to correct, and the field kept a rejected value."""
    h = _Harness()
    field = {"id": "f", "label": "Headline", "kind": "text",
             "maxlength": "20"}
    state = {"found": True, "valid": True, "message": ""}
    assert "at most 20" in h._ff_repair_reason(
        field, "USD 48000+/year - negotiable against scope", state)
    # A value that FITS the limit is left alone.
    assert h._ff_repair_reason(field, "USD 48000+/year", state) == ""


def test_repair_stops_when_the_re_answer_cannot_change():
    """A semantic flag is judged ONCE per pass, so a field whose re-answer is
    the SAME value can never satisfy it - and re-writing that value made the
    pass report 'repaired' anyway (live: "attempt 1/4 corrected it ->
    '[No photo provided]'"), then flag the field again on the next pass."""
    h = _Harness()
    _wire(h, fields=[{"id": "d0", "label": "Photo", "kind": "text",
                      "tag": "input"}],
          evidence="resume text", value="No photo provided",
          reads=["No photo provided"], llm_value="No photo provided")
    h._ff_semantic_reasons = lambda *a, **k: {
        "d0": "the answer does not fit the field: not a photo"}
    h._execute_form_filling_node(_node(), None)
    summary = json.loads(h.get_variable("node_ff1_output"))
    assert summary["repaired"] == 0
    # ONE write (the fill pass), never a pointless second one.
    assert h.writes == [("d0", "No photo provided")]


def test_repair_pass_corrects_a_rejected_value():
    h = _Harness()
    _wire(h, fields=[{"id": "d0", "label": "Start date", "type": "date",
                      "tag": "input", "required": True}],
          evidence="resume text", value="2017", reads=["2017"], llm_value="2017")
    seq = iter(["2017", "2024-01-15"])
    h._ff_llm_call = lambda *a, **k: next(seq, "2024-01-15")
    states = [
        {"found": True, "value": "2017", "valid": False,
         "message": "Please enter a valid date."},
        {"found": True, "value": "2024-01-15", "valid": True, "message": ""},
    ]
    h._ff_field_state = lambda field, sf, driver=None: states.pop(0) if states else {
        "found": True, "value": "", "valid": True, "message": ""}
    h._execute_form_filling_node(_node(), None)
    summary = json.loads(h.get_variable("node_ff1_output"))
    assert summary["repaired"] == 1
    assert summary["filled"] == 1 and summary["failed"] == 0
    assert h.writes == [("d0", "2017"), ("d0", "2024-01-15")]


def test_repair_reason_flags_bad_content_the_page_accepts():
    """Native validity only sees what the PAGE rejects; a placeholder or a
    leaked explanation is accepted by the page but still wrong, so it must be
    flagged by the content check and repaired the same way."""
    h = _Harness()
    ok = {"valid": True, "message": ""}
    # An unfilled template placeholder the model copied instead of answering.
    assert "placeholder" in h._ff_repair_reason(
        {"label": "Summary"}, "I help **[Company]** achieve ****!", ok)
    assert "placeholder" in h._ff_repair_reason(
        {"label": "Cover letter"}, "Dear [Hiring Manager],", ok)
    # A page rejection still wins, with the page's own message.
    assert h._ff_repair_reason(
        {"label": "Start date"}, "2017",
        {"valid": False, "message": "Please enter a valid date."}) == \
        "Please enter a valid date."
    # Real answers are left alone.
    assert h._ff_repair_reason({"label": "Email"}, "a@b.com", ok) == ""
    assert h._ff_repair_reason({"label": "Years"}, "6", ok) == ""
    assert h._ff_repair_reason(
        {"label": "Why"}, "I have not worked in consulting.", ok) == ""
    # An identifier the source does NOT contain was invented, not retrieved
    # (the composer turned the real profile URL into '...-0000000000').
    ground = "contact: linkedin.com/in/alex-doe-1234567890 and more"
    assert "does not appear" in h._ff_repair_reason(
        {"label": "LinkedIn Profile URL"},
        "https://www.linkedin.com/in/alex-doe-0000000000/", ok, ground)
    # ...while the real one passes, and without a source nothing is claimed.
    assert h._ff_repair_reason(
        {"label": "LinkedIn Profile URL"},
        "https://www.linkedin.com/in/alex-doe-1234567890", ok,
        ground) == ""
    assert h._ff_repair_reason(
        {"label": "Site"}, "https://example.org/whatever", ok, "") == ""


def test_repair_pass_fixes_content_the_page_accepts():
    """A value the page ACCEPTS but that is plainly wrong (an unfilled
    placeholder) must never reach the page: the fill pass discards it exactly
    as the repair pass flags it, and the corrected answer lands instead.
    Leaving it to the repair pass meant the page held the placeholder from the
    moment it was written."""
    h = _Harness()
    _wire(h, fields=[{"id": "a", "label": "Summary"}],
          value="I help **[Company]** achieve ****!",
          reads=["I build local-first AI systems."])
    # Content, not validity, is the trigger.
    h._ff_field_state = lambda field, sf, driver=None: {
        "found": True, "valid": True,
        "value": "I build local-first AI systems.", "message": ""}
    seq = iter(["I help **[Company]** achieve ****!",
                "I build local-first AI systems."])
    h._ff_llm_call = lambda *a, **k: next(seq, "I build local-first AI systems.")
    h._execute_form_filling_node(_node(), None)
    summary = json.loads(h.get_variable("node_ff1_output"))
    assert summary["filled"] == 1 and summary["failed"] == 0
    assert summary["repaired"] == 0          # nothing left to correct
    assert h.writes == [("a", "I build local-first AI systems.")]


def test_repair_pass_logs_its_progress(caplog):
    """The repair pass re-reads every field it may have to correct - the ones
    it wrote AND the ones the page already filled - before it finds anything
    wrong; with no trace the run looks hung, so it must narrate its progress as
    a boxy block listing the fields it will check."""
    h = _Harness()
    _wire(h, fields=[{"id": "a", "label": "First"},
                     {"id": "b", "label": "Second"}],
          value="v", reads=["v", "v"], llm_value="v")
    caplog.set_level("INFO")
    h._execute_form_filling_node(_node(), None)
    assert "Form Repair Pass" in caplog.text
    assert "checking 2 field(s)" in caplog.text
    assert "1. First" in caplog.text and "2. Second" in caplog.text
    assert "Form Repair Summary" in caplog.text
    assert "Corrected" in caplog.text


def test_repair_can_be_disabled():
    h = _Harness()
    _wire(h, fields=[{"id": "d0", "label": "Start date", "type": "date",
                      "tag": "input"}], value="2017", reads=["2017"],
          llm_value="2017")
    h._ff_field_state = lambda field, sf, driver=None: {"found": True, "value": "2017",
                                           "valid": False, "message": "bad date"}
    h._execute_form_filling_node(_node(repair=False), None)
    summary = json.loads(h.get_variable("node_ff1_output"))
    assert summary["repaired"] == 0
    assert summary["filled"] == 1
    assert h.writes == [("d0", "2017")]


def test_repair_pass_leaves_valid_fields_alone():
    h = _Harness()
    _wire(h, fields=[{"id": "f0", "label": "Email"}], value="a@b.com",
          reads=["a@b.com"], llm_value="a@b.com")
    checked = []
    h._ff_field_state = lambda field, sf, driver=None: (
        checked.append(field.get("id"))
        or {"found": True, "value": "a@b.com", "valid": True, "message": ""}
    )
    h._execute_form_filling_node(_node(), None)
    summary = json.loads(h.get_variable("node_ff1_output"))
    assert summary["repaired"] == 0
    assert checked == ["f0"]           # checked once, valid, left untouched
    assert h.writes == [("f0", "a@b.com")]


def test_repair_refused_write_keeps_an_honest_status():
    """A typed input clears a malformed value; the write must report the
    refusal instead of claiming success, and the summary must say failed."""
    h = _Harness()
    _wire(h, fields=[{"id": "d0", "label": "Start date", "type": "date",
                      "tag": "input"}], value="2017", reads=["2017"],
          llm_value="2017")
    h._ff_write_web = lambda field, val, sf, driver=None: False
    h._ff_field_state = lambda field, sf, driver=None: {"found": True, "value": "2017",
                                           "valid": False,
                                           "message": "Please enter a valid date."}
    h._execute_form_filling_node(_node(), None)
    summary = json.loads(h.get_variable("node_ff1_output"))
    assert summary["repaired"] == 0
    assert summary["filled"] == 0
    assert summary["failed"] == 1


def test_repair_reaches_a_field_the_page_already_filled():
    """A form that only reveals its rejections once something submits it (a
    "Review" click, or a chain that clicks and loops back) re-enters with EVERY
    field already filled - so every field is SKIPped.  The repair pass must
    still reach them, or it can never fire on the pass that exists to fix
    them (observed: Checked 4 of 10, Flagged 0)."""
    h = _Harness()
    _wire(h, fields=[{"id": "d0", "label": "Start date", "type": "date",
                      "tag": "input"}], current="2017", value="2017",
          reads=["2017"], llm_value="2024-01-15")
    state = {"valid": False,
             "message": "the page rejected this value: Please enter a date."}

    def _state(field, sf, driver=None):
        return {"found": True, "valid": state["valid"],
                "message": state["message"],
                "value": h.writes[-1][1] if h.writes else "2017"}
    h._ff_field_state = _state

    def _write(field, val, sf, driver=None):
        h.writes.append((field.get("id"), val))
        state["valid"] = True
        state["message"] = ""
        return True
    h._ff_write_web = _write

    h._execute_form_filling_node(_node(), None)
    summary = json.loads(h.get_variable("node_ff1_output"))
    assert summary["repaired"] == 1
    # ONE write only - the corrected value.  The main loop wrote nothing
    # (already filled), so this proves the repair pass reached the field.
    assert h.writes == [("d0", "2024-01-15")]


def test_repair_probe_gets_the_format_not_the_rejected_value():
    """The rejected value belongs in the ANSWER prompt, never in the retrieval
    probe: the model read "return a different value" as "find a date that is
    NOT September 2026" and the plan chased the rejection instead of the
    field's real answer."""
    h = _Harness()
    _wire(h, fields=[{"id": "d0", "label": "Earliest start date",
                      "type": "date", "tag": "input"}],
          current="September 2026", value="2017", reads=["2017"])
    hints = []

    def _probe(label, cfg, src, docs=None, stop_flag=None, repair_hint=""):
        hints.append(repair_hint)
        return "evidence text"
    h._ff_probe = _probe

    prompts = []

    def _llm(prompt, system, cfg, sf):
        prompts.append(prompt)
        return "2024-02-01"
    h._ff_llm_call = _llm

    state = {"valid": False,
             "message": "the page rejected this value: September 2026"}

    def _st(field, sf, driver=None):
        return {"found": True, "valid": state["valid"],
                "message": state["message"],
                "value": h.writes[-1][1] if h.writes else "September 2026"}
    h._ff_field_state = _st

    def _write(field, val, sf, driver=None):
        h.writes.append((field.get("id"), val))
        state["valid"] = True
        state["message"] = ""
        return True
    h._ff_write_web = _write

    h._execute_form_filling_node(_node(), None)
    assert hints == ["the field expects a date as YYYY-MM-DD (e.g. 2024-01-15)"]
    # ...while the answer prompt still carries the rejection.
    assert prompts and "September 2026" in prompts[0]


def test_field_state_reports_native_validity():
    from player.web import actions as web_actions
    js = web_actions.JS_FIELD_STATE
    assert "validity" in js
    assert "validationMessage" in js
    assert "JSON.stringify" in js
    assert "__wvpVisible" in js and "__wvpTopmost" in js
    # A framework-rendered rejection must be read too: a page that only shows
    # its errors once a submit click runs leaves el.validity.valid true.  A
    # LOCALIZED message is read on a strong channel (aria-invalid / an error or
    # feedback class) whatever language it is written in, while the weak
    # role=alert channel still needs text that READS like feedback - a picker's
    # live-region month header matched role=alert and was taken for a rejection,
    # which rewrote a value the page had accepted.
    assert "pageError" in js and "aria-invalid" in js
    assert "ERROR_TEXT_RE" in js
    assert "strongChannel" in js
    # The enumerator captures the constraint attributes the repair needs.
    enum_js = web_actions.JS_ENUMERATE_FORM_FIELDS
    for attr in ("pattern", "minlength", "maxlength", "step"):
        assert attr in enum_js


# ---------------------------------------------------------------------------
# Semantic verdict: a format-valid answer to the WRONG question
# ---------------------------------------------------------------------------


def test_semantic_pass_judges_meaning_and_skips_settled_fields():
    """The content rules cannot tell a right-format answer to the wrong
    question from a correct one, so the written values go to the model as a
    numbered list.  Fields whose answer kind is already settled - a typed
    input, a choice list, a BOOLEAN control, a value the page already held -
    are never asked about."""
    h = _Harness()
    seen = []

    def _llm(prompt, system, cfg, sf):
        seen.append(prompt)
        return ("1: NO a programming language is not a spoken language\n"
                "2: OK\n")
    h._ff_llm_call = _llm
    targets = [
        {"id": "a", "status": "filled", "label": "Languages you speak",
         "value": "Python"},
        {"id": "b", "status": "filled", "label": "City",
         "value": "Springfield"},
        {"id": "c", "status": "filled", "label": "Start date",
         "value": "2017"},
        {"id": "d", "status": "filled", "label": "Have you worked before?",
         "value": "No"},
        {"id": "e", "status": "skipped", "label": "Website",
         "value": "page value"},
    ]
    field_map = {
        "a": {"id": "a", "label": "Languages you speak"},
        "b": {"id": "b", "label": "City"},
        "c": {"id": "c", "label": "Start date", "type": "date"},
        "d": {"id": "d", "label": "Have you worked before?",
              "kind": "switch"},
        "e": {"id": "e", "label": "Website"},
    }
    reasons = h._ff_semantic_reasons(targets, field_map, {}, None)
    assert list(reasons) == ["a"]           # only the semantic field
    assert "programming language" in reasons["a"]
    assert len(seen) == 1                    # ONE batched call...
    assert seen[0].count("Question:") == 2   # ... asking about two fields
    assert "page value" not in seen[0]


def test_semantic_pass_fails_open_on_an_unparseable_verdict():
    h = _Harness()
    h._ff_llm_call = lambda prompt, system, cfg, sf: "I cannot judge these."
    targets = [{"id": "a", "status": "filled", "label": "City",
                "value": "Springfield"}]
    assert h._ff_semantic_reasons(
        targets, {"a": {"id": "a", "label": "City"}}, {}, None) == {}


def test_semantic_verdict_drives_a_repair_of_a_wrong_but_valid_answer():
    """The end-to-end effect: the page ACCEPTED 'Python' in a spoken-language
    field (native validity true), so only the semantic verdict can flag it -
    and the existing re-probe/re-answer loop then corrects it."""
    h = _Harness()
    _wire(h, fields=[{"id": "f0", "label": "Languages you speak"}],
          evidence="resume text", value="Python", reads=["Python"],
          llm_value="Python")
    calls = {"n": 0}

    def _llm(prompt, system, cfg, sf):
        if "write one line" in prompt:          # the semantic verdict
            return ("1: NO a programming language is not a spoken "
                    "language\n")
        calls["n"] += 1
        return "Python" if calls["n"] == 1 else "Spanish"
    h._ff_llm_call = _llm
    h._execute_form_filling_node(_node(), None)
    summary = json.loads(h.get_variable("node_ff1_output"))
    assert summary["repaired"] == 1
    assert h.writes == [("f0", "Python"), ("f0", "Spanish")]
