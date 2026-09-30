"""One value for one field: prompt, sentinels, yes/no."""

import json

from ._test_support import (
    _Harness,
    _node,
    _wire,
)


def test_extract_returns_first_line_and_skips():
    h = _Harness()
    cfg = h._ff_cfg({})
    h._ff_llm_call = lambda p, s, c, f: "  John Doe  \ntrailing note"
    assert h._ff_extract("Name", "ev", cfg, None) == "John Doe"
    h._ff_llm_call = lambda p, s, c, f: "SKIP."
    assert h._ff_extract("Name", "ev", cfg, None) == ""
    h._ff_llm_call = lambda p, s, c, f: " thinkingplan<｜end▁of▁thinking｜>\nJane"
    assert h._ff_extract("Name", "ev", cfg, None) == "Jane"


def test_empty_reply_retry_shrinks_the_evidence():
    """A prompt that overflows the engine context (observed: n_ctx 512 vs a
    754-token prompt) must be retried with a SHORTER prompt."""
    h = _Harness()
    cfg = h._ff_cfg({"instruction": "fill", "max_tokens": 256,
                     "answer_no": False})
    calls = []

    def _llm(prompt, system, c, f):
        calls.append(len(prompt))
        return "" if len(calls) == 1 else "A value."

    h._ff_llm_call = _llm
    assert h._ff_extract("Field", "E" * 4000, cfg, None) == "A value."
    assert len(calls) == 2
    assert calls[1] < calls[0]        # the retry prompt is SHORTER


def test_llamacpp_call_carries_the_context_size(monkeypatch):
    h = _Harness()
    seen = {}
    h._resolve_api_url = lambda: "http://x"
    monkeypatch.setattr(
        _Harness, "_call_llamacpp_api",
        lambda self, **kw: seen.update(kw) or "ok",
        raising=False,
    )
    cfg = h._ff_cfg({"instruction": "fill", "context_size": 4096})
    assert h._ff_llm_call("p", "s", cfg, None) == "ok"
    assert seen["context_size"] == 4096


def test_switch_and_combo_answer_kinds_are_told_to_the_model():
    """A non-native control has no free-text target, so the model is told what
    the control IS - a toggle answers YES/NO, a dropdown answers one of its
    options - otherwise it answers like a text field and the write bounces."""
    h = _Harness()
    cfg = h._ff_cfg({"instruction": "fill"})
    seen = {}
    h._ff_llm_call = lambda p, s, c, f: seen.update(system=s) or "Yes"
    assert h._ff_extract("I agree to the terms", "ev", cfg, None,
                         kind="switch") == "Yes"
    assert "ON/OFF toggle" in seen["system"]
    h._ff_llm_call = lambda p, s, c, f: seen.update(system=s) or "Springfield"
    got = h._ff_extract("City", "ev", cfg, None, kind="combo",
                        options=["Springfield", "Bristol"])
    assert got == "Springfield"
    assert "search/autocomplete dropdown" in seen["system"]


def test_extract_no_sentinel_is_a_value_not_a_skip():
    """A negation IS an answer, returned as the model wrote it: normalising it
    through a synonym list is a language, not a rule."""
    h = _Harness()
    cfg = h._ff_cfg({"instruction": "fill"})
    h._ff_llm_call = lambda *a, **k: "No"
    assert h._ff_extract("Have you worked?", "ev", cfg, None) == "No"
    h._ff_llm_call = lambda *a, **k: "No."
    assert h._ff_extract("Have you worked?", "ev", cfg, None) == "No."
    # SKIP stays a skip
    h._ff_llm_call = lambda *a, **k: "SKIP"
    assert h._ff_extract("Have you worked?", "ev", cfg, None) == ""


def test_extract_prompt_never_offers_a_skip_escape():
    """The model ANSWERS; leaving a field alone is the harness's decision.  The
    prompt therefore demands a value and names the ONE sentinel ('SKIP') only to
    forbid it - a stray one is still never written into the field."""
    h = _Harness()
    cfg = h._ff_cfg({"instruction": "fill"})
    seen = {}
    h._ff_llm_call = lambda p, s, c, f: seen.update(prompt=p, system=s) or "V"
    assert h._ff_extract("First name", "ev", cfg, None) == "V"
    assert "never reply with the sentinel word SKIP" in seen["system"]
    assert "Value for 'First name':" in seen["prompt"]


