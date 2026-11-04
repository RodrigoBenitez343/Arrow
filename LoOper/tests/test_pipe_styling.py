"""Runnable check for pipe_styling: every connection type gets its own color.

Covers: exec green, loopback red, context blue, conditional dashed green/red,
orchestrator tools-port orange, Input-node passthrough data green.
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _port(node, name, io="out"):
    from NGUI.graph_elements.pipe_styling import _pname
    ports = getattr(node, "output_ports" if io == "out" else "input_ports")()
    for p in ports:
        if _pname(p) == name:
            return p
    raise AssertionError(f"{io} port {name!r} missing on {type(node).__name__}")


def _style(graph, src, out_name, dst, in_name):
    """(color, style) of the single wire src.out_name -> dst.in_name."""
    from NGUI.graph_elements.pipe_styling import connected_pipes, _pname
    for pipe in connected_pipes(graph):
        if (_pname(pipe.output_port) == out_name and pipe.output_port.node is src.view
                and _pname(pipe.input_port) == in_name and pipe.input_port.node is dst.view):
            return (pipe.color, pipe.style)
    raise AssertionError(f"no wire {out_name} -> {in_name} between the given nodes")


def test_connection_type_colors(qapp):
    from NodeGraphQt import NodeGraph
    from NGUI.nodes_resources.input_node import InputNode
    from NGUI.nodes_resources.sequence_node import SequenceNode
    from NGUI.nodes_resources.conditional_node import ConditionalNode
    from NGUI.nodes_resources.context_node import ContextNode
    from NGUI.nodes_resources.llm_node import LLMNode
    from NGUI.nodes_resources.chain_import_node import ChainImportNode
    from NGUI.graph_elements.pipe_styling import (
        GREEN, RED, BLUE, ORANGE, _SOLID, _DASHED, restyle_pipes)

    graph = NodeGraph()
    for cls in (InputNode, SequenceNode, ConditionalNode, ContextNode,
                LLMNode, ChainImportNode):
        graph.register_node(cls)

    inp = graph.create_node("input.InputNode", pos=[0, 0])
    seq_a = graph.create_node("sequence.SequenceNode", pos=[200, 0])
    seq_b = graph.create_node("sequence.SequenceNode", pos=[400, 0])
    cond = graph.create_node("conditional.ConditionalNode", pos=[600, 0])
    ctx = graph.create_node("context.ContextNode", pos=[200, 300])
    llm = graph.create_node("llm.LLMNode", pos=[400, 300])
    llm.set_orchestrator_mode(True)
    chain = graph.create_node("chain_import.ChainImportNode", pos=[600, 300])

    _port(inp, "output").connect_to(_port(seq_a, "input", "in"))       # 1. exec
    _port(seq_a, "output").connect_to(_port(seq_b, "input", "in"))      # 1. exec
    _port(seq_b, "output").connect_to(_port(seq_a, "input", "in"))      # 2. loopback
    _port(inp, "data").connect_to(_port(llm, "input", "in"))           # 4. passthrough
    _port(ctx, "ctx_out").connect_to(_port(llm, "context", "in"))       # 3. context
    _port(cond, "true").connect_to(_port(llm, "input", "in"))           # 5. true
    _port(cond, "false").connect_to(_port(llm, "input", "in"))          # 5. false
    _port(chain, "output").connect_to(_port(llm, "tools", "in"))        # 6. tools

    restyle_pipes(graph)

    assert _style(graph, inp, "output", seq_a, "input") == (GREEN, _SOLID)
    assert _style(graph, seq_b, "output", seq_a, "input") == (RED, _SOLID)
    assert _style(graph, inp, "data", llm, "input") == (GREEN, _SOLID)
    assert _style(graph, ctx, "ctx_out", llm, "context") == (BLUE, _SOLID)
    assert _style(graph, cond, "true", llm, "input") == (GREEN, _DASHED)
    assert _style(graph, cond, "false", llm, "input") == (RED, _DASHED)
    assert _style(graph, chain, "output", llm, "tools") == (ORANGE, _SOLID)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
