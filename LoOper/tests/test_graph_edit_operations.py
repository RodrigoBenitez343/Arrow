"""Graph-editor edit-buttons coverage across every node type.

Regressions guarded here (both reported from the graph editor):
- deleting a selected node (Delete key / Delete button) silently ignored
  Orchestrator nodes: NodeManagementMixin.delete_node had no dispatch
  branch for them and fell into the "Unknown node type" warning;
- dragging a selection onto an empty Chain Import node ("wrap into chain",
  the in-graph bundling flow) saved only a hardcoded subset of node
  categories and then deleted EVERY selected node, so wrapped
  output / input / handle / mcp / orchestrator / web-sequence nodes were
  dropped from the bundle and lost from the graph.

Run:  python -m pytest LoOper/tests/test_graph_edit_operations.py
"""

import json
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from NGUI.graph_elements.node_operations_modules import chain_import as ci_mod  # noqa: E402
from NGUI.graph_elements.node_operations_modules.chain_import import (  # noqa: E402
    ChainImportOperationsMixin,
)
from NGUI.graph_elements.node_operations_modules.node_management import (  # noqa: E402
    NodeManagementMixin,
)
from NGUI.nodes import (  # noqa: E402
    ActionNode,
    ChainImportNode,
    CodeNode,
    ConditionalNode,
    ContainerNode,
    ContextNode,
    FormFillerNode,
    HandleNode,
    InputNode,
    LLMNode,
    MCPNode,
    OutputNode,
    SequenceNode,
    WebSequenceNode,
)

# Everything GraphManager registers must be deletable through the app's
# Delete button / Delete key.
ALL_NODE_CLASSES = [
    SequenceNode,
    WebSequenceNode,
    ActionNode,
    ConditionalNode,
    LLMNode,
    ChainImportNode,
    FormFillerNode,
    CodeNode,
    ContainerNode,
    ContextNode,
    InputNode,
    HandleNode,
    MCPNode,
    OutputNode,
]


class _GraphRecorder:
    def __init__(self):
        self.deleted = []

    def delete_node(self, node):
        self.deleted.append(node)


class _DeleteHarness(NodeManagementMixin):
    def __init__(self):
        self.parent_widget = SimpleNamespace(graph_manager=_GraphRecorder())
        self.file_backed_deletes = []

    # Dedicated handlers (they also touch sequence/action files on disk).
    def delete_sequence_node(self, node):
        self.file_backed_deletes.append(node)

    def delete_action_node(self, node):
        self.file_backed_deletes.append(node)


def _bare_node(cls, node_id):
    """Node instance without running the Qt-heavy NodeGraphQt __init__."""
    node = cls.__new__(cls)
    node._model = SimpleNamespace(id=node_id)
    return node


def test_delete_dispatches_for_every_node_type():
    harness = _DeleteHarness()
    graph = harness.parent_widget.graph_manager
    for idx, cls in enumerate(ALL_NODE_CLASSES):
        node = _bare_node(cls, f"n{idx}")
        harness.delete_node(node)
        assert node in graph.deleted or node in harness.file_backed_deletes, (
            f"{cls.__name__} was not deleted - dispatch branch missing"
        )


# ---------------------------------------------------------------------------
# Wrap-into-chain (drag selection onto an empty Chain Import node)
# ---------------------------------------------------------------------------


class _WrapHarness(ChainImportOperationsMixin):
    def __init__(self, parent):
        self.parent_widget = parent


class _FakeConfigManager:
    """Stands in for ConfigManager: canned per-node-id category lists."""

    def __init__(self, configs):
        self._configs = dict(configs)

    def get_single_node_config(self, node):
        return self._configs.get(node.id, {})


def _patch_dialogs(monkeypatch, name="Wrapped Flow", desc="a description"):
    answers = iter([(name, True), (desc, True)])
    monkeypatch.setattr(
        ci_mod, "QInputDialog",
        SimpleNamespace(getText=lambda *a, **k: next(answers)),
    )
    monkeypatch.setattr(
        ci_mod, "QMessageBox",
        SimpleNamespace(
            warning=lambda *a, **k: None,
            information=lambda *a, **k: None,
            critical=lambda *a, **k: None,
        ),
    )


def test_wrap_keeps_every_node_category(tmp_path, monkeypatch):
    """Every node category must land in the wrapped chain; a node with no
    exportable configuration must stay in the graph instead of being
    deleted into nowhere."""
    llm = SimpleNamespace(id="llm_1")
    out = SimpleNamespace(id="out_1")
    web = SimpleNamespace(id="web_1")
    inp = SimpleNamespace(id="inp_1")
    opaque = SimpleNamespace(id="opaque_1")

    parent = SimpleNamespace(
        chains_folder=str(tmp_path),
        graph_manager=_GraphRecorder(),
        config_manager=_FakeConfigManager({
            "llm_1": {"llm_nodes": [{"node_id": "llm_1"}]},
            "out_1": {"output_nodes": [{"node_id": "out_1"}]},
            "web_1": {"web_sequences": [{"node_id": "web_1"}]},
            "inp_1": {"input_nodes": [{"node_id": "inp_1"}]},
            "opaque_1": {},
        }),
    )
    configured = []
    target = SimpleNamespace(
        set_chain_import_data=lambda *a, **k: configured.append(a)
    )
    _patch_dialogs(monkeypatch)

    harness = _WrapHarness(parent)
    assert harness.wrap_selected_nodes_into_chain(
        [llm, out, web, inp, opaque], target
    ) is True

    chain_file = tmp_path / "Wrapped Flow.json"
    saved = json.loads(chain_file.read_text(encoding="utf-8"))
    for key, node_id in [
        ("llm_nodes", "llm_1"),
        ("output_nodes", "out_1"),
        ("web_sequences", "web_1"),
        ("input_nodes", "inp_1"),
    ]:
        assert saved.get(key) == [{"node_id": node_id}], (
            f"{key} was dropped from the wrapped chain"
        )
    assert saved["description"] == "a description"

    # Wrapped nodes leave the graph; the unexportable one survives.
    for node in (llm, out, web, inp):
        assert node in parent.graph_manager.deleted
    assert opaque not in parent.graph_manager.deleted
    # The target Chain Import node now points at the new chain file.
    assert configured and configured[0][0] == str(chain_file)


def test_wrap_without_any_config_deletes_nothing(tmp_path, monkeypatch):
    """A selection that yields no node data aborts without removing nodes."""
    opaque_a = SimpleNamespace(id="opaque_a")
    opaque_b = SimpleNamespace(id="opaque_b")
    parent = SimpleNamespace(
        chains_folder=str(tmp_path),
        graph_manager=_GraphRecorder(),
        config_manager=_FakeConfigManager({
            "opaque_a": {},
            "opaque_b": {},
        }),
    )
    _patch_dialogs(monkeypatch)

    harness = _WrapHarness(parent)
    result = harness.wrap_selected_nodes_into_chain(
        [opaque_a, opaque_b], SimpleNamespace()
    )

    assert result is False
    assert parent.graph_manager.deleted == []
    assert not list(tmp_path.glob("*.json"))
