"""Node config, shared helpers, field identity and filters."""

import pytest

from .common import (
    _ff_fragment_of_evidence,
    _ff_na_value,
    _ff_option_exact,
    _ff_option_match,
    _ff_placeholder_value,
    _split_list,
    _strip_think,
)
from ._test_support import (
    _Harness,
)


def test_na_value_only_writes_a_literal_a_free_text_field_can_hold():
    """A free-text control takes the literal.  A LIST-backed one can only hold
    one of its OWN options, so ``_ff_na_value`` returns '' for ANY option list -
    the choice rail asks the model for an option the page really offers, and
    the harness never guesses which option word means 'none'."""
    assert _ff_na_value() == "N/A"
    assert _ff_na_value(["N/A (not applicable)", "Yes"]) == ""
    assert _ff_na_value(["None", "Conversational", "Professional"]) == ""
    assert _ff_na_value(["Yes", "No"]) == ""


def test_cfg_parsing_tolerates_strings_and_bools():
    h = _Harness()
    cfg = h._ff_cfg({
        "mode": "Web", "verify": "false", "probe_top_k": "5",
        "max_fields": "10", "temperature": "0.2",
    })
    assert cfg["mode"] == "web"
    assert cfg["verify"] is False
    assert cfg["probe_top_k"] == 5
    assert cfg["max_fields"] == 10
    assert cfg["temperature"] == pytest.approx(0.2)


def test_placeholder_markers_are_caught_but_markdown_links_are_not():
    """A bracketed template marker the source itself contains must never be
    pasted into a field (observed: a summary filled with the source's own
    '...help [Company] with [specific goal]').  A markdown link is NOT a marker -
    its ']' is followed by '('."""
    from .common import _PLACEHOLDER_RE as rx
    for text in ("help [Company] with [specific goal]", "[Your Name]",
                 "{{candidate_name}}"):
        assert rx.search(text), text
    for text in ("[+1 555 0100](tel:+15550100)",
                 "[my site](https://example.com)",
                 "![photo](x.png)",
                 # No keyword half any more: a plain word is not a marker.
                 "TBD",
                 "plain answer with no markers"):
        assert not rx.search(text), text


def test_exact_values_stay_fatal_but_inferences_are_dynamic():
    """A URL / email / phone the excerpts do not contain is an invention and
    stays fatal; anything else may be a correct INFERENCE (a count computed
    from dates, a paraphrase), so the grounding verdict is dynamic."""
    from .probe import _ff_exact_value_token
    assert _ff_exact_value_token("https://x/y")
    assert _ff_exact_value_token("fake@example.com")
    assert _ff_exact_value_token("+15550100")
    assert not _ff_exact_value_token("Francisco")
    assert not _ff_exact_value_token("2017")


def test_grounding_verdict_fails_closed_without_the_engine():
    """The dynamic verdict returns None (caller keeps its own rule) rather than
    raising or silently passing when Laya is unavailable."""
    from AI.laya_hooks import grounding_or
    assert grounding_or("a claim", "") is None
    assert grounding_or("", "some evidence") is None


def test_probe_keeps_an_inferred_composition_but_never_an_invented_value():
    """The composed answer is DROPPED only for an exact-value term (or when the
    dynamic verdict fails closed) - an inferred one is kept."""
    from . import probe as probe_mod
    src = open(probe_mod.__file__, encoding="utf-8").read()
    assert "_fatal = [t for t in _viol if _ff_exact_value_token(t)]" in src
    assert "_ff_synth_supported(_synth, _excerpts) is True" in src


def test_option_exact_is_not_the_lenient_option_match():
    """A combo/select can only HOLD one of its own options, so 'is this value
    already an answer' must be EXACT - the lenient containment of
    ``_ff_option_match`` would call a typed 'riverside' a match for the option
    'Riverside, Illinois, United States' and leave the field stuck."""
    opts = ["Riverside, Illinois, United States", "Bristol, England"]
    assert _ff_option_exact("Bristol, England", opts)
    assert _ff_option_exact("bristol,   england", opts)     # normalized
    assert not _ff_option_exact("riverside", opts)          # a fragment
    assert _ff_option_exact("riverside", ["riverside"])    # a real option
    assert not _ff_option_exact("", opts)
    assert not _ff_option_exact("anything", [])
    # The lenient matcher disagrees on purpose - that is the point.
    assert _ff_option_match("riverside", opts)


