"""Unit tests for player/multi_sequence/worflow_interpreter_modules/builder.py —
WorkflowGraphBuilder integrity: id disambiguation, dangling targets, node
indexing, and topology construction.
"""

import pytest

from player.multi_sequence.worflow_interpreter_modules.builder import WorkflowGraphBuilder


def _seq_node(node_id, outputs=None, inputs=None):
    node = {
        "name": f"{node_id}.json",
        "sequence_file": f"{node_id}.json",
        "node_id": node_id,
        "connections": [{"output_port": "output", "target_node_id": t, "input_port": "input"}
                        for t in (outputs or [])],
    }
    if inputs is not None:
        node["inputs"] = inputs
    return node


# ---------------------------------------------------------------------------
# Basic construction
# ---------------------------------------------------------------------------


def test_build_simple_linear_graph():
    builder = WorkflowGraphBuilder(sequences=[
        _seq_node("a", outputs=["b"]),
        _seq_node("b", outputs=["c"]),
        _seq_node("c"),
    ])
    graph = builder.build_workflow_graph()
    assert set(graph) == {"a", "b", "c"}
    assert graph["a"]["connections"]["output"][0]["node_id"] == "b"
    assert graph["c"]["connections"]["output"] == []


def test_build_empty_sequences():
    builder = WorkflowGraphBuilder(sequences=[])
    graph = builder.build_workflow_graph()
    assert graph == {}


# ---------------------------------------------------------------------------
# Id integrity (b7 from execution_skip_reliability spec)
# ---------------------------------------------------------------------------


def test_duplicate_ids_disambiguated():
    seqs = [
        _seq_node("dup"),
        _seq_node("dup"),
        _seq_node(""),
    ]
    builder = WorkflowGraphBuilder(sequences=seqs)
    graph = builder.build_workflow_graph()
    assert "dup" in graph
    assert "dup#2" in graph  # duplicate gets a suffix, never overwritten
    assert len(graph) == 3  # no node silently lost
    assert seqs[1]["node_id"] == "dup#2"  # resolved id persisted


def test_missing_ids_get_fallback():
    seqs = [
        {"name": "x.json", "sequence_file": "x.json", "connections": []},
        {"name": "y.json", "sequence_file": "y.json", "connections": []},
    ]
    builder = WorkflowGraphBuilder(sequences=seqs)
    graph = builder.build_workflow_graph()
    assert len(graph) == 2
    fallback_ids = [nid for nid in graph if nid.startswith("sequence_")]
    assert len(fallback_ids) == 2


def test_dangling_targets_logged_and_skipped():
    import logging

    import player.multi_sequence.worflow_interpreter_modules.builder as builder_mod

    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    logger = builder_mod.logger  # the module logs through player.config's logger
    logger.addHandler(Capture())

    seqs = [_seq_node("a", outputs=["ghost"])]
    builder = WorkflowGraphBuilder(sequences=seqs)
    graph = builder.build_workflow_graph()
    logger.removeHandler(Capture())
    assert "a" in graph
    assert "ghost" not in graph
    # The stale edge is dropped from the built node, never left dangling.
    assert graph["a"]["connections"]["output"] == []
    assert any("Dangling connection target" in r for r in records)


# ---------------------------------------------------------------------------
# Input wiring
# ---------------------------------------------------------------------------


def test_inputs_recorded_in_graph():
    """The builder derives a node's 'inputs' list from upstream output
    connections automatically."""
    seqs = [
        _seq_node("a", outputs=["b"]),
        _seq_node("b"),
    ]
    builder = WorkflowGraphBuilder(sequences=seqs)
    graph = builder.build_workflow_graph()
    assert graph["b"]["inputs"][0]["from_node"] == "a"
    assert graph["a"]["type"] == "sequence"


def test_graph_nodes_have_type_and_data():
    builder = WorkflowGraphBuilder(sequences=[_seq_node("a")])
    graph = builder.build_workflow_graph()
    node = graph["a"]
    assert node["type"] == "sequence"
    assert node["data"]["node_id"] == "a"
    assert node["data"]["sequence_file"] == "a.json"