def test_extract_includes_choice_options_in_prompt():
    h = _Harness()
    cfg = h._ff_cfg({})
    seen = {}

    def _llm(prompt, system, c, f):
        seen["prompt"] = prompt
        seen["system"] = system
        return "Yes"

    h._ff_llm_call = _llm
    got = h._ff_extract("Sponsorship?", "ev", cfg, None, options=["Yes", "No"])
    assert got == "Yes"
    assert "Yes | No" in seen["prompt"]
    assert "EXACTLY ONE" in seen["system"]


def test_missing_evidence_asks_the_model_for_the_controls_own_none_answer():
    """With NO evidence the MODEL reads the control (its own options ride in the
    prompt) and returns the answer that means none / no.  The harness no longer
    classifies the question from an English verb list."""
    h = _Harness()
    _wire(h, fields=[{"id": "q1", "label": "Have you worked before?",
                      "kind": "text"}], evidence="", llm_value="No",
          reads=["No"])
    h._execute_form_filling_node(_node(), None)
    summary = json.loads(h.get_variable("node_ff1_output"))
    assert summary["filled"] == 1
    assert h.writes == [("q1", "No")]
    assert h.llm_calls          # the model IS asked - no verb list decides this


def test_answer_no_disabled_answers_na_not_no():
    """answer_no OFF means "never answer No for me": the field is then answered
    the last-resort N/A - and with the N/A policy off too it is left alone."""
    h = _Harness()
    _wire(h, fields=[{"id": "q1", "label": "Have you worked before?",
                      "kind": "text"}], evidence="")
    h._execute_form_filling_node(_node(answer_no=False), None)
    assert h.writes == [("q1", "N/A")]
    assert json.loads(h.get_variable("node_ff1_output"))["answered_na"] == 1

    h2 = _Harness()
    _wire(h2, fields=[{"id": "q1", "label": "Have you worked before?",
                       "kind": "text"}], evidence="")
    h2._execute_form_filling_node(_node(answer_no=False, answer_na=False), None)
    assert h2.writes == []
    assert json.loads(h2.get_variable("node_ff1_output"))["skipped"] == 1


def test_model_skip_with_evidence_falls_to_the_na_policy():
    """A stray skip on a field WITH evidence is not a value: the last-resort N/A
    applies.  The harness no longer classifies the question to write 'No'."""
    h = _Harness()
    _wire(h, fields=[{"id": "q1", "label": "Are you authorized to work?",
                      "kind": "text"}], evidence="some evidence",
          llm_value="SKIP", reads=["N/A"])
    h._execute_form_filling_node(_node(), None)
    assert h.writes == [("q1", "N/A")]


def test_extract_prompt_carries_the_required_format():
    h = _Harness()
    cfg = h._ff_cfg({"instruction": "fill"})
    seen = {}
    h._ff_llm_call = lambda p, s, c, f: seen.update(prompt=p) or "2024-01-15"
    got = h._ff_extract("Start date", "ev", cfg, None,
                        format_hint=h._ff_format_spec({"type": "date"}))
    assert got == "2024-01-15"
    assert "Required format:" in seen["prompt"]
    assert "YYYY-MM-DD" in seen["prompt"]


def test_extract_keeps_whole_answer_for_multiline_field():
    h = _Harness()
    cfg = h._ff_cfg({"instruction": "fill"})
    h._ff_llm_call = lambda *a, **k: "First line of the answer.\nSecond line of the answer."
    assert h._ff_extract("Summary", "ev", cfg, None, multiline=True) == \
        "First line of the answer.\nSecond line of the answer."
    # A single-line field still keeps only its first line.
    assert h._ff_extract("Summary", "ev", cfg, None) == "First line of the answer."


def test_extract_escalates_budget_on_empty_reply():
    h = _Harness()
    cfg = h._ff_cfg({"instruction": "fill", "max_tokens": 256, "answer_no": False})
    seen = []

    def _llm(prompt, system, c, f):
        seen.append(c["max_tokens"])
        return "" if len(seen) == 1 else "A grounded value."

    h._ff_llm_call = _llm
    assert h._ff_extract("Field", "ev", cfg, None) == "A grounded value."
    assert seen == [256, 512]      # retried once with double the budget


def test_extract_keeps_the_models_own_negation_spelling():
    """A sentence that merely STARTS with 'No' is a real answer, and a bare
    negation is returned as the model wrote it - the normalisation was a
    synonym list."""
    h = _Harness()
    cfg = h._ff_cfg({"instruction": "fill"})
    h._ff_llm_call = lambda *a, **k: "No prior experience in this area."
    assert h._ff_extract("Experience", "ev", cfg, None) == \
        "No prior experience in this area."
    h._ff_llm_call = lambda *a, **k: "NO"
    assert h._ff_extract("Experience", "ev", cfg, None) == "NO"