def test_filter_include_and_skip():
    h = _Harness()
    cfg = h._ff_cfg({"fields_include": "email, name", "fields_skip": "name"})
    fields = [
        {"id": "a", "label": "Email address"},
        {"id": "b", "label": "Full name"},
        {"id": "c", "label": "Phone"},
    ]
    out = h._ff_filter(fields, cfg)
    assert [f["id"] for f in out] == ["a"]


def test_split_list_and_strip_think():
    assert _split_list("a, b ,,") == ["a", "b"]
    assert _strip_think(" thinking\nreasoning\n<｜end▁of▁thinking｜>Hello world") == "Hello world"
    assert _strip_think("```\nvalue\n```") == "value"


def test_selectors_ladder_prefers_identity_then_label():
    h = _Harness()
    sels = h._ff_selectors({
        "id": "email", "name": "email", "label": "Email",
        "tag": "input", "type": "text",
    })
    assert sels[0] == '[id="email"]'
    assert '[name="email"]' in sels
    assert any(s.startswith("//label[") and "following::input" in s for s in sels)
    assert sels[-1] == "input"


def test_a_generated_id_is_an_attribute_selector_not_a_css_identifier():
    """React 19's useId emits ids like '«r54»'.  '#«r54»' is an INVALID CSS
    selector, so querySelectorAll threw, the candidate resolved nothing, and the
    field became unwritable (live: 'target unresolved or not editable ...
    selectors=['#«r54»', ...]').  A quoted attribute selector accepts any
    character."""
    h = _Harness()
    sels = h._ff_selectors({"id": "«r54»", "tag": "input", "type": "text"})
    assert sels[0] == '[id="«r54»"]'
    assert not any(s.startswith("#") for s in sels)


def test_merge_choices_groups_radios_by_question():
    h = _Harness()
    q = "Have you worked in a client-facing role?"
    fields = [
        {"id": "r1", "label": "Yes", "kind": "choice", "type": "radio",
         "name": "worked", "question": q, "group": "n:worked",
         "index": 0, "type_index": 0},
        {"id": "r2", "label": "No", "kind": "choice", "type": "radio",
         "name": "worked", "question": q, "group": "n:worked",
         "index": 1, "type_index": 1},
    ]
    out = h._ff_merge_choices(fields)
    assert len(out) == 1
    f = out[0]
    assert f["kind"] == "choice"
    assert f["label"] == q
    assert f["options"] == ["Yes", "No"]
    assert f["selectors"][0] == 'input[name="worked"]'
    assert h._ff_field_selectors(f) == f["selectors"]


def test_placeholder_prefilled_value_is_not_an_answer():
    """A pre-filled value is only an answer when the PAGE says so: the field is
    EMPTY, or it holds exactly its own ``placeholder`` text.  Which option WORD
    means "not an answer" is the page's own convention (an empty-valued or
    disabled option is dropped by the enumerator) - never a word list."""
    assert _ff_placeholder_value({"label": "City"}, "")           # empty
    # The field's OWN placeholder attribute.
    assert _ff_placeholder_value({"placeholder": "Enter your city"},
                                 "Enter your city")
    # A REAL value is left alone, in any language - including one that merely
    # READS like an instruction (this used to be guessed from a word list).
    assert not _ff_placeholder_value({"label": "Role"},
                                    "Select Committee Member")
    assert not _ff_placeholder_value({"label": "País"},
                                     "Selecciona una opción")
    assert not _ff_placeholder_value({"label": "Email"}, "a@b.com")
    assert not _ff_placeholder_value({"label": "Years"}, "6")


def test_merge_choices_keeps_non_choice_fields():
    h = _Harness()
    fields = [{"id": "f0", "label": "Email", "kind": "text"}]
    assert h._ff_merge_choices(fields) == fields