def test_llm_nodes_included():
    builder = WorkflowGraphBuilder(sequences=[], llm_nodes=[{
        "node_id": "llm1",
        "name": "LLM",
        "connections": [],
    }])
    graph = builder.build_workflow_graph()
    assert "llm1" in graph
    assert graph["llm1"]["type"] == "llm"


def test_conditional_nodes_included():
    # The 'true' target exists so it is kept; the 'false' target is absent
    # from the graph so post-build validation drops it (WARNING, never fatal).
    builder = WorkflowGraphBuilder(sequences=[_seq_node("x")], conditional_nodes=[{
        "node_id": "cond1",
        "name": "Cond",
        "connections": [
            {"output_port": "true", "target_node_id": "x", "input_port": "input"},
            {"output_port": "false", "target_node_id": "ghost", "input_port": "input"},
        ],
    }])
    graph = builder.build_workflow_graph()
    assert "cond1" in graph
    assert graph["cond1"]["type"] == "conditional"
    assert graph["cond1"]["connections"]["true"][0]["node_id"] == "x"
    assert graph["cond1"]["connections"]["false"] == []


def test_web_ctx_out_to_code_inputs_recorded():
    """A web sequence ctx_out edge to a code node must surface in the code
    node's 'inputs' with the exact output_type/input_port pair so code_ops can
    resolve the raw payload from port_store."""
    web = {
        "node_id": "ws1",
        "session_file": "scrolldown.json",
        "connections": [
            {"output_port": "output", "target_node_id": "code1", "input_port": "input"},
            {"output_port": "ctx_out", "target_node_id": "code1", "input_port": "html"},
        ],
    }
    code = {"node_id": "code1", "code": "pass", "connections": []}
    builder = WorkflowGraphBuilder(sequences=[], web_sequences=[web], code_nodes=[code])
    graph = builder.build_workflow_graph()
    inputs = graph["code1"]["inputs"]
    assert {"from_node": "ws1", "output_type": "ctx_out", "input_port": "html"} in inputs
    assert {"from_node": "ws1", "output_type": "output", "input_port": "input"} in inputs
    assert len(inputs) == 2


def test_legacy_code_inputs_dict_gets_input_port():
    """Legacy code-node 'inputs' dict edges keep their meaning: the dict key
    becomes input_port so dispatch still routes to input/args/custom vars."""
    web = {"node_id": "ws1", "session_file": "a.json", "connections": []}
    code = {
        "node_id": "code1",
        "code": "pass",
        "connections": [],
        "inputs": {"html": {"node_id": "ws1", "output_name": "ctx_out"}},
    }
    builder = WorkflowGraphBuilder(sequences=[], web_sequences=[web], code_nodes=[code])
    graph = builder.build_workflow_graph()
    assert graph["code1"]["inputs"] == [
        {"from_node": "ws1", "output_type": "ctx_out", "input_port": "html"}
    ]


def test_duplicate_code_inputs_deduped_prefer_ported():
    """Producer-side and legacy registrations of the same edge collapse to one
    entry, keeping the one that names the input port."""
    web = {
        "node_id": "ws1",
        "session_file": "a.json",
        "connections": [
            {"output_port": "ctx_out", "target_node_id": "code1", "input_port": "html"},
        ],
    }
    code = {
        "node_id": "code1",
        "code": "pass",
        "connections": [],
        "inputs": {"html": {"node_id": "ws1", "output_name": "ctx_out"}},
    }
    builder = WorkflowGraphBuilder(sequences=[], web_sequences=[web], code_nodes=[code])
    graph = builder.build_workflow_graph()
    assert len(graph["code1"]["inputs"]) == 1
    assert graph["code1"]["inputs"][0] == {
        "from_node": "ws1", "output_type": "ctx_out", "input_port": "html"
    }