def test_extract_discards_an_invented_identifier():
    """An identifier the source does not contain was INVENTED, never
    retrieved - it must not be written (observed: '.../in/yourname' in a URL
    the page accepted, so the repair pass runs too late to help)."""
    h = _Harness()
    cfg = h._ff_cfg({"instruction": "fill"})
    ev = "contact: linkedin.com/in/alex-doe-1234567890"
    h._ff_llm_call = lambda *a, **k: "https://www.linkedin.com/in/yourname"
    assert h._ff_extract("LinkedIn Profile URL", ev, cfg, None) == ""
    # ...while the real one passes, however the model spells it.
    h._ff_llm_call = (
        lambda *a, **k: "https://www.linkedin.com/in/alex-doe-1234567890/"
    )
    assert h._ff_extract("LinkedIn Profile URL", ev, cfg, None) == \
        "https://www.linkedin.com/in/alex-doe-1234567890/"


def test_a_borrowed_but_grounded_value_is_left_to_the_semantic_verdict():
    """A value of the wrong KIND that the source really contains is no longer
    caught by a label keyword list (postal / zip / email) - that rule was
    English-only, so it passed a wrong-kind answer in one language and killed a
    correct one in another.  The page's own native validity (type / pattern)
    catches the machine-checkable half and the semantic verdict the rest."""
    h = _Harness()
    cfg = h._ff_cfg({"instruction": "fill"})
    ev = "Sincerely, Alex · [+1 555 0100](tel:+15550100)"
    h._ff_llm_call = lambda *a, **k: "+1 555 0100"
    assert h._ff_extract("Postal", ev, cfg, None) == "+1 555 0100"


def test_extract_fits_the_field_character_limit():
    """A value longer than the page's own maxlength can NEVER be accepted, so
    the harness fits it instead of letting the page reject it on both attempts
    (observed: a 150-character field answered with 166 characters, twice, and
    the field ended 'failed')."""
    h = _Harness()
    cfg = h._ff_cfg({"instruction": "fill"})
    h._ff_llm_call = lambda *a, **k: "I build local AI tools that ship fast."
    assert h._ff_extract("What makes you unique", "ev", cfg, None,
                         multiline=True, max_chars=20) == "I build local AI"
    # No limit -> the answer is untouched.
    assert h._ff_extract("What makes you unique", "ev", cfg, None,
                         multiline=True) == \
        "I build local AI tools that ship fast."


def test_multiline_flag_comes_from_textarea_tag():
    h = _Harness()
    _wire(h, fields=[{"id": "t0", "label": "Summary", "tag": "textarea"}],
          llm_value="Line one.\nLine two.", reads=["Line one.\nLine two."])
    h._execute_form_filling_node(_node(), None)
    assert h.writes == [("t0", "Line one.\nLine two.")]


def test_on_list_choice_answer_is_returned_unchanged():
    """The rail must cost nothing when the model already answered with an
    option the page has: an exact answer (or its short label) is used as-is,
    with no second generation."""
    h = _Harness()
    cfg = h._ff_cfg({"instruction": "fill"})
    calls = []
    h._ff_llm_call = lambda *a, **k: calls.append(1) or "B2"
    opts = ["A1 (Beginner): Basic everyday expressions.",
            "B2 (Upper-Intermediate): Most goals, complex text."]
    assert h._ff_extract("Level", "ev", cfg, None, options=opts) == opts[1]
    assert len(calls) == 1


def test_off_list_choice_answer_is_mapped_onto_an_option():
    """An off-list answer is re-asked ONCE against the option list and the
    exact option LABEL is returned - the page has no control for anything
    else, so the field could never be selected (observed: LinkedIn's
    multiple-choice answered 'Full professional proficiency' against a CEFR
    list A1..C2, every write bounced and the wizard re-filled the whole 8-field
    form 11 identical times)."""
    h = _Harness()
    cfg = h._ff_cfg({"instruction": "fill"})
    opts = ["A1 (Beginner): Basic everyday expressions.",
            "B2 (Upper-Intermediate): Most goals, complex text.",
            "C2 (Proficiency): Near-native mastery."]
    prompts = []

    def _llm(prompt, system, c, f):
        prompts.append(prompt)
        return "Full professional proficiency" if len(prompts) == 1 else opts[2]

    h._ff_llm_call = _llm
    got = h._ff_extract("English proficiency", "ev", cfg, None, options=opts)
    assert got == opts[2]
    assert len(prompts) == 2                       # exactly one re-ask
    assert "NOT one of the allowed options" in prompts[1]
    assert opts[1] in prompts[1]                   # the list is pinned in it
    assert h._ff_last_raw == "Full professional proficiency"


