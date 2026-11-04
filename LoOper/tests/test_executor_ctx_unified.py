"""Context INPUT port payload tests for the LLM executor's consolidation gather.

Guards the strict port semantics: only upstreams connected to the node's
context INPUT port enter ``context_nodes``.  Data rides NAMED source ports
(``context``/``ctx_out`` read ``node_<id>_context``; ``data`` reads
``node_<id>_data``).  Base/branch source ports (``output``, ``true`` ...)
drive execution only — they carry no data.
"""

from player.multi_sequence.llm_executor_resources import executor as ex
from player.multi_sequence.worflow_interpreter_modules.executor_modules.\
    context_ops import ContextMixin, _ctx_row_key


def test_a_pool_row_is_keyed_by_identity_never_by_a_description():
    """A node's pool row is keyed by its type + id.  The DESCRIPTION is
    workspace-agent text about the node, not source material: keying rows by
    it put it in the pool every consumer reads, and the form filler's intent
    planner then wrote probes about "the answers written by Qwen3.8-2B-Q8_0.gguf"
    instead of about the field it was filling."""
    assert _ctx_row_key("form_filler", "n7") == "form_filler/n7"
    assert _ctx_row_key("llm", "0x1abb69fffd0") == "llm/0x1abb69fffd0"
    # Nothing to name -> the type alone, still no free text.
    assert _ctx_row_key("", "") == "unknown"
    assert _ctx_row_key("context", None) == "context"


def test_context_port_payload_ignores_base_output():
    # A base 'output' source carries no data (execution only).
    variables = {"node_b_output": "<html><body>page text from a web sequence</body></html>"}
    assert ex._context_port_payload(variables, "b") == ""


def test_context_port_payload_reads_context_port():
    variables = {
        "node_c_context": "context node value",
        "node_c_output": "ignored output",
    }
    assert ex._context_port_payload(variables, "c", "context") == "context node value"


def test_context_port_payload_empty_when_nothing_stored():
    assert ex._context_port_payload({}, "missing") == ""


def test_context_port_payload_keeps_context_var_verbatim():
    # A stored node_<id>_context is used as-is (no re-shaping).
    variables = {"node_d_context": '{"user_input": "q", "response": "a"}'}
    assert ex._context_port_payload(variables, "d", "ctx_out") == variables["node_d_context"]


# ---------------------------------------------------------------------------
# Turn records: one turn reports ITS OWN tools, with an outcome
# ---------------------------------------------------------------------------


def test_already_reported_filters_earlier_turns():
    # A record outlives its turn, so turn 2 used to re-list turn 1's tool
    # (observed: "Tool executed: CLOSE …" on a turn that only ran gmail).
    assert ContextMixin._already_reported({"seq": 1}, 1) is True
    assert ContextMixin._already_reported({"seq": 2}, 1) is False
    # No mark, no filtering: an unstamped record is never dropped.
    assert ContextMixin._already_reported({"seq": 0}, 5) is False
    assert ContextMixin._already_reported(None, 5) is False


def test_tool_outcome_names_what_the_tool_did():
    # A recorded-action tool has no result slot: "it ran" is the honest
    # outcome, and its absence is what made the summariser recite the tool's
    # DESCRIPTION as if it were a report.
    assert ContextMixin._tool_outcome(
        {"last_node": {"type": "code", "result": "42"}}) == "42"
    assert ContextMixin._tool_outcome(
        {"last_node": {"type": "llm",
                       "result": {"query": "q", "response": "done"}}}) == "done"
    assert ContextMixin._tool_outcome(
        {"last_node": {"type": "sequence"}}) == "ran successfully (sequence)"
    assert ContextMixin._tool_outcome({"last_node": {}}) == ""
    assert ContextMixin._tool_outcome(None) == ""


def test_tool_outcome_prefers_the_tools_return_value():
    # Output nodes are the tool's return value: every one the chain ran wins
    # over the last-node summary, which only describes the final step.
    assert ContextMixin._tool_outcome(
        {"tool_output": "Summary: HELLO", "last_node": {"type": "sequence"}}
    ) == "Summary: HELLO"


def test_turn_text_reads_as_the_agents_own_action():
    text = ContextMixin._format_turn({
        "turn": 2, "query": "open gmail",
        "tool": "gmail: this tool opens gmail",
        "result": "ran successfully (sequence)",
    })
    assert "User query: open gmail" in text
    assert "Tool I ran: gmail" in text
    assert "What it did: ran successfully (sequence)" in text


# ---------------------------------------------------------------------------
# ONE QUESTION = ONE ANSWER: the context node LINKS a reworded question
# ---------------------------------------------------------------------------


def test_a_reworded_question_is_linked_not_written_again(monkeypatch):
    """The store keys answers by the field label, so the same question in two
    wordings became two records with two answers.  A reworded question is now
    resolved to the record it duplicates (an ``alias/`` link) instead of being
    written again - and the verdict is Laya's, not a lexical score's."""
    from AI import laya_hooks

    class _Ctx(ContextMixin):
        pass

    ctx = _Ctx()
    stored = "learned/how many years of experience do you have with aws"
    existing = {stored: "6"}
    reworded = "how much experience you have with cloud providers like aws?"

    monkeypatch.setattr(laya_hooks, "same_question_or",
                        lambda label, cands, fallback=None, agree=None: cands[0])
    assert ctx._ff_dupe_canonical(reworded, existing) == stored

    # The engine answering NONE keeps them apart (two real questions).
    monkeypatch.setattr(laya_hooks, "same_question_or",
                        lambda label, cands, fallback=None, agree=None: "")
    assert ctx._ff_dupe_canonical(reworded, existing) == ""

    # A verified record outranks the auto one for the same question.
    existing["verified/%s" % "how many years of experience do you have with aws"] = "7"
    monkeypatch.setattr(laya_hooks, "same_question_or",
                        lambda label, cands, fallback=None, agree=None: cands[0])
    assert ctx._ff_dupe_canonical(reworded, existing).startswith("verified/")


def test_a_question_asked_in_another_language_is_linked(monkeypatch):
    """Same question, other language, same answer: ONE record.

    A translation shares no word with the stored label, so the lexical prefilter
    never offered the record and the answer was stored twice, once per
    language.  A record that already holds the SAME answer is a candidate, and
    the engine's undecided band (where a translation scores) is lifted for it.
    """
    from AI import laya_hooks

    class _Ctx(ContextMixin):
        pass

    ctx = _Ctx()
    stored = "learned/please provide your salary expectations in usd in a monthly basis"
    existing = {stored: "6000"}
    spanish = "cual es tu remuneracion bruta pretendida para el rol?"

    # Measured shape of a translated match: undecided band (0.62-0.73).
    monkeypatch.setattr(laya_hooks, "same_question_scores",
                        lambda label, cands: [(c, 0.68) for c in cands])
    assert ctx._ff_dupe_canonical(spanish, existing, "6000") == stored

    # A DIFFERENT answer in the same band is not merged (that would fill one
    # field with the other's value) - both records are kept.
    assert ctx._ff_dupe_canonical(spanish, existing, "5000") == ""

    # Engine unavailable -> no dedupe at all, never a guess.
    monkeypatch.setattr(laya_hooks, "same_question_scores",
                        lambda label, cands: None)
    assert ctx._ff_dupe_canonical(spanish, existing, "6000") == ""
