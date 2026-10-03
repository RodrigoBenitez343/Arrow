"""Config round-trip for the v2 node surface.

Guards the known config-drop pitfall: properties silently lost between
chain save and chain load.  Covers every new Input-node property (decision
toggle, question modes, accepted media) and the legacy Orchestrator-node
migration into LLM 'orchestrator' mode.

Run:  python -m pytest LoOper/tests/test_v2_node_config_roundtrip.py
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from NGUI.graph_elements.config_manager import ConfigManager  # noqa: E402


class _FakeParent:
    """Minimal parent_widget: graph_manager doubles as the node factory."""

    def __init__(self):
        self.created = []
        self.graph_manager = self

    def create_node(self, cls_name, name=""):
        node = _RecordingNode()
        node.class_name = cls_name
        self.created.append(node)
        return node


class _RecordingNode:
    """Captures everything the loader does to a freshly created node."""

    def __init__(self):
        self.id = f"auto_{id(self)}"
        self.props = {}
        self.name_ = ""
        self.pos_ = [0, 0]
        self.decision_ports_rebuilt = 0

    def set_property(self, key, value):
        self.props[key] = value

    def get_property(self, key):
        return self.props.get(key)

    def set_pos(self, *xy):
        self.pos_ = list(xy)

    def set_name(self, name):
        self.name_ = name

    def pos(self):
        return self.pos_

    def rebuild_decision_ports(self):
        self.decision_ports_rebuilt += 1


class _SourceNode:
    """Save-side node: returns a canned config, like the real NGUI nodes."""

    def __init__(self, cfg, node_id):
        self._cfg = dict(cfg)
        self.id = node_id

    def get_input_config(self):
        return dict(self._cfg)

    def get_orchestrator_config(self):
        return dict(self._cfg)

    def pos(self):
        return [11.0, 22.0]


def _save_manager():
    cm = ConfigManager(_FakeParent())
    cm._get_node_connections = lambda node: [{"output_port": "output"}]
    return cm


def _load_manager(chain_config):
    parent = _FakeParent()
    cm = ConfigManager(parent)
    cm.chain_config = chain_config
    return cm, parent


# ---------------------------------------------------------------------------
# Input node v2 properties
# ---------------------------------------------------------------------------

INPUT_V2_CONFIG = {
    "label": "Ask v2",
    "default_value": "",
    "user_prompt": "Proceed?",
    "passthrough": True,
    "web_mode": False,
    "agent_modifiable": False,
    "decision_mode": True,
    "decision_criterion": "requires researching or searching the web",
    "decision_evaluator": "laya",
    "decision_model": "qwen-small",
    "decision_default": True,
    "question_mode": "choice",
    "choices": '[{"label": "Red", "value": "r"}]',
    "route_on_answer": True,
    "accept_text": True,
    "accept_images": True,
    "accept_documents": True,
    "tts_enabled": False,
}


def test_input_v2_props_survive_save_and_load():
    cm = _save_manager()
    cm._save_input_node(_SourceNode(INPUT_V2_CONFIG, "in_v2"))

    entry = cm.chain_config["input_nodes"][0]
    for key, expected in INPUT_V2_CONFIG.items():
        assert entry[key] == expected, f"save dropped/changed {key}"
    assert entry["node_id"] == "in_v2"
    assert entry["connections"] == [{"output_port": "output"}]

    cm2, parent = _load_manager(cm.chain_config)
    node_map = {}
    cm2._create_input_nodes(node_map)

    node = node_map["in_v2"]
    for key, expected in INPUT_V2_CONFIG.items():
        assert node.props[key] == expected, f"load dropped/changed {key}"
    # Branch ports must be rebuilt BEFORE connections restore.
    assert node.decision_ports_rebuilt == 1
    assert node.pos_ == [11.0, 22.0]
    assert node.name_ == "Input: Ask v2 [passthrough] [decide]"
    assert node_map["in_v2"] is node


def test_input_v2_defaults_roundtrip_without_rebuilding_ports():
    """A plain input node (all toggles off) loads exactly as a legacy chain:
    no decision ports rebuilt, media defaults unchanged."""
    cm = _save_manager()
    cm._save_input_node(_SourceNode(
        {"label": "plain", "passthrough": False, "accept_text": True}, "in_plain"))

    entry = cm.chain_config["input_nodes"][0]
    assert entry["decision_mode"] is False
    assert entry["question_mode"] == "text"
    assert entry["route_on_answer"] is False
    assert entry["accept_text"] is True
    assert entry["accept_images"] is False
    assert entry["accept_documents"] is False

    cm2, _ = _load_manager(cm.chain_config)
    node_map = {}
    cm2._create_input_nodes(node_map)
    node = node_map["in_plain"]
    assert node.props["decision_mode"] is False
    assert node.props["question_mode"] == "text"
    assert node.props["accept_images"] is False
    assert node.decision_ports_rebuilt == 0


# ---------------------------------------------------------------------------
# Default-root flag preservation
# ---------------------------------------------------------------------------


class _ChainParent(_FakeParent):
    def get_all_nodes(self):
        return []


def test_is_default_flag_survives_editor_save_cycle():
    """The default-root flag must not be dropped on editor load/save cycles.

    Regression: ORCHESTRATOR.json lost 'is_default' the moment it was saved
    from the graph editor, silently demoting the orchestrator root to "some
    System chain" - the router then boots whichever System chain it finds
    first instead of the flagged root."""
    cm = ConfigManager(_ChainParent())
    cm._current_chain_is_default = True
    cm._current_chain_collection = "System"
    cm.save_current_state()
    assert cm.chain_config.get("is_default") is True

    # A regular chain gains no flag noise from the save path.
    cm2 = ConfigManager(_ChainParent())
    cm2.save_current_state()
    assert "is_default" not in cm2.chain_config


def test_routing_examples_survive_editor_save_cycle():
    """Accumulated routing.examples must not be dropped on editor load/save.

    The editor rebuilds the chain dict from a FIXED key set, so a key it does
    not know is erased on the next save.  The orchestrator writes 'routing'
    into the chain file; without this the accumulated reinforcement would
    silently vanish the moment the user opens and saves the chain."""
    cm = ConfigManager(_ChainParent())
    cm._current_chain_routing = {"examples": ["Click the jobs button"]}
    cm.save_current_state()
    assert cm.chain_config.get("routing") == {
        "examples": ["Click the jobs button"]}

    # No routing -> no key noise injected into a plain chain.
    cm2 = ConfigManager(_ChainParent())
    cm2.save_current_state()
    assert "routing" not in cm2.chain_config


# ---------------------------------------------------------------------------
# Legacy Orchestrator nodes fold into LLM 'orchestrator' mode on load
# ---------------------------------------------------------------------------


def test_orchestrator_nodes_migrate_to_llm_orchestrator_mode():
    cm = ConfigManager(_FakeParent())
    cfg = {
        "llm_nodes": [],
        "input_nodes": [{
            "node_id": "src",
            "connections": [
                {"target_node_id": "o1", "output_port": "output",
                 "input_port": "brains"},
                {"target_node_id": "o1", "output_port": "output",
                 "input_port": "chains"},
            ],
        }],
        "orchestrator_nodes": [{
            "type": "orchestrator", "node_id": "o1", "description": "route",
            "max_steps": 7, "picker": "laya", "model": "m.gguf",
            "connections": [
                {"output_port": "output", "target_node_id": "t1",
                 "input_port": "input"},
            ],
        }],
    }

    cm._migrate_orchestrator_nodes(cfg)

    assert "orchestrator_nodes" not in cfg
    assert len(cfg["llm_nodes"]) == 1
    llm = cfg["llm_nodes"][0]
    assert llm["type"] == "llm"
    assert llm["node_id"] == "o1"
    assert llm["orchestrator_mode"] is True
    assert llm["orch_max_steps"] == 7
    assert llm["model"] == "m.gguf"
    # Edges that landed on the old brains/chains ports now land on 'tools'.
    ports = [c["input_port"] for c in cfg["input_nodes"][0]["connections"]]
    assert ports == ["tools", "tools"]


def test_migration_is_a_noop_without_orchestrator_nodes():
    cm = ConfigManager(_FakeParent())
    cfg = {"llm_nodes": [{"node_id": "l1", "mode": "chat"}]}
    cm._migrate_orchestrator_nodes(cfg)
    assert cfg["llm_nodes"] == [{"node_id": "l1", "mode": "chat"}]
    assert "orchestrator_nodes" not in cfg
