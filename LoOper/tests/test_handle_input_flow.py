"""Input node -> Handle node compatibility.

Covers the two wiring rules that let a passthrough/user-prompt Input node
drive a Handle node's grounding goal:
1. A Handle node consumes the value produced by a connected Input node
   (_resolve_connected_input_goal) and uses it as the grounding prompt.
2. A pure-carry Input node with nothing to inject only hard-fails when a
   non-Handle consumer depends on its value — feeding a Handle alone is not
   a dead end (the Handle grounds on its own goal or asks the user).
"""

import pytest

from player.multi_sequence.worflow_interpreter_modules.executor_modules.input_ops import InputMixin
from player.multi_sequence.worflow_interpreter_modules.executor_modules.handle_ops import HandleMixin


class _FakePortStore:
    def __init__(self):
        self.data = {}

    def get_output(self, chain_id, node_id, port):
        return self.data.get((chain_id, node_id, port))

    def set_output(self, chain_id, node_id, port, value):
        self.data[(chain_id, node_id, port)] = value


class _FakeLLMExecutor:
    def __init__(self):
        self.vars = {}

    def get_variable(self, name, default=None):
        return self.vars.get(name, default)

    def set_variable(self, name, value):
        self.vars[name] = value


class _FakeExecutor(InputMixin, HandleMixin):
    """Binds the real mixins onto a stub with fake stores."""

    def __init__(self, graph):
        self.port_store = _FakePortStore()
        self.chain_id = "chain"
        self.llm_executor = _FakeLLMExecutor()
        self.workflow_graph = graph
        self._node_execution_count = {}

    def _get_input_next_node(self, node, port):
        return "__done__"


def _make_input_node(nid="IN", downstream=None):
    return {
        "type": "input",
        "id": nid,
        "data": {
            "node_id": nid,
            "label": "what do you want to click",
            "default_value": "",
            "user_prompt": "",
            "passthrough": True,
            "agent_modifiable": False,
        },
        "inputs": [],
        "connections": (
            {"output": [{"node_id": downstream}]} if downstream else {}
        ),
    }


def _make_handle_node(hid="HDL", goal="the main submit button", inputs=None):
    return {
        "type": "handle",
        "id": hid,
        "data": {
            "node_id": hid,
            "action_type": "click",
            "goal_description": goal,
            "target_description": "",
            "agent_adaptive": False,
        },
        "inputs": inputs or [],
        "connections": {"output": []},
    }


# ---------------------------------------------------------------------------
# Handle consumes connected Input value
# ---------------------------------------------------------------------------


def test_handle_uses_data_connected_input_value_when_present():
    graph = {
        "IN": _make_input_node(),
        "HDL": _make_handle_node(inputs=[
            {"from_node": "IN", "output_type": "data", "input_port": "data"},
        ]),
    }
    ex = _FakeExecutor(graph)
    ex.port_store.set_output("chain", "IN", "data", "click the red button")

    assert ex._resolve_connected_input_goal(graph["HDL"]) == "click the red button"


def test_handle_ignores_exec_only_and_legacy_output_value():
    # Strict data ports: an Input's value is a Handle goal ONLY over an edge
    # from the Input's 'data' output port.  The base execution edge and the
    # legacy node_<id>_output variable must NOT leak into the goal.
    graph = {
        "IN": _make_input_node(),
        "HDL": _make_handle_node(inputs=[
            {"from_node": "IN", "output_type": "output", "input_port": "input"},
            {"from_node": "IN", "output_type": "data", "input_port": "data"},
        ]),
    }
    ex = _FakeExecutor(graph)
    ex.llm_executor.set_variable("node_IN_output", "legacy value")
    ex.port_store.set_output("chain", "IN", "output", "exec value")
    # Neither the base-port value nor the legacy variable is readable...
    assert ex._resolve_connected_input_goal(graph["HDL"]) == ""
    # ... but the data-port value is.
    ex.port_store.set_output("chain", "IN", "data", "click the blue icon")
    assert ex._resolve_connected_input_goal(graph["HDL"]) == "click the blue icon"
    # And the data variable serves as the read fallback.
    ex.port_store.set_output("chain", "IN", "data", None)
    ex.llm_executor.set_variable("node_IN_data", "click the green button")
    assert ex._resolve_connected_input_goal(graph["HDL"]) == "click the green button"


def test_handle_ignores_non_input_upstream_for_goal():
    # A non-Input upstream (e.g. flow ordering) must not leak into the goal.
    graph = {
        "SEQ": {"id": "SEQ", "type": "sequence"},
        "HDL": _make_handle_node(inputs=[
            {"from_node": "SEQ", "output_type": "output", "input_port": "input"},
        ]),
    }
    ex = _FakeExecutor(graph)
    ex.port_store.set_output("chain", "SEQ", "output", "some flow output")

    assert ex._resolve_connected_input_goal(graph["HDL"]) == ""


def test_handle_without_connected_input_returns_empty():
    ex = _FakeExecutor({})
    assert ex._resolve_connected_input_goal(_make_handle_node()) == ""


# ---------------------------------------------------------------------------
# Empty carry Input feeding a Handle-only downstream does not hard-fail
# ---------------------------------------------------------------------------


def test_carry_input_feeding_handle_only_does_not_fail():
    graph = {"IN": _make_input_node(downstream="HDL"), "HDL": _make_handle_node()}
    ex = _FakeExecutor(graph)

    assert ex._feeds_handle_only(graph["IN"]) is True
    # The strict fail path (return None) must NOT trigger for handle-only.
    assert ex._execute_input_node(graph["IN"], lambda: False) is not None


def test_carry_input_feeding_non_handle_still_hard_fails():
    graph = {
        "IN": _make_input_node(downstream="LLM"),
        "LLM": {"id": "LLM", "type": "llm"},
    }
    ex = _FakeExecutor(graph)

    assert ex._feeds_handle_only(graph["IN"]) is False
    # Pure carry with no value and a data-dependent consumer stays strict.
    assert ex._execute_input_node(graph["IN"], lambda: False) is None


def test_feeds_handle_only_mixed_downstream_is_false():
    graph = {
        "IN": _make_input_node(downstream=None),
        "HDL": _make_handle_node(),
        "LLM": {"id": "LLM", "type": "llm"},
    }
    graph["IN"]["connections"] = {
        "output": [{"node_id": "HDL"}, {"node_id": "LLM"}],
    }
    ex = _FakeExecutor(graph)
    assert ex._feeds_handle_only(graph["IN"]) is False


def test_feeds_handle_only_no_connections_is_false():
    ex = _FakeExecutor({})
    assert ex._feeds_handle_only(_make_input_node()) is False
