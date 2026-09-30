"""The opt-in ask-user fallback: fill from the user, learn into the file."""

import json

from ._test_support import (
    _Harness,
    _node,
    _wire,
)


def test_ask_user_fills_and_learns_into_the_node_owned_file(tmp_path):
    """Nothing grounded the field -> the user is asked, their answer fills it,
    and it is appended to the corrections file the NODE owns (no path set)."""
    h = _Harness()
    h.sequence_executor = type("S", (), {"chain_file_dir": str(tmp_path)})()
    h.set_variable("_ask_user_callback", lambda q: "AnswerValue")
    _wire(h, fields=[{"id": "f0", "label": "Have you worked before?"}],
          evidence="")

    h._execute_form_filling_node(_node(ask_user=True, answer_no=False), None)

    assert h.writes == [("f0", "AnswerValue")]
    summary = json.loads(h.get_variable("node_ff1_output"))
    assert summary["filled"] == 1 and summary["skipped"] == 0
    learned = (tmp_path / "ff1_corrections.md").read_text(encoding="utf-8")
    assert "Have you worked before?" in learned
    assert "AnswerValue" in learned


def test_ask_user_off_keeps_the_skip_behaviour():
    """The toggle is the ONLY gate: off (the default) asks nobody - the field
    gets the last-resort N/A, never the callback's answer."""
    h = _Harness()
    h.set_variable("_ask_user_callback", lambda q: "AnswerValue")
    _wire(h, fields=[{"id": "f0", "label": "Email"}], evidence="",
          llm_value="N/A", reads=["N/A"])

    h._execute_form_filling_node(_node(), None)

    assert h.writes == [("f0", "N/A")]
    summary = json.loads(h.get_variable("node_ff1_output"))
    assert summary["answered_na"] == 1 and summary["skipped"] == 0


def test_learned_answer_reaches_later_fields(tmp_path):
    """The answer learned for one field is readable by the NEXT field's probe,
    so a later field grounded by it is never asked."""
    h = _Harness()
    h.sequence_executor = type("S", (), {"chain_file_dir": str(tmp_path)})()
    h.set_variable("_ask_user_callback", lambda q: "UserAnswer")
    fields = [
        {"id": "f0", "label": "Nickname"},
        {"id": "f1", "label": "Email"},
    ]
    _wire(h, fields=fields, value="a@b.com", llm_value="a@b.com")
    seen = {}

    def _probe(label, cfg, src, docs=None, stop_flag=None, repair_hint=""):
        seen[label] = src
        return "" if label == "Nickname" else "the learned note"
    h._ff_probe = _probe

    h._execute_form_filling_node(_node(ask_user=True, answer_no=False), None)

    assert "Nickname" in seen["Email"]
    assert "UserAnswer" in seen["Email"]


def test_corrections_path_is_derived_and_never_blank(tmp_path):
    """There is no user-set path: the node derives one beside the chain, and
    with no chain directory at all it still resolves one (the runtime dir)."""
    h = _Harness()
    h.sequence_executor = type("S", (), {"chain_file_dir": str(tmp_path)})()
    assert h._ff_corrections_path("ff1") == str(tmp_path / "ff1_corrections.md")
    h.sequence_executor = None
    assert h._ff_corrections_path("ff1").endswith("ff1_corrections.md")


def test_corrections_file_is_owned_by_the_node(tmp_path):
    """A field nothing grounds is asked, and the answer lands in the file the
    NODE owns beside the chain - no path the user had to point at."""
    h = _Harness()
    h.sequence_executor = type("S", (), {"chain_file_dir": str(tmp_path)})()
    h.set_variable("_ask_user_callback", lambda q: "https://so.example/u/1")
    _wire(h, fields=[{"id": "f0", "label": "StackOverflow profile"}],
          evidence="")

    h._execute_form_filling_node(_node(ask_user=True, answer_no=False), None)

    owned = tmp_path / "ff1_corrections.md"
    assert owned.exists()
    text = owned.read_text(encoding="utf-8")
    assert "StackOverflow profile" in text
    assert "https://so.example/u/1" in text
    assert h.writes == [("f0", "https://so.example/u/1")]