def test_output_nodes_included_and_flow_through():
    # Legacy standalone TTS nodes are migrated to audio Output nodes — they
    # must appear in the graph with output connections intact.
    builder = WorkflowGraphBuilder(sequences=[{
        "node_id": "seq1",
        "sequence_file": "seq1.json",
        "connections": [],
    }], output_nodes=[{
        "node_id": "audio1",
        "type": "output",
        "render_mode": "audio",
        "tts_enabled": True,
        "connections": [{"output_port": "output", "target_node_id": "seq1", "input_port": "input"}],
    }])
    graph = builder.build_workflow_graph()
    assert "audio1" in graph
    assert graph["audio1"]["type"] == "output"
    assert graph["audio1"]["connections"]["output"][0]["node_id"] == "seq1"
    assert graph["seq1"]["inputs"][0]["from_node"] == "audio1"


# ---------------------------------------------------------------------------
# Shared-context clones: foreign edges are expected, never failures
# ---------------------------------------------------------------------------


def test_shared_context_foreign_edges_dropped_silently():
    """A context clone created from another chain carries stored edges that
    reference source-chain ids.  Post-build validation drops them quietly
    (the cross-chain link is the runtime identity feed, not graph edges) and
    never logs a chain-failing ERROR."""
    import logging

    import player.multi_sequence.worflow_interpreter_modules.builder as builder_mod

    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append((record.levelno, record.getMessage()))

    logger = builder_mod.logger
    logger.addHandler(Capture())
    try:
        ctx = {
            "type": "context",
            "node_id": "ctx_clone",
            "scope": "local",
            "clear_on_finish": True,
            "shared_context_chain_file": "D:\\chains\\source_chain.json",
            "shared_context_node_id": "0xsrc",
            "connections": [
                {"output_port": "ctx_out", "target_node_id": "0xsrc", "input_port": "context"},
            ],
        }
        builder = WorkflowGraphBuilder(sequences=[], context_nodes=[ctx])
        graph = builder.build_workflow_graph()
    finally:
        logger.removeHandler(Capture())
    assert "ctx_clone" in graph
    # The foreign edge is dropped; no WARNING/ERROR escapes for shared clones
    # (only a DEBUG note).
    assert graph["ctx_clone"]["connections"]["ctx_out"] == []
    dangling = [m for lvl, m in records if "Dangling connection" in m]
    assert dangling  # the drop is still noted (debug level)
    assert all(lvl < logging.WARNING for lvl, m in records if "Dangling connection" in m)


# ---------------------------------------------------------------------------
# Input node data port: explicit value edges, base port drives execution
# ---------------------------------------------------------------------------


def test_input_node_data_port_split_and_inputs_recorded():
    """An Input node's connections split by output_port into 'output'
    (execution driver) and 'data' (explicit value delivery); data-port
    consumers get an inputs entry with output_type 'data'."""
    inode = {
        "type": "input",
        "node_id": "in1",
        "passthrough": True,
        "connections": [
            {"output_port": "output", "target_node_id": "llm1", "input_port": "input"},
            {"output_port": "data", "target_node_id": "llm2", "input_port": "input"},
        ],
    }
    llm = {"node_id": "llm1", "name": "LLM", "connections": []}
    llm2 = {"node_id": "llm2", "name": "LLM2", "connections": []}
    builder = WorkflowGraphBuilder(
        sequences=[], input_nodes=[inode], llm_nodes=[llm, llm2],
    )
    graph = builder.build_workflow_graph()
    assert set(graph["in1"]["connections"].keys()) == {"output", "data"}
    assert graph["in1"]["connections"]["output"][0]["node_id"] == "llm1"
    assert graph["in1"]["connections"]["data"][0]["node_id"] == "llm2"
    # The data edge surfaces on the consumer as output_type 'data'.
    assert {"from_node": "in1", "output_type": "data", "input_port": "input"} \
        in graph["llm2"]["inputs"]
    # The execution edge surfaces as output_type 'output'.
    assert {"from_node": "in1", "output_type": "output", "input_port": "input"} \
        in graph["llm1"]["inputs"]

