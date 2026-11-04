"""An atomic tool (one node, no wires) must be describable by the builder.

The failing case: a workspace of one-step chains.  Each chain is a single
``web_sequence`` node pointing at a recorded browser session, so the topology
says nothing — and the builder said the chain "replays nothing", because it read
a node's properties wrong (NodeGraphQt nests them under ``custom``) and because
nothing ever opened the recording the node points at.

These tests pin the contract:

* a live node's config is read whether it is flat or nested under ``custom``;
* the recording is summarised: its own name, its action count and kinds, URLs;
* a missing/unreadable/empty recording is a REPORTED defect, not silence;
* the digest carries the content (NODE CONTENT) and the node detail carries the
  artifact + the type's purpose, so a lone node is explained;
* the visual walkthrough speaks the recording instead of the type's boilerplate.

Run:  python -m pytest LoOper/tests/test_builder_atomic.py
"""

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PyQt5.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _recording(folder, name, actions, url=""):
    """A minimal web recording, the shape the web recorder writes."""
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name)
    payload = {
        "schema_version": 1,
        "mode": "web",
        "metadata": {"total_actions": len(actions), "mode": "web",
                     "start_url": "chrome://new-tab-page/",
                     "name": "Open linkedin.com in the browser and read the feed"},
        "actions": actions,
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    return path


def _lone_web_node(qapp, session_file):
    """A real canvas holding exactly one web_sequence node (an atomic tool)."""
    from NodeGraphQt import NodeGraph
    from NGUI.nodes import WebSequenceNode

    graph = NodeGraph()
    graph.register_node(WebSequenceNode)
    node = graph.create_node("web_sequence.WebSequenceNode",
                             name="open linkedin", pos=[0, 0])
    node.set_property("session_file", session_file)
    return graph, node


def _snapshot(graph):
    from types import SimpleNamespace
    from player.builder import snapshot

    return snapshot.build(SimpleNamespace(get_all_nodes=graph.all_nodes))


# ------------------------------------------------------- property reading

def test_a_live_nodes_config_is_read_from_the_nested_custom_block(qapp):
    """NodeGraphQt serialises custom props under 'custom': read them."""
    from player.builder import node_templates

    graph, node = _lone_web_node(qapp, "whatever.json")
    raw = node.properties()
    assert "session_file" not in raw, "the top level never carries custom props"
    assert raw["custom"]["session_file"] == "whatever.json"

    props = node_templates.node_properties(node)
    assert props["session_file"] == "whatever.json"


def test_a_flat_property_dict_still_works():
    """A stub node / a snapshot hands properties flat; both shapes are read."""
    from player.builder import node_templates

    class Flat:
        __identifier__ = "web_sequence"
        type_ = "web_sequence.WebSequenceNode"

        def properties(self):
            return {"session_file": "x.json"}

    assert node_templates.node_properties(Flat())["session_file"] == "x.json"


# ------------------------------------------------------------- artifacts

def test_the_recording_is_summarised(tmp_path):
    from player.builder import artifacts

    actions = [{"type": "navigate", "url": "https://www.linkedin.com"},
               {"type": "scroll"}, {"type": "scroll"}, {"type": "click"}]
    path = _recording(str(tmp_path / "web_sequences"), "session.json", actions)

    described = artifacts.describe("web_sequence", {"session_file": path})
    assert described["problem"] == ""
    text = described["text"]
    assert "Open linkedin.com" in text, text
    # The recording says what it DID, not how many actions it holds: the
    # targets are the only part a reader (or a model) can use.
    assert "opens www.linkedin.com" in text, text
    assert "scrolls the page ×2" in text, text
    assert "clicks" in text and "action(s)" not in text, text


def test_a_missing_recording_is_reported_not_swallowed():
    from player.builder import artifacts

    described = artifacts.describe("web_sequence",
                                   {"session_file": "not_there.json"})
    assert described["text"] == ""
    assert "not on disk" in described["problem"]


def test_an_empty_recording_is_reported(tmp_path):
    from player.builder import artifacts

    path = _recording(str(tmp_path / "web_sequences"), "empty.json", [])
    described = artifacts.describe("web_sequence", {"session_file": path})
    assert "holds no actions" in described["problem"]


def test_a_chain_import_is_summarised_by_the_chain_it_runs(tmp_path):
    from player.builder import artifacts

    chain = tmp_path / "tool.json"
    chain.write_text(json.dumps({
        "input_nodes": [{"node_id": "0x1", "label": "url"}],
        "output_nodes": [{"node_id": "0x2", "label": "page"}],
        "flow": {"summary": "opens a page and reports its title"},
    }), encoding="utf-8")

    described = artifacts.describe("chain_import",
                                   {"chain_file_path": str(chain)})
    assert "tool.json" in described["text"]
    assert "opens a page" in described["text"]


# ---------------------------------------------------------------- digest

def test_the_digest_explains_a_lone_node(qapp, tmp_path):
    from player.builder import describe

    path = _recording(str(tmp_path / "web_sequences"), "session.json",
                      [{"type": "navigate", "url": "https://www.linkedin.com"},
                       {"type": "scroll"}])
    graph, _node = _lone_web_node(qapp, path)
    digest = describe.overview(_snapshot(graph))

    assert "NODE CONTENT" in digest
    # The verb-first contract: the digest names what the recording DOES, not
    # the file it replays.
    assert "opens www.linkedin.com" in digest, digest
    assert "linkedin.com" in digest
    assert "no session_file is set" not in digest, "the recording IS attached"


def test_the_node_detail_carries_purpose_and_artifact(qapp, tmp_path):
    from player.builder import describe

    path = _recording(str(tmp_path / "web_sequences"), "session.json",
                      [{"type": "navigate", "url": "https://example.test"}])
    graph, node = _lone_web_node(qapp, path)
    detail = describe.node_detail(_snapshot(graph), node.id)

    assert "PURPOSE" in detail
    # The artifact IS the node's action here, so it is stated once, as DOES —
    # repeating it under ARTIFACT said nothing new.
    assert "DOES: opens example.test" in detail, detail
    assert "example.test" in detail
    assert "ARTIFACT:" not in detail


def test_an_unattached_recording_is_a_defect(qapp):
    from player.builder import describe

    graph, _node = _lone_web_node(qapp, "")
    snap = _snapshot(graph)
    problems = describe.violations(snap, set(snap.nodes))
    assert any("no session_file is set" in text for text in problems), problems


# ------------------------------------------------------------ walkthrough

def test_the_walkthrough_speaks_the_recording(qapp, tmp_path):
    from player.builder import flow

    path = _recording(str(tmp_path / "web_sequences"), "session.json",
                      [{"type": "navigate", "url": "https://www.linkedin.com"}])
    graph, _node = _lone_web_node(qapp, path)
    steps = flow.steps(_snapshot(graph))

    assert len(steps) == 1
    body = steps[0]["body"]
    # The walkthrough speaks the recording's ACTION, not its file.
    assert "opens www.linkedin.com" in body[0], body
    assert not any("session_file:" in line for line in body), \
        "the raw path adds nothing once the recording is summarised"
