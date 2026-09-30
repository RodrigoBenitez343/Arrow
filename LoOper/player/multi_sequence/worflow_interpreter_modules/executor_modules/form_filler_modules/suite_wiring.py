"""Builder + executor wiring for the runtime node type."""

from . import FormFillerMixin
from player.multi_sequence.worflow_interpreter_modules.builder import WorkflowGraphBuilder


def test_builder_creates_form_filler_runtime_node():
    builder = WorkflowGraphBuilder(form_filler_nodes=[{
        "id": "ff1", "mode": "web", "instruction": "fill",
        "connections": [{"output_port": "output", "target_node_id": "n2",
                         "input_port": "input"}],
    }], output_nodes=[{"id": "n2", "connections": []}])
    graph = builder.build_workflow_graph()
    assert graph["ff1"]["type"] == "form_filler"
    assert graph["ff1"]["connections"]["output"][0]["node_id"] == "n2"
    # Downstream receives the form node as an input dependency.
    assert graph["n2"]["inputs"][0]["from_node"] == "ff1"


def test_workflow_executor_routes_form_filler():
    from player.multi_sequence.worflow_interpreter_modules.executor_modules.core import (
        WorkflowExecutor,
    )
    assert issubclass(WorkflowExecutor, FormFillerMixin)
    assert hasattr(WorkflowExecutor, "_execute_form_filling_node")
