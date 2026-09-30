"""Context INPUT port payload tests for the LLM executor's consolidation gather.

Guards the strict port semantics: only upstreams connected to the node's
context INPUT port enter ``context_nodes``.  Data rides NAMED source ports
(``context``/``ctx_out`` read ``node_<id>_context``; ``data`` reads
``node_<id>_data``).  Base/branch source ports (``output``, ``true`` ...)
drive execution only — they carry no data.
"""

from player.multi_sequence.llm_executor_resources import executor as ex
from player.multi_sequence.worflow_interpreter_modules.executor_modules.\
    context_ops import ContextMixin


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
