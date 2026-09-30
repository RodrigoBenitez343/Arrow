"""Model-selection dropdowns on the v2 node dialogs.

Regression: the Input (decision) dialog shipped the model as a free-text
field, so users could not see which models actually exist — and an empty
value silently meant "borrow the chain's LLM node" (the runtime also falls
back to the app default; the dialog must offer the real model list).  Also
covers the LLM node's chat|orchestrator mode toggle.

Run:  python -m pytest LoOper/tests/test_node_dialogs_model_dropdowns.py
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from PyQt5.QtWidgets import QApplication, QComboBox  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_input_dialog_decision_model_is_a_dropdown(qapp):
    import NGUI.graph_elements  # noqa: F401
    from NGUI.dialogs.input_dialog import InputPropertiesDialog

    dlg = InputPropertiesDialog(None, {
        "label": "q", "decision_mode": True,
        "decision_criterion": "asks for a search",
        "decision_model": "SmolLM3-Q4_K_M.gguf",
    })

    assert not hasattr(dlg, "decision_model_edit")
    assert isinstance(dlg.decision_model_combo, QComboBox)
    assert dlg.decision_model_combo.isEditable()
    assert dlg.decision_model_combo.currentText() == "SmolLM3-Q4_K_M.gguf"
    assert dlg.get_config()["decision_model"] == "SmolLM3-Q4_K_M.gguf"


def test_llm_dialog_exposes_orchestrator_mode(qapp):
    """The LLM node dialog offers the orchestrator switch and reflects it."""
    import NGUI.graph_elements  # noqa: F401
    from NGUI.dialogs.llm_dialogs import LLMPropertiesDialog

    dlg = LLMPropertiesDialog(None, {})
    assert not dlg.orchestrator_mode_check.isChecked()
    dlg.orchestrator_mode_check.setChecked(True)
    cfg = dlg.get_config()
    assert cfg["orchestrator_mode"] is True
    assert "orch_synthesize" in cfg and "orch_use_goal_ledger" in cfg

    # Reopening with a saved ON config reflects the switch (persistence).
    dlg2 = LLMPropertiesDialog(
        None, {"orchestrator_mode": True, "orch_max_steps": 9})
    assert dlg2.orchestrator_mode_check.isChecked()
    assert dlg2.orch_max_steps_spin.value() == 9

    # Toggling OFF is not a one-way ticket: back to the vanilla LLM node.
    dlg2.orchestrator_mode_check.setChecked(False)
    assert dlg2.get_config()["orchestrator_mode"] is False


def test_llm_node_orchestrator_switch_is_reversible(qapp):
    """ON adds the orchestrator ports; OFF removes them again."""
    import NGUI.graph_elements  # noqa: F401
    from NGUI.nodes_resources.llm_node import LLMNode

    node = LLMNode()
    assert not bool(node.get_property("orchestrator_mode"))

    node.set_orchestrator_mode(True)
    assert bool(node.get_property("orchestrator_mode")) is True
    on_names = {p.name() for p in node.output_ports()}
    assert {"trace", "route"} <= on_names

    node.set_orchestrator_mode(False)
    assert bool(node.get_property("orchestrator_mode")) is False
    off_names = {p.name() for p in node.output_ports()}
    assert "trace" not in off_names and "route" not in off_names
    assert "output" in off_names


def test_llm_node_prompt_port_and_tools_gating(qapp):
    """A vanilla LLM node has a 'prompt' port but no 'tools' port; the
    'tools' port appears only in orchestrator mode (brains+chains fused)."""
    import NGUI.graph_elements  # noqa: F401
    from NGUI.nodes_resources.llm_node import LLMNode

    node = LLMNode()
    vanilla_inputs = {p.name() for p in node.input_ports()}
    assert "prompt" in vanilla_inputs
    assert "context" in vanilla_inputs
    assert "tools" not in vanilla_inputs

    node.set_orchestrator_mode(True)
    orch_inputs = {p.name() for p in node.input_ports()}
    assert "tools" in orch_inputs
    assert "prompt" in orch_inputs

    node.set_orchestrator_mode(False)
    assert "tools" not in {p.name() for p in node.input_ports()}


def test_handle_node_exposes_data_in_and_out_ports(qapp):
    """The Handle node carries data on 'data' (in/out); base ports are exec-only."""
    import NGUI.graph_elements  # noqa: F401
    from NGUI.nodes_resources.handle_node import HandleNode

    node = HandleNode()
    inputs = {p.name() for p in node.input_ports()}
    outputs = {p.name() for p in node.output_ports()}
    assert {"input", "data"} <= inputs
    assert {"output", "data"} <= outputs