def test_unmappable_choice_answer_is_never_written():
    """When nothing on the list fits, the field is left to the harness: a
    selection the page does not offer must never be written as its value."""
    h = _Harness()
    cfg = h._ff_cfg({"instruction": "fill", "answer_no": False})
    h._ff_llm_call = lambda *a, **k: "PURPLE"
    assert h._ff_extract("Favourite colour", "ev", cfg, None,
                         options=["Red", "Green", "Blue"]) == ""


def test_laya_picks_the_option_the_prose_answer_means(monkeypatch):
    """A choice answered in PROSE (the small model's usual shape) is resolved by
    Laya's SEMANTIC pick - the option the answer MEANS - not by the string
    rail's loose containment, which pinned a keyword soup to whichever option
    shared a word (observed: the AWS/DataOps answer mapped to the MOST SENIOR
    option, which the source does not support).  Laya answers in ONE pass, so
    no pinned re-ask is spent."""
    import AI.laya_hooks as hooks
    h = _Harness()
    cfg = h._ff_cfg({"instruction": "fill"})
    opts = [
        "I have hands-on production experience with AWS (EC2, Lambda, IAM, "
        "SQS, RDS, Kinesis, Glue, EKS) and tools such as Terraform, GitHub, "
        "Docker, and dbt.",
        "I have strong AWS experience and have worked with some of these "
        "tools, but I have limited exposure to the full AWS/DataOps stack.",
        "I have limited AWS experience and little or no hands-on experience "
        "with Terraform, Docker, dbt, or modern DataOps tooling.",
    ]
    monkeypatch.setattr(hooks, "choose_option", lambda q, a, o: (1, True))
    calls = []
    h._ff_llm_call = lambda *a, **k: calls.append(1) or (
        "AWS (EC2, Lambda, IAM, SQS, RDS, Kinesis, Glue, EKS) with hands-on "
        "production experience using Terraform, Docker and dbt.")
    got = h._ff_extract(
        "Which option best describes your experience with AWS and DataOps "
        "tooling?", "ev", cfg, None, options=opts)
    assert got == opts[1]
    assert len(calls) == 1                 # Laya answered: no pinned re-ask


def test_a_refused_laya_pick_is_not_second_guessed(monkeypatch):
    """When Laya ANSWERS but no option wins clearly, the rail must NOT spend a
    second generation on a blind pick - observed live: a cloud-experience
    question the evidence answered 'no' was filled with the MOST SENIOR option.
    The field is left to the harness, which asks the user."""
    import AI.laya_hooks as hooks
    h = _Harness()
    cfg = h._ff_cfg({"instruction": "fill"})
    opts = ["Solo usé lo básico.",
            "Implementé y desplegué soluciones completas.",
            "No tengo experiencia con la nube."]
    monkeypatch.setattr(hooks, "choose_option", lambda q, a, o: (None, True))
    calls = []
    h._ff_llm_call = lambda *a, **k: calls.append(1) or "He usado AWS Transcribe."
    assert h._ff_extract("Cloud experience", "ev", cfg, None,
                         options=opts) == ""
    assert len(calls) == 1                  # no pinned re-ask was spent


def test_a_choice_keeps_the_whole_reply_not_just_the_first_line(monkeypatch):
    """A small model often puts the QUESTION on its first line and the answer
    below; keeping only the first line handed the choice rail a question echo
    and threw the real content away (observed live: the cloud-experience answer
    was lost that way and the senior option was written)."""
    import AI.laya_hooks as hooks
    h = _Harness()
    cfg = h._ff_cfg({"instruction": "fill"})
    opts = ["Solo usé lo básico.",
            "Implementé y desplegué soluciones completas.",
            "No tengo experiencia con la nube."]
    seen = {}
    monkeypatch.setattr(
        hooks, "choose_option",
        lambda q, a, o: seen.update(answer=a) or (0, True),
    )
    h._ff_llm_call = lambda *a, **k: (
        "**¿Qué experiencia tenés usando servicios en la nube?**\n\n"
        "He usado AWS y AWS Transcribe para transcripción.")
    got = h._ff_extract("Cloud experience", "ev", cfg, None, options=opts)
    assert "AWS Transcribe" in seen["answer"]   # the real content survived
    assert got == opts[0]