def test_skip_sentinel_is_the_one_literal_the_prompt_names():
    h = _Harness()
    cfg = h._ff_cfg({"instruction": "fill"})

    def _extract(raw, c=None):
        h._ff_llm_call = lambda *a, **k: raw
        return h._ff_extract("First name", "ev", c or cfg, None)

    for raw in ("SKIP", "[Skip]", "[SKIP]", "skip.", "Skip:\n",
                "Skip \n\nThe context does not contain it.",
                "[Skip] The provided context does not contain any information"):
        assert _extract(raw) == "", raw

    # A word that merely resembles a sentinel is a VALUE now - the harness
    # never invents a synonym list for it.
    assert _extract("none") == "none"
    assert _extract("unknown") == "unknown"
    assert _extract("Alex") == "Alex"
    assert _extract("Skipper") == "Skipper"  # a real value, not a sentinel
    # N/A is the UNANSWERABLE verdict - a value the page RECEIVES, not the
    # silent skip the harness uses for a field it leaves alone...
    assert _extract("N/A") == "N/A"
    # ...and it stays a plain skip when the last-resort policy is off.
    off = h._ff_cfg({"instruction": "fill", "answer_na": False})
    assert _extract("n/a", off) == ""


def test_doc_head_is_read_bounded_and_cached(tmp_path):
    ff = _Harness()
    p = tmp_path / "d.txt"
    p.write_text("Alex Doe\nalex@example.com\n" + "x" * 500,
                 encoding="utf-8")
    head = ff._ff_doc_head([str(p)], 40)
    assert head.startswith("Alex Doe")
    assert len(head) == 40
    assert ff._ff_doc_head([str(p)], 40) == head  # cached
    # Missing file / no documents -> empty (no crash).
    assert ff._ff_doc_head([str(tmp_path / "nope.txt")], 40) == ""
    assert ff._ff_doc_head([], 40) == ""


def test_filter_matches_skip_against_name_id_and_placeholder():
    """A date input's visible question often lives outside the control, so a
    skip token must match name/id/placeholder - not just the label."""
    h = _Harness()
    cfg = h._ff_cfg({"fields_skip": "date"})
    fields = [
        {"id": "a", "label": "", "name": "start_date", "tag": "input"},
        {"id": "b", "label": "", "placeholder": "Date (MM/DD/YYYY)"},
        {"id": "c", "label": "Full name"},
    ]
    out = h._ff_filter(fields, cfg)
    assert [f["id"] for f in out] == ["c"]
    # The visible question text (question/aria_label) counts too.
    cfg2 = h._ff_cfg({"fields_skip": "earliest"})
    fields2 = h._ff_fields = [
        {"id": "d", "label": "", "question": "Earliest start date?"},
        {"id": "e", "label": "Desired salary"},
    ]
    assert [f["id"] for f in h._ff_filter(fields2, cfg2)] == ["e"]


def test_cfg_default_max_tokens_is_1024():
    h = _Harness()
    assert h._ff_cfg({})["max_tokens"] == 1024


def test_strip_think_handles_dangling_close_and_answer_block():
    # A reasoning model whose template injects the OPENING tag emits reasoning
    # then a standalone </think> before the answer.
    assert _strip_think("reasoning about yes or no\n</think>\nThe answer") == "The answer"
    assert _strip_think("<answer>Final</answer>") == "Final"
    assert _strip_think("[thinking]x[/thinking]Out") == "Out"
    assert _strip_think("   \nJust a value") == "Just a value"


def test_fragment_guard_flags_only_longer_word_occurrences():
    """A value that occurs in the source ONLY inside a longer word is a
    truncation ('iversid' for 'Riverside', 'locat' for 'location'), never a
    copied value.  A value the source does not contain at all is left alone, so
    a legitimate DERIVED answer is never blocked."""
    ev = "my location is Riverside and I worked in Austin"
    assert _ff_fragment_of_evidence("iversid", ev)   # inside 'Riverside'
    assert _ff_fragment_of_evidence("locat", ev)     # prefix of 'location'
    assert _ff_fragment_of_evidence("aust", ev)      # prefix of 'Austin'
    assert not _ff_fragment_of_evidence("Riverside", ev)   # the whole word
    assert not _ff_fragment_of_evidence("Austin", ev)
    assert not _ff_fragment_of_evidence("Springfield", ev)  # absent entirely
    assert not _ff_fragment_of_evidence("ab", ev)    # too short to judge