def test_ask_user_is_preferred_over_writing_n_a():
    """The model's unanswerable verdict (N/A) is owned by the user: with
    ask_user on the question is asked BEFORE the last-resort N/A is written,
    so the last resort never beats an answer the user can give."""
    h = _Harness()
    h.set_variable("_ask_user_callback", lambda q: "https://so.example/u/1")
    _wire(h, fields=[{"id": "f0", "label": "StackOverflow profile"}],
          evidence="The candidate builds automation tools.",
          llm_value="N/A", value="https://so.example/u/1")

    h._execute_form_filling_node(_node(ask_user=True), None)

    assert h.writes == [("f0", "https://so.example/u/1")]
    summary = json.loads(h.get_variable("node_ff1_output"))
    assert summary["answered_na"] == 0 and summary["filled"] == 1


def test_ask_uses_the_controls_format_and_the_pages_own_options():
    """A form asks in several shapes, so the prompt must match the CONTROL: a
    list-backed field OFFERS its own options, and a numbered reply resolves to
    the page's own option text (never the raw number)."""
    h = _Harness()
    seen = {}
    h.set_variable("_ask_user_v2_callback",
                   lambda req: seen.update(req) or {"value": "2"})
    _wire(h, fields=[{"id": "f0", "label": "Country", "kind": "select",
                      "options": ["Ireland", "Spain", "Norway"]}],
          evidence="", value="Spain")

    h._execute_form_filling_node(_node(ask_user=True, answer_no=False), None)

    assert seen["kind"] == "choice"
    assert [c["value"] for c in seen["choices"]] == [
        "Ireland", "Spain", "Norway"]
    assert h.writes == [("f0", "Spain")]


def test_ask_renders_a_switch_as_yes_no_and_a_free_field_as_text():
    """A toggle is a yes/no question and everything else is free text - the
    prompt matches what the page can hold."""
    h = _Harness()
    seen = {}
    h.set_variable("_ask_user_v2_callback",
                   lambda req: seen.update(req) or {"value": "yes"})
    _wire(h, fields=[{"id": "f0", "label": "Authorized to work?",
                      "kind": "switch"}], evidence="", value="yes")
    h._execute_form_filling_node(_node(ask_user=True, answer_no=False), None)
    assert seen["kind"] == "yes_no" and seen["choices"] == []
    assert h.writes == [("f0", "yes")]

    h2 = _Harness()
    seen2 = {}
    h2.set_variable("_ask_user_v2_callback",
                    lambda req: seen2.update(req) or {"value": "a@b.com"})
    _wire(h2, fields=[{"id": "f0", "label": "Email"}], evidence="",
          value="a@b.com")
    h2._execute_form_filling_node(_node(ask_user=True, answer_no=False), None)
    assert seen2["kind"] == "text" and seen2["choices"] == []
    assert h2.writes == [("f0", "a@b.com")]


def test_ask_unwraps_a_v2_pair_reply():
    """An ask channel can hand back the v2 PAIR (value, attachments) - the app's
    rich callback is wired to the function ``ask_question`` returns - and
    stringifying it wrote the literal '(None, [])' into the field (live:
    ``Value (None, [])``, ``Status failed``)."""
    h = _Harness()
    h.set_variable("_ask_user_v2_callback", lambda req: ("Ireland", []))
    _wire(h, fields=[{"id": "f0", "label": "Country", "kind": "select",
                      "options": ["Ireland", "Spain"]}], evidence="",
          value="Ireland")

    h._execute_form_filling_node(_node(ask_user=True, answer_no=False), None)

    assert h.writes == [("f0", "Ireland")]

    # A CANCELLED pair (None, []) means no answer - never that literal.
    h2 = _Harness()
    h2.set_variable("_ask_user_v2_callback", lambda req: (None, []))
    _wire(h2, fields=[{"id": "f0", "label": "Country", "kind": "select",
                       "options": ["Ireland", "Spain"]}], evidence="")
    h2._execute_form_filling_node(_node(ask_user=True), None)
    assert not any("None, []" in str(v) for _i, v in h2.writes)