def test_extract_discards_a_truncated_fragment_of_a_source_word():
    """A truncation is not a value: the model emitted 'iversid' for
    'Riverside' and 'locat' for 'location'.  Both are plain substrings of the
    source token, so a substring grounding test passes them - the value must be
    found at a WORD BOUNDARY or it is dropped."""
    h = _Harness()
    cfg = h._ff_cfg({"instruction": "fill"})
    ev = "Location (city): Riverside, Illinois"
    h._ff_llm_call = lambda *a, **k: "iversid"
    assert h._ff_extract("Location (city)", ev, cfg, None) == ""
    # The whole word passes, in any case.
    h._ff_llm_call = lambda *a, **k: "Riverside"
    assert h._ff_extract("Location (city)", ev, cfg, None) == "Riverside"
    # A derived answer the evidence never contains is NOT a fragment - it is
    # left alone (inference must keep working).
    h._ff_llm_call = lambda *a, **k: "7 years"
    assert h._ff_extract("Years of experience", ev, cfg, None) == "7 years"


def test_placeholder_note_is_never_written_and_falls_to_na():
    """A text box asking for a PHOTO cannot hold one, so a made-up note must
    not be written - observed live: the fill pass wrote '[No photo provided]'
    while its own repair pass called that exact value 'placeholder text instead
    of the real value', so the pass re-answered forever.  The fill path now
    discards it like the repair path does, and the honest verdict (N/A) is what
    the field receives."""
    h = _Harness()
    cfg = h._ff_cfg({"instruction": "fill"})
    from .common import _ff_value_rejection
    ev = "Alex Doe, DevOps Engineer. Photo available on request."
    assert _ff_value_rejection("Photo", "[No photo provided]", ev)
    replies = ["[No photo provided]", "N/A"]
    h._ff_llm_call = lambda *a, **k: replies.pop(0)
    assert h._ff_extract("Photo", ev, cfg, None) == "N/A"



def test_explicit_na_is_confirmed_against_non_empty_evidence():
    """A model that reaches for N/A while it holds evidence is answered with
    N/A on a field that was answerable - and the repair pass never revisits it
    (live: 'How many years of work experience do you have with Data
    Engineering?' written 'N/A' beside ten excerpts).  ONE confirmation; with
    nothing retrieved, N/A stands as the last-resort verdict."""
    h = _Harness()
    cfg = h._ff_cfg({"instruction": "fill"})
    replies = ["N/A", "5"]
    h._ff_llm_call = lambda *a, **k: replies.pop(0)
    ev = "Jan 2018 - Jan 2023: built data pipelines and warehouse models."
    assert h._ff_extract("How many years of experience with Data Engineering?",
                         ev, cfg, None) == "5"
    h2 = _Harness()
    h2._ff_llm_call = lambda *a, **k: "N/A"
    assert h2._ff_extract("Photo", "", cfg, None) == "N/A"


def test_choice_value_bypasses_the_invented_identifier_guard():
    """An option label is the PAGE's own text, not the source's, so the
    invented-identifier guard (which compares against the source documents)
    must not drop it."""
    h = _Harness()
    cfg = h._ff_cfg({"instruction": "fill"})
    opts = ["Yes - see https://example.com/terms", "No"]
    h._ff_llm_call = lambda *a, **k: "Yes - see https://example.com/terms"
    assert h._ff_extract("Terms", "no link here", cfg, None,
                         options=opts) == opts[0]


def test_a_discarded_value_earns_one_grounded_retry_not_an_na():
    """A value the source cannot support must NOT fall straight through to the
    last-resort N/A - the evidence is right there, so the field IS answerable
    and only the answer was refused.  Observed: a required 'share links to 3-5
    applications' textarea answered with invented example.com links was written
    'N/A' while six verbatim excerpts about those very applications sat beside
    it."""
    h = _Harness()
    cfg = h._ff_cfg({"instruction": "fill"})
    replies = ["https://example.com/realtime-transcription-service",
               "Realtime transcription service"]
    prompts = []

    def _llm(prompt, system, c, f):
        prompts.append(prompt)
        return replies.pop(0)

    h._ff_llm_call = _llm
    ev = ("The Realtime transcription service is scaled to 1,000+ "
          "concurrent audio streams.")
    assert h._ff_extract("Share links to apps", ev, cfg, None) == (
        "Realtime transcription service")
    assert len(prompts) == 2 and "REJECTED" in prompts[1]
    # Bounded: a SECOND discard gives up, and is flagged so the caller's N/A
    # note can say the answer was discarded instead of "grounded nothing".
    h2 = _Harness()
    h2._ff_llm_call = lambda p, s, c, f: "https://example.com/nope"
    assert h2._ff_extract("Share links to apps", ev, cfg, None) == ""
    assert h2._ff_last_reject
