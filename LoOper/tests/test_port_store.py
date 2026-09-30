"""Unit tests for player/multi_sequence/worflow_interpreter_modules/executor_modules/port_store.py —
PortScopedStore and the VariableShim backward-compat layer.
"""

import pytest

from player.multi_sequence.worflow_interpreter_modules.executor_modules.port_store import (
    PortScopedStore,
    VariableShim,
)


@pytest.fixture
def store():
    return PortScopedStore()


# ---------------------------------------------------------------------------
# Port output management
# ---------------------------------------------------------------------------


def test_set_get_output(store):
    store.set_output("chain", "node1", "output", "hello")
    assert store.get_output("chain", "node1", "output") == "hello"
    assert store.get_output("chain", "node1") == "hello"  # default port
    assert store.get_output("chain", "node1", "context") is None
    assert store.get_output("other", "node1", "output") is None


def test_set_output_overwrites(store):
    store.set_output("c", "n", "output", 1)
    store.set_output("c", "n", "output", 2)
    assert store.get_output("c", "n") == 2


# ---------------------------------------------------------------------------
# Connection resolution
# ---------------------------------------------------------------------------


def test_resolve_connection_follows_graph(store):
    store.set_output("c", "upstream", "data", "val")
    node = {"inputs": [{"input_port": "ctx_in", "from_node": "upstream",
                       "output_type": "data"}]}
    assert store.resolve_connection(node, "ctx_in", "c") == "val"


def test_resolve_connection_ignores_base_output_source(store):
    # Base ports carry no data — an exec-only source resolves to None.
    store.set_output("c", "upstream", "output", "val")
    node = {"inputs": [{"input_port": "ctx_in", "from_node": "upstream"}]}
    assert store.resolve_connection(node, "ctx_in", "c") is None


def test_resolve_connection_uses_output_type(store):
    store.set_output("c", "up", "context", "ctxval")
    node = {"inputs": [{"input_port": "ctx_in", "from_node": "up", "output_type": "context"}]}
    assert store.resolve_connection(node, "ctx_in", "c") == "ctxval"


def test_resolve_connection_missing_returns_none(store):
    node = {"inputs": []}
    assert store.resolve_connection(node, "input", "c") is None


def test_resolve_connection_list_dedup(store):
    store.set_output("c", "a", "data", 1)
    store.set_output("c", "b", "data", 2)
    node = {"inputs": [
        {"input_port": "ctx_in", "from_node": "a", "output_type": "data"},
        {"input_port": "ctx2", "from_node": "a", "output_type": "data"},  # duplicate upstream
        {"input_port": "ctx_in", "from_node": "b", "output_type": "data"},
        {"input_port": "x", "from_node": "missing", "output_type": "data"},
    ]}
    resolved = store.resolve_connection_list(node, "c")
    # dedup removes the second 'a' entry; the unresolvable upstream is still
    # listed with a None value (no silent drop)
    assert len(resolved) == 3
    assert resolved[0] == ("a", "ctx_in", 1)
    assert resolved[1] == ("b", "ctx_in", 2)
    assert resolved[2] == ("missing", "x", None)


def test_resolve_connection_list_ignores_base_ports(store):
    # Neither a base 'input' target nor a base 'output' source is a data channel.
    store.set_output("c", "a", "output", 1)
    node = {"inputs": [
        {"input_port": "input", "from_node": "a", "output_type": "output"},
        {"input_port": "ctx_in", "from_node": "a", "output_type": "output"},
    ]}
    assert store.resolve_connection_list(node, "c") == []


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


def test_clear_chain(store):
    store.set_output("c1", "n", "output", 1)
    store.set_output("c1", "n", "context", 2)
    store.set_output("c2", "n", "output", 3)
    assert store.clear_chain("c1") == 2
    assert store.get_output("c1", "n") is None
    assert store.get_output("c2", "n") == 3


def test_clear_node_by_port(store):
    store.set_output("c", "n", "output", 1)
    store.set_output("c", "n", "context", 2)
    assert store.clear_node("c", "n", port="output") == 1
    assert store.clear_node("c", "n", port="output") == 0
    assert store.get_output("c", "n", "context") == 2