def test_format_spec_covers_native_input_types():
    h = _Harness()
    assert "YYYY-MM-DD" in h._ff_format_spec({"type": "date"})
    assert "digits only" in h._ff_format_spec({"type": "number"})
    assert "user@example.com" in h._ff_format_spec({"type": "email"})
    assert "https://" in h._ff_format_spec({"type": "url"})
    assert h._ff_format_spec({"type": "text"}) == ""
    assert "pattern" in h._ff_format_spec({"type": "text", "pattern": r"\d{5}"})


def test_format_hint_summarises_the_constraints():
    h = _Harness()
    hint = h._ff_format_hint(
        {"type": "date", "pattern": "", "required": True},
        {"message": "Please enter a valid date.", "value": "2017"})
    assert "Please enter a valid date." in hint
    assert "expects a date as YYYY-MM-DD" in hint
    assert "required" in hint
    assert "'2017' was NOT accepted" in hint      # names the rejected value
    assert "DIFFERENT value" in hint              # must not repeat it
    hint2 = h._ff_format_hint({"type": "text", "pattern": r"\d{5}"})
    assert "must match the pattern" in hint2
    assert h._ff_format_hint({}).startswith("Your previous answer was rejected")


def test_option_match_is_exact_or_the_short_label():
    opts = ["A1 (Beginner): Can understand and use familiar everyday "
            "expressions for basic needs.",
            "B2 (Upper-Intermediate): Can fulfill most goals and express "
            "ideas on various topics."]
    assert _ff_option_match("B2", opts) == opts[1]         # short label
    assert _ff_option_match("  b2 (upper-intermediate) ", opts) == opts[1]
    assert _ff_option_match(opts[0], opts) == opts[0]      # verbatim option
    assert _ff_option_match("Yes, I agree", ["Yes", "No"]) == "Yes"
    # A near miss must NEVER become a wrong selection: the source's own phrase
    # for a level the list does not spell that way snaps to nothing.
    assert _ff_option_match("Full professional proficiency", opts) == ""
    assert _ff_option_match("PURPLE", ["Red", "Green", "Blue"]) == ""
    # A select's prompt entry is dropped by the ENUMERATOR (an option with an
    # empty value attribute, or a disabled one), so no word list stands in for
    # it here - a containment hit is a hit.
    assert _ff_option_match("Select", ["-- Select --", "Yes"]) == "-- Select --"
    assert _ff_option_match(
        "resume_alex.pdf", ["Select resume resume_alex.pdf"]
    ) == "Select resume resume_alex.pdf"


def test_merged_choice_ladder_anchors_each_option():
    """A name-less choice group must resolve EVERY option: the ladder is the
    union of each option's own anchor, with the generic sweep dropped (live:
    selectors=['input', ''] could never click the answer - 'target unresolved')."""
    h = _Harness()
    fields = [
        {"id": "", "label": "Yes", "kind": "choice", "type": "radio",
         "name": "", "question": "Q", "group": "c:0", "index": 0,
         "type_index": 0, "tag": "input"},
        {"id": "", "label": "No", "kind": "choice", "type": "radio",
         "name": "", "question": "Q", "group": "c:0", "index": 1,
         "type_index": 1, "tag": "input"},
    ]
    g = h._ff_merge_choices(fields)[0]
    sels = g["selectors"]
    assert len(sels) == 2                       # one anchor per option
    assert all("following::input" in s for s in sels)
    assert "input" not in sels                  # no generic sweep
    assert 'input[type="radio"]' not in sels
    assert h._ff_field_selectors(g) == sels


def test_specific_selectors_drop_generic_sweeps():
    h = _Harness()
    assert h._ff_specific_selectors(
        ["input", 'input[type="radio"]', "", "#a", '[name="b"]',
         "//label[normalize-space(.)='Yes']/following::input[1]"]
    ) == ["#a", '[name="b"]',
          "//label[normalize-space(.)='Yes']/following::input[1]"]


def test_role_widget_gets_a_role_text_anchor():
    """A <div role="radio">Yes</div> has no following <input>, so it needs its
    own role+text anchor or the group can never be clicked."""
    h = _Harness()
    sels = h._ff_selectors({"kind": "choice", "tag": "div", "type": "radio",
                            "label": "Yes", "id": "", "name": "",
                            "placeholder": "", "aria_label": ""})
    assert any("@role" in s and "Yes" in s for s in sels)
