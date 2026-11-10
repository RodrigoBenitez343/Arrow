"""Every node says what IT does — not what its type is for.

Field report: the Builder could not describe a one-node chain nor a single
node.  The digest only carried content for nodes that point at a recording, so
an ``llm``/``conditional``/``input``/``code`` node came back as its type name
plus its wiring, and a lone node of those types had nothing at all.  The chain's
stored metadata was the same story: one paragraph about the chain, nothing
about its contents — so agent mode routed on a summary and knew nothing about
what the steps do.

These tests pin the contract:

* ``describe.does_of`` reads the LIVE property names (a guessed key degrades to
  the type's purpose, which is exactly the superficiality being fixed);
* the digest lists EVERY node with its own action, wires included;
* ``read_node`` answers with a DOES line (first thing after the role);
* stored ``flow.nodes`` covers every node, authored text wins, and it survives
  a normalise/validate round trip;
* the agent's catalog exposes the per-node lines, and the brief uses them when
  there are no section titles (the atomic-tool case).

Run:  python -m pytest LoOper/tests/test_builder_node_metadata.py
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PyQt5.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _canvas(classes):
    from NodeGraphQt import NodeGraph

    graph = NodeGraph()
    for cls in classes:
        graph.register_node(cls)
    return graph


def _snapshot(graph):
    from types import SimpleNamespace

    from player.builder import snapshot

    return snapshot.build(SimpleNamespace(get_all_nodes=graph.all_nodes))


def _four_nodes(qapp):
    """input -> llm -> conditional -> output, each with its own settings."""
    from NGUI.nodes import (ConditionalNode, InputNode, LLMNode, OutputNode)

    graph = _canvas((InputNode, LLMNode, ConditionalNode, OutputNode))
    inp = graph.create_node("input.InputNode", name="query", pos=[0, 0])
    llm = graph.create_node("llm.LLMNode", name="draft", pos=[0, 200])
    cond = graph.create_node("conditional.ConditionalNode", name="priced?",
                             pos=[0, 400])
    out = graph.create_node("output.OutputNode", name="result", pos=[0, 600])
    inp.set_property("label", "ask")
    inp.set_property("passthrough", False)
    inp.set_property("user_prompt", "What should the reply be about?")
    llm.set_property("prompt", "Write a polite reply about {topic}")
    llm.set_property("model", "llama3.2:1b")
    llm.set_property("semantic_description", "drafts a polite reply")
    cond.set_property("condition_type", "code")
    cond.set_property("code", "# route on the draft\nresult = 'price' in "
                              "str(input_data)")
    out.set_property("label", "reply")
    inp.outputs()["output"].connect_to(llm.inputs()["input"])
    llm.outputs()["output"].connect_to(cond.inputs()["input"])
    cond.outputs()["true"].connect_to(out.inputs()["input"])
    cond.outputs()["false"].connect_to(out.inputs()["input"])
    inp.outputs()["data"].connect_to(llm.inputs()["prompt"])
    return graph, {"input": inp, "llm": llm, "conditional": cond,
                   "output": out}


# ------------------------------------------------------------ a node's action

def test_every_node_type_gets_its_own_action_line(qapp):
    from player.builder import describe

    graph, _nodes = _four_nodes(qapp)
    snap = _snapshot(graph)
    for node in snap.nodes.values():
        text = describe.does_of(snap, node)
        assert text, node.type
        assert not text.startswith("a %s node" % node.type), text

    digest = describe.overview(snap)
    assert 'supplies the value "ask"' in digest, digest
    assert 'asks the user "What should the reply be about?"' in digest
    assert 'TYPED on the real desktop' in digest
    assert 'writes text with the model "llama3.2:1b"' in digest
    assert 'runs a code test "result = ' in digest, digest
    assert 'and branches on the value it leaves in result' in digest
    assert 'true → "result"; false → "result"' in digest, digest
    assert 'prints "reply"' in digest


def test_a_lone_node_that_is_not_a_recording_is_described(qapp):
    """The other half of the atomic-tool case: one code node, no wires."""
    from NGUI.nodes import CodeNode
    from player.builder import describe

    graph = _canvas((CodeNode,))
    node = graph.create_node("code.CodeNode", name="normalise", pos=[0, 0])
    node.set_property("code", "# tidy the rows\ntext = str(input_data).strip()")
    snap = _snapshot(graph)

    digest = describe.overview(snap)
    assert "NODE CONTENT" in digest
    assert "runs Python: text = str(input_data).strip()" in digest, digest
    assert "# tidy the rows\ntext" not in digest, "comments are not the action"


def test_read_node_answers_with_does_before_its_wiring(qapp):
    from player.builder import describe

    graph, nodes = _four_nodes(qapp)
    snap = _snapshot(graph)
    detail = describe.node_detail(snap, nodes["conditional"].id)
    lines = detail.splitlines()
    role_at = next(i for i, line in enumerate(lines) if line.startswith("ROLE:"))
    does_at = next(i for i, line in enumerate(lines) if line.startswith("DOES:"))
    assert does_at == role_at + 1, lines[:5]
    assert "runs a code test" in lines[does_at]


# ------------------------------------------------------- stored, permanent

def test_stored_flow_covers_every_node_and_keeps_the_authored_text(qapp):
    from player.builder import flow

    graph, nodes = _four_nodes(qapp)
    snap = _snapshot(graph)
    raw = flow.normalise({
        "summary": "Asks the user, drafts a reply and reports it.",
        "nodes": [{"node": nodes["llm"].id,
                   "does": "drafts the reply from the prompt"}],
    })
    stored = flow.complete(snap, flow.check_nodes(snap, raw))

    described = {entry["node"]: entry["does"] for entry in stored["nodes"]}
    assert len(described) == len(snap.nodes), described
    assert described[nodes["llm"].id] == "drafts the reply from the prompt"
    assert 'supplies the value "ask"' in described[nodes["input"].id]
    assert 'prints "reply"' in described[nodes["output"].id]

    # the same metadata rides through a second validate (a reopened chain)
    again = flow.check_nodes(snap, flow.normalise(stored))
    assert len(again["nodes"]) == len(stored["nodes"])
    assert "NODES (what each node does, one line each):" in flow.digest(stored)


def test_a_node_entry_naming_a_missing_node_is_a_hard_error(qapp):
    from player.builder import flow
    from player.builder.errors import FlowMetadataError

    graph, _nodes = _four_nodes(qapp)
    snap = _snapshot(graph)
    with pytest.raises(FlowMetadataError):
        flow.check_nodes(snap, flow.normalise(
            {"nodes": [{"node": "0xdeadbeef", "does": "does nothing"}]}))


# ------------------------------------------------- the digest cannot eat them

def _long_chain(count=20):
    """A linear chain of *count* nodes with their own label, as a Snapshot."""
    from player.builder import ports
    from player.builder.snapshot import Edge, NodeView, PortView, Snapshot

    nodes, edges = {}, []
    for index in range(count):
        node_id = '0x%02d' % index
        nodes[node_id] = NodeView(
            id=node_id, type='input', name='step %d' % index, role='VALUE',
            position=(0.0, float(index * 100)),
            properties={'label': 'value %d' % index, 'passthrough': True},
            inputs=[PortView('input', 'in', ports.EXEC_IN, 'run', [])],
            outputs=[PortView('output', 'out', ports.EXEC_OUT, 'hand on', [])],
        )
        if index:
            edges.append(Edge(src_id='0x%02d' % (index - 1), src_port='output',
                              src_role=ports.EXEC_OUT, dst_id=node_id,
                              dst_port='input', dst_role=ports.EXEC_IN,
                              kind='exec'))
    return Snapshot(nodes=nodes, edges=edges, order=list(nodes))


def test_the_digest_keeps_every_node_line_in_a_long_chain():
    """The walk used to be joined first and clipped last: every DOES line was
    cut on a long chain, so the model saw the topology and not the nodes."""
    from player.builder import describe

    snap = _long_chain(20)
    digest = describe.overview(snap, 9000)
    content = digest.split('NODE CONTENT', 1)[-1]

    for index in range(20):
        assert '0x%02d ' % index in content, "node %d lost its line" % index
    assert 'supplies the value "value 19"' in content


def test_a_section_note_is_derived_when_none_was_written(qapp):
    """The box prints the note: a section that only has a title would be drawn
    as a headline with no body."""
    from player.builder import flow

    graph, nodes = _four_nodes(qapp)
    snap = _snapshot(graph)
    boxes = flow.sections(snap, flow.normalise({
        "sections": [{"title": "ask", "nodes": [nodes["input"].id]},
                     {"title": "draft", "nodes": [nodes["llm"].id,
                                                    nodes["conditional"].id]},
                     {"title": "report", "note": "author's own words",
                      "nodes": [nodes["output"].id]}],
    }))

    assert all(section["note"] for section in boxes), boxes
    assert 'supplies the value "ask"' in boxes[0]["note"]
    assert 'writes text with the model' in boxes[1]["note"]
    assert 'runs a code test' in boxes[1]["note"], \
        'the whole stretch, not just its first node'
    assert boxes[2]["note"] == "author's own words", \
        "authored words win over the derived ones"

    # the phase fallback (a chain that was never described) carries one too
    fallback = flow.sections(snap, {})
    assert fallback and all(section["note"] for section in fallback), fallback


def test_the_digest_states_the_data_wiring():
    """A note guessed that an unwired REPORT was 'fed' by the step before it:
    the digest must say, in so many words, that nothing carries a value."""
    from player.builder import describe

    digest = describe.overview(_long_chain(4), 9000)
    assert 'DATA FLOWS: none' in digest, digest


def test_a_prose_line_over_the_limit_is_clipped_not_fatal(qapp):
    """A stored DOES line of 601 chars killed the walkthrough: prose is clipped
    to its ceiling, only structure (a title) still raises."""
    from player.builder import flow
    from player.builder.errors import FlowMetadataError

    long_text = 'x' * (flow.MAX_DOES + 400)
    stored = flow.normalise({
        'summary': long_text,
        'nodes': [{'node': '0x1', 'does': long_text}],
        'sections': [{'title': 't', 'note': long_text, 'nodes': ['0x1']}],
    })
    assert len(stored['nodes'][0]['does']) == flow.MAX_DOES
    assert len(stored['sections'][0]['note']) == flow.MAX_NOTE
    assert len(stored['summary']) == flow.MAX_SUMMARY
    assert len(flow._clip(long_text, 10)) == 10

    with pytest.raises(FlowMetadataError):
        flow.normalise({'sections': [{'title': 'y' * 200,
                                      'nodes': ['0x1']}]})


def test_a_stale_ref_does_not_hide_the_rest_of_the_description():
    """One unknown id used to raise and draw NOTHING; the sections that still
    name live nodes are true and must be drawn."""
    from player.builder import flow

    snap = _long_chain(3)
    pruned, dropped = flow.prune(snap, {
        'sections': [{'title': 'good', 'nodes': ['0x00', '0x01']},
                     {'title': 'gone', 'nodes': ['0xdeadbeef']}],
        'steps': [{'node': '0x02', 'does': 'x'},
                  {'node': '0xbeef', 'does': 'y'}],
        'nodes': [{'node': '0x00', 'does': 'a'},
                  {'node': '0xbeef', 'does': 'b'}],
    })

    assert dropped == 3
    assert [section['title'] for section in pruned['sections']] == ['good']
    assert pruned['sections'][0]['nodes'] == ['0x00', '0x01']
    assert [step['node'] for step in pruned['steps']] == ['0x02']
    assert [entry['node'] for entry in pruned['nodes']] == ['0x00']


def test_the_node_description_is_not_part_of_its_stamp():
    """The description is the SENTENCE about the node: editing the wording
    must not make it look stale and get it rewritten."""
    from player.builder import flow

    assert flow.node_stamp('input', {'label': 'a',
                                     'description': 'x'}) \
        == flow.node_stamp('input', {'label': 'a'})


# --------------------------------------------------------- what the agent sees

def test_the_catalog_carries_the_node_lines(tmp_path):
    from player.agentic_ops import catalog

    path = tmp_path / "draft.json"
    path.write_text(
        '{"name": "draft", "flow": {"summary": "drafts a reply", '
        '"nodes": [{"node": "0x1", "does": "supplies the value \\"ask\\""}, '
        '{"node": "0x2", "does": "writes the reply with the model"}]}}',
        encoding="utf-8")

    entry = catalog.entry(str(path))
    assert [node["does"] for node in entry["nodes"]] == [
        'supplies the value "ask"', "writes the reply with the model"]
    line = catalog.digest([entry])
    assert "| its nodes: " in line, line
    assert "writes the reply with the model" in line


def test_a_chain_described_only_by_node_lines_is_routable(tmp_path):
    from player.agentic_ops import catalog

    path = tmp_path / "tool.json"
    path.write_text('{"name": "open linkedin", "flow": {"nodes": '
                    '[{"node": "0x1", "does": "replays the web recording '
                    '\\"my network.json\\""}]}}', encoding="utf-8")

    entry = catalog.entry(str(path))
    assert entry["routable"] is True
    assert entry["nodes"], entry


def test_the_brief_uses_the_node_trail_when_there_are_no_sections():
    from player import chain_brief

    described = {"flow": {"summary": "Atomic web-replay tool.",
                          "nodes": [{"node": "0x1",
                                     "does": 'replays the web recording "my '
                                             'network.json" — 1 action'},
                                    {"node": "0x2",
                                     "does": "reports the feed"}]}}
    line = chain_brief.brief(described)
    assert line.startswith("Atomic web-replay tool."), line
    assert 'replays the web recording "my network.json"' in line, line
    assert "reports the feed" in line, line
    assert "— 1 action" not in line, "the settings are not the action"
    assert len(line) <= chain_brief.BRIEF_LIMIT