def test_a_learned_answer_is_reused_and_never_asked_again():
    """A field the user answered ONCE is resolved from the node's own
    corrections file on the NEXT pass - retrieval is skipped, the model is not
    consulted, and the user is NOT asked again.  Live: 'Location (city)*' was
    asked twice, hours apart in one chain, because the model re-derived a lossy
    'Ezeiza' from the stored 'Ezeiza, Buenos Aires Province, Argentina' and no
    option won the choice rail."""
    h = _Harness()
    with open(h._ff_corrections_path("ff1"), "w", encoding="utf-8") as fh:
        fh.write("## Location (city)*\n"
                 "Ezeiza, Buenos Aires Province, Argentina\n")
    asked = []
    h.set_variable("_ask_user_callback", lambda q: asked.append(q) or "x")
    _wire(h, fields=[{"id": "f0", "label": "Location (city)*",
                      "kind": "combo",
                      "options": ["Ezeiza, Buenos Aires Province, Argentina",
                                  "Partido de Ezeiza, Buenos Aires Province, "
                                  "Argentina"]}],
          evidence="Buenos Aires, Argentina",
          reads=["Ezeiza, Buenos Aires Province, Argentina"])

    h._execute_form_filling_node(_node(ask_user=True), None)

    assert h.writes == [("f0", "Ezeiza, Buenos Aires Province, Argentina")]
    assert h.probes == []        # the learned answer skipped retrieval ...
    assert h.llm_calls == []     # ... and the model was never consulted ...
    assert asked == []           # ... so the user was NOT asked again


def test_a_learned_answer_the_control_cannot_hold_falls_through():
    """A stored answer the page's option list does not offer is NOT written:
    the field falls through to normal retrieval, never to an off-list value."""
    h = _Harness()
    with open(h._ff_corrections_path("ff1"), "w", encoding="utf-8") as fh:
        fh.write("## Country\nAtlantis\n")
    _wire(h, fields=[{"id": "f0", "label": "Country", "kind": "select",
                      "options": ["Ireland", "Spain"]}],
          evidence="The candidate lives in Spain.", llm_value="Spain",
          reads=["Spain"])

    h._execute_form_filling_node(_node(), None)

    assert h.writes == [("f0", "Spain")]
    assert h.probes == ["Country"]   # the off-list stored answer did not shortcut


def test_a_learned_choice_record_is_mapped_by_laya(monkeypatch):
    """A choice record that is NOT the option verbatim is mapped onto the
    page's OWN option by LAYA - the model is bypassed and the user is not asked.
    The record is the 'approx option we want to click' handed to the picker."""
    import AI.laya_hooks as hooks
    monkeypatch.setattr(hooks, "choose_option", lambda q, a, o: (1, True))
    h = _Harness()
    with open(h._ff_corrections_path("ff1"), "w", encoding="utf-8") as fh:
        fh.write("## Are you open to relocation?*\nSure, I would move\n")
    asked = []
    h.set_variable("_ask_user_callback", lambda q: asked.append(q) or "x")
    _wire(h, fields=[{"id": "f0", "label": "Are you open to relocation?*",
                      "kind": "combo",
                      "options": ["Yes, I am open to relocation",
                                  "Not open to relocation"]}],
          evidence="", reads=["Not open to relocation"])

    h._execute_form_filling_node(_node(ask_user=True), None)

    assert h.writes == [("f0", "Not open to relocation")]
    assert h.probes == [] and h.llm_calls == [] and asked == []


def test_an_unpinned_record_is_decided_without_asking(monkeypatch):
    """A record that pins NO option goes through the usual pipeline with the
    record as its deciding context - and the user is NOT asked again."""
    import AI.laya_hooks as hooks
    monkeypatch.setattr(hooks, "choose_option", lambda q, a, o: (None, True))
    h = _Harness()
    with open(h._ff_corrections_path("ff1"), "w", encoding="utf-8") as fh:
        fh.write("## Country\nWhere I was born\n")
    asked = []
    h.set_variable("_ask_user_callback", lambda q: asked.append(q) or "x")
    _wire(h, fields=[{"id": "f0", "label": "Country", "kind": "select",
                      "options": ["Ireland", "Spain"]}],
          evidence="", value="Spain", llm_value="Spain", reads=["Spain"])

    h._execute_form_filling_node(_node(ask_user=True), None)

    assert asked == []                 # the record decided, not the user
    assert h.probes == ["Country"]     # the usual pipeline still ran
    assert h.writes == [("f0", "Spain")]
