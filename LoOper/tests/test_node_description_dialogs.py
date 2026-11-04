"""Node description fields (task #2) — the dialogs round-trip the text the
orchestrator routes node brains by.

These are the only automated checks over the GUI edits: each dialog is
constructed offscreen, and the description is proven to be read from the
config dict and returned by ``get_properties()`` / ``get_config()``.

Run:  python -m pytest LoOper/tests/test_node_description_dialogs.py
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    return app


def test_sequence_dialog_roundtrips_the_description(qapp):
    from NGUI.dialogs.sequence_dialogs import SequencePropertiesDialog

    d = SequencePropertiesDialog(
        {"description": "Open the jobs page", "loop_count": 1})
    assert d.get_properties()["description"] == "Open the jobs page"

    # The dialog strips surrounding whitespace on the way out.
    d.description_input.setText("  trimmed text  ")
    assert d.get_properties()["description"] == "trimmed text"


def test_web_sequence_dialog_roundtrips_the_description(qapp):
    from NGUI.dialogs.web_sequence_dialogs import WebSequencePropertiesDialog

    d = WebSequencePropertiesDialog(
        {"description": "Search the job board", "session_file": "search.json"})
    assert d.get_properties()["description"] == "Search the job board"


def test_conditional_dialog_roundtrips_the_description(qapp):
    from NGUI.dialogs.conditional_dialogs import AdvancedConditionalDialog

    d = AdvancedConditionalDialog(
        None, 0, [], {"description": "Is the Apply button visible?"})
    assert d.get_config().get("description") == "Is the Apply button visible?"


def test_dialogs_default_to_empty_without_a_description(qapp):
    from NGUI.dialogs.sequence_dialogs import SequencePropertiesDialog
    from NGUI.dialogs.web_sequence_dialogs import WebSequencePropertiesDialog

    assert SequencePropertiesDialog({"loop_count": 1}).get_properties()["description"] == ""
    assert WebSequencePropertiesDialog({}).get_properties()["description"] == ""


# ------------------------------------------------- every node, one sentence
def test_every_node_class_carries_a_description_property(qapp):
    """The Builder fills it and the dialog edits it, so a type without the
    property would silently lose half of what the Builder knows."""
    from NodeGraphQt import NodeGraph

    from NGUI.nodes import (ChainImportNode, CodeNode, ConditionalNode,
                            ContainerNode, ContextNode, FormFillerNode,
                            HandleNode, InputNode, LLMNode, MCPNode,
                            OutputNode)

    graph = NodeGraph()
    for cls in (InputNode, OutputNode, LLMNode, CodeNode, ConditionalNode,
                ChainImportNode, ContainerNode, ContextNode, FormFillerNode,
                HandleNode, MCPNode):
        graph.register_node(cls)

    for identifier in ("input.InputNode", "output.OutputNode",
                       "llm.LLMNode", "code.CodeNode",
                       "conditional.ConditionalNode",
                       "chain_import.ChainImportNode",
                       "container.ContainerNode", "context.ContextNode",
                       "form_filler.FormFillerNode", "handle.HandleNode",
                       "mcp.MCPNode"):
        node = graph.create_node(identifier, name="n", pos=[0, 0])
        node.set_property("description", "what this node does")
        assert node.get_property("description") == "what this node does", identifier


def test_a_node_dialog_shows_and_returns_the_description(qapp):
    from NGUI.dialogs.output_dialog import OutputPropertiesDialog

    dialog = OutputPropertiesDialog(None, {"label": "result"})
    dialog.add_description("reports the shortlist to the chat")
    assert dialog.description() == "reports the shortlist to the chat"

    dialog.description_edit.setPlainText("  edited by hand  ")
    assert dialog.description() == "edited by hand"


def test_the_description_rides_through_save_and_load(qapp, monkeypatch):
    """It is node metadata, so one pass carries it for every type."""
    from NGUI.graph_elements import config_manager as cm_mod
    from NGUI.graph_elements.config_manager import ConfigManager

    monkeypatch.setattr(cm_mod.QMessageBox, "critical",
                        staticmethod(lambda *a, **k: None))

    class _Parent:
        chains_folder = ""

        class graph_manager:
            @staticmethod
            def get_all_nodes():
                return []

    class _Node:
        def __init__(self, node_id, props=None):
            self.id = node_id
            self._props = dict(props or {})

        def properties(self):
            # NodeGraphQt nests registered properties under ``custom``.
            return {"id": self.id, "custom": dict(self._props)}

        def get_property(self, name):
            return self._props[name]

        def set_property(self, name, value):
            self._props[name] = value

    cm = ConfigManager(_Parent())
    cm.chain_config = {"input_nodes": [{"node_id": "n1"}],
                       "output_nodes": [{"node_id": "n2"}]}

    cm._attach_descriptions([_Node("n1", {"description": "supplies the query"}),
                             _Node("n2", {"description": "reports the answer"})])
    assert cm.chain_config["input_nodes"][0]["description"] == "supplies the query"
    assert cm.chain_config["output_nodes"][0]["description"] == "reports the answer"

    live_in, live_out = _Node("n1"), _Node("n2")
    cm._restore_descriptions({"n1": live_in, "n2": live_out})
    assert live_in.get_property("description") == "supplies the query"
    assert live_out.get_property("description") == "reports the answer"


# ------------------------------------------- the model that writes them
def test_ai_settings_owns_the_description_model(qapp, monkeypatch, tmp_path):
    """AI Settings edits the Builder's own settings file, so the panel and the
    dialog can never disagree about which model writes the descriptions."""
    from player.builder import settings as bsettings

    target = tmp_path / "builder_settings.json"
    monkeypatch.setattr(bsettings, "path", lambda: str(target))

    from NGUI.dialogs import ai_settings_dialog as ai_mod

    # The dialog pings the API server on a timer; a settings test does not.
    monkeypatch.setattr(ai_mod.AISettingsDialog, "check_api_status",
                        lambda self: None)
    dialog = ai_mod.AISettingsDialog(None)

    dialog.description_model_combo.setCurrentText("smollm2:135m")
    index = dialog.description_provider_combo.findData("ollama")
    if index >= 0:
        dialog.description_provider_combo.setCurrentIndex(index)
    dialog._save_description_settings()

    saved = bsettings.load()
    assert saved["model"] == "smollm2:135m"
    if index >= 0:
        assert saved["engine"] == "ollama"

    dialog.description_model_combo.clear()
    dialog._load_description_settings()
    assert dialog.description_model_combo.currentText() == "smollm2:135m"
    if index >= 0:
        assert dialog.description_provider_combo.currentData() == "ollama"

