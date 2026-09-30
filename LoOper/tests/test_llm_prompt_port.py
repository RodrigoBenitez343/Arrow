"""LLM node 'prompt' port semantics.

The executor REPLACES the node's prompt template with the text wired to the
'prompt' INPUT port (the system message is untouched), so the prompt stays a
prompt instead of being folded into context.  Only 'prompt'-port edges are
read; input/context/tools edges are ignored.

Run:  python -m pytest LoOper/tests/test_llm_prompt_port.py
"""

from player.multi_sequence.llm_executor_resources.inputs import _prompt_port_input


def test_prompt_port_reads_a_data_edge():
    node = {
        "inputs": [
            {"from_node": "in1", "input_port": "prompt", "output_type": "data"},
        ]
    }
    variables = {"node_in1_data": "summarize this page"}
    assert _prompt_port_input(node, variables) == "summarize this page"


def test_prompt_port_joins_multiple_sources():
    node = {
        "inputs": [
            {"from_node": "a", "input_port": "prompt", "output_type": "data"},
            {"from_node": "b", "input_port": "prompt", "output_type": "data"},
        ]
    }
    variables = {"node_a_data": "part one", "node_b_data": "part two"}
    assert _prompt_port_input(node, variables) == "part one\n\npart two"


def test_prompt_port_ignores_other_ports():
    node = {
        "inputs": [
            {"from_node": "ctx", "input_port": "context", "output_type": "context"},
            {"from_node": "in1", "input_port": "input", "output_type": "data"},
            {"from_node": "t1", "input_port": "tools", "output_type": "output"},
        ]
    }
    variables = {
        "node_ctx_context": "history",
        "node_in1_data": "user text",
        "node_t1_output": "tool",
    }
    assert _prompt_port_input(node, variables) == ""


def test_prompt_port_empty_when_unwired_or_blank():
    assert _prompt_port_input({"inputs": []}, {}) == ""
    node = {
        "inputs": [
            {"from_node": "in1", "input_port": "prompt", "output_type": "data"},
        ]
    }
    assert _prompt_port_input(node, {"node_in1_data": "   "}) == ""