def test_clear_node_all_ports(store):
    store.set_output("c", "n", "output", 1)
    store.set_output("c", "n", "context", 2)
    assert store.clear_node("c", "n") == 2


# ---------------------------------------------------------------------------
# Turn history
# ---------------------------------------------------------------------------


def test_add_turn_assigns_sequence_numbers(store):
    t1 = store.add_turn("c", "ctx", {"query": "q1"})
    t2 = store.add_turn("c", "ctx", {"query": "q2"})
    assert t1["turn"] == 1
    assert t2["turn"] == 2
    assert t1["context_node_id"] == "ctx"


def test_get_turns_filters_by_node_and_limit(store):
    for i in range(5):
        store.add_turn("c", "ctx_a", {"i": i})
    store.add_turn("c", "ctx_b", {"i": "b"})
    turns_a = store.get_turns("c", node_id="ctx_a")
    assert len(turns_a) == 5
    limited = store.get_turns("c", node_id="ctx_a", limit=2)
    assert [t["turn"] for t in limited] == [4, 5]
    all_turns = store.get_turns("c")
    assert len(all_turns) == 6


def test_clear_turns(store):
    store.add_turn("c", "ctx", {"query": "q"})
    store.clear_turns("c")
    assert store.get_turns("c") == []
    # Counter reset: next turn starts again at 1
    t = store.add_turn("c", "ctx", {"query": "q"})
    assert t["turn"] == 1


def test_add_turn_trims_with_max_turns(store):
    for i in range(5):
        store.add_turn("c", "ctx", {"i": i}, max_turns=3)
    turns = store.get_turns("c", node_id="ctx")
    assert [t["turn"] for t in turns] == [3, 4, 5]
    # Other nodes' turns are untouched by the node-aware trim
    store.add_turn("c", "other", {"x": 1}, max_turns=3)
    assert len(store.get_turns("c", node_id="other")) == 1
    assert len(store.get_turns("c", node_id="ctx")) == 3


def test_add_turn_no_max_turns_keeps_all(store):
    for i in range(5):
        store.add_turn("c", "ctx", {"i": i})
    assert len(store.get_turns("c", node_id="ctx")) == 5


# ---------------------------------------------------------------------------
# VariableShim
# ---------------------------------------------------------------------------


def test_shim_delegates_node_keys_to_port_store():
    store = PortScopedStore()
    shim = VariableShim(store, chain_id="chain")
    shim.set_variable("node_abc_output", "outval")
    assert store.get_output("chain", "abc", "output") == "outval"
    assert shim.get_variable("node_abc_output") == "outval"


def test_shim_default_value_for_missing():
    shim = VariableShim(PortScopedStore(), chain_id="chain")
    assert shim.get_variable("node_x_output", "default") == "default"


def test_shim_fallback_dict_for_other_keys():
    shim = VariableShim(PortScopedStore(), chain_id="chain")
    shim.set_variable("runtime_flag", 42)
    assert shim.get_variable("runtime_flag") == 42
    assert shim.get_variable("node_x_output") is None


def test_shim_variables_view_combines_both(store):
    shim = VariableShim(store, chain_id="chain")
    shim.set_variable("node_1_output", "v1")
    shim.set_variable("node_1_context", "v2")
    shim.set_variable("plain", "v3")
    view = shim.variables
    assert view["node_1_output"] == "v1"
    assert view["node_1_context"] == "v2"
    assert view["plain"] == "v3"


def test_shim_contains():
    shim = VariableShim(PortScopedStore(), chain_id="chain")
    assert "node_a_output" not in shim
    shim.set_variable("node_a_output", 1)
    assert "node_a_output" in shim


def test_shim_chain_id_change_redirects(store):
    shim = VariableShim(store, chain_id="c1")
    shim.set_variable("node_a_output", "one")
    shim.chain_id = "c2"
    assert shim.get_variable("node_a_output") is None
    assert store.get_output("c1", "a", "output") == "one"
