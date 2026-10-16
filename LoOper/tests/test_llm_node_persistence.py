"""LLM node Web Mode (and OCR region) must survive a chain save + reopen.

``_save_llm_node`` writes ``web_mode`` / ``ocr_region`` / ``web_ocr_locator``;
the chain loader (``_create_llm_nodes``) used to drop them, so reopening a chain
reset Web Mode to the node default (``"false"``) and the next save re-wrote it
OFF - the "toggle does not persist across reopen" bug.  This pins the restore
and the full save -> load round trip.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from NGUI.graph_elements.config_manager import ConfigManager  # noqa: E402
from NGUI.nodes_resources.llm_node import LLMNode  # noqa: E402


class _GraphManager:
    """Minimal graph manager: create_node returns a real, standalone LLMNode."""

    def create_node(self, kind, name=None, **kw):
        return LLMNode()


class _Parent:
    """Minimal parent_widget the ConfigManager reads (graph now, list on load)."""

    def __init__(self):
        self.graph_manager = _GraphManager()
        self.llm_nodes = []


def test_create_llm_nodes_restores_web_mode_and_ocr_region():
    parent = _Parent()
    cm = ConfigManager(parent)
    cm.chain_config["llm_nodes"] = [{
        "model": "",
        "prompt": "",
        "input_source": "ocr",
        "web_mode": True,
        "ocr_region": "[1, 2, 3, 4]",
        "web_ocr_locator": '{"tag": "div"}',
    }]
    cm._create_llm_nodes({})
    node = parent.llm_nodes[0]
    assert node.get_property("web_mode") == "true"
    assert node.get_property("ocr_region") == "[1, 2, 3, 4]"
    assert node.get_property("web_ocr_locator") == '{"tag": "div"}'
    assert node.get_llm_config()["web_mode"] is True


def test_web_mode_survives_a_save_then_load_round_trip():
    # Save: a Web-Mode-ON node serializes web_mode True + the OCR region.
    cm = ConfigManager(_Parent())
    node = LLMNode()
    node.set_property("web_mode", "true")
    node.set_property("ocr_region", "[5, 6, 7, 8]")
    node.set_property("web_ocr_locator", '{"id": "x"}')
    cm._save_llm_node(node)
    saved_llm = cm.chain_config["llm_nodes"][0]
    assert saved_llm["web_mode"] is True
    assert saved_llm["ocr_region"] == "[5, 6, 7, 8]"
    assert saved_llm["web_ocr_locator"] == '{"id": "x"}'

    # Load: recreating the node from the saved dict restores the toggle.
    parent2 = _Parent()
    cm2 = ConfigManager(parent2)
    cm2.chain_config["llm_nodes"] = [saved_llm]
    cm2._create_llm_nodes({})
    reloaded = parent2.llm_nodes[0]
    assert reloaded.get_property("web_mode") == "true"
    assert reloaded.get_property("ocr_region") == "[5, 6, 7, 8]"
    assert reloaded.get_property("web_ocr_locator") == '{"id": "x"}'
    assert reloaded.get_llm_config()["web_mode"] is True
