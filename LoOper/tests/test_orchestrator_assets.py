"""Orchestrator chain assets + goal ledger.

Loads the shipped ORCHESTRATOR.json / BRAIN_*.json exactly like the player
does (WorkflowGraphBuilder), and exercises the goal ledger files.

Run:  python -m pytest LoOper/tests/test_orchestrator_assets.py
"""

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from player.multi_sequence.worflow_interpreter_modules.builder import (  # noqa: E402
    WorkflowGraphBuilder,
)
from player.multi_sequence.worflow_interpreter_modules.executor_modules.dependencies import (  # noqa: E402
    DependenciesMixin,
)
from player.multi_sequence.worflow_interpreter_modules.executor_modules.orchestrator_ops import (  # noqa: E402
    OrchestratorMixin,
)


class _ToolScanner(OrchestratorMixin):
    """Minimal harness for the orchestrator's asset-scanning helpers."""

    def __init__(self, graph):
        self.workflow_graph = graph
        self.sequence_executor = None


_CHAINS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "chains",
)


def _load(name):
    with open(os.path.join(_CHAINS, name), "r", encoding="utf-8") as f:
        return json.load(f)


def _build(cfg):
    return WorkflowGraphBuilder(
        sequences=cfg.get("sequences"),
        conditional_nodes=cfg.get("conditional_nodes"),
        llm_nodes=cfg.get("llm_nodes"),
        chain_import_nodes=cfg.get("chain_import_nodes"),
        code_nodes=cfg.get("code_nodes"),
        container_nodes=cfg.get("container_nodes"),
        context_nodes=cfg.get("context_nodes"),
        input_nodes=cfg.get("input_nodes"),
        handle_nodes=cfg.get("handle_nodes"),
        mcp_nodes=cfg.get("mcp_nodes"),
        output_nodes=cfg.get("output_nodes"),
        web_sequences=cfg.get("web_sequences"),
        form_filler_nodes=cfg.get("form_filler_nodes"),
        orchestrator_nodes=cfg.get("orchestrator_nodes"),
    ).build_workflow_graph()


def test_orchestrator_chain_builds_and_gates_brains():
    """Discovery-based (node ids change whenever the user rewires the chain):
    an orchestrator root, every 'brains'-port chain_import gated as a tool
    provider, the goal arriving via exec + data edges, output → Output node."""
    cfg = _load("ORCHESTRATOR.json")
    assert str(cfg.get("collection", "")).lower() == "system"
    assert cfg.get("is_default") is True

    graph = _build(cfg)

    orch_ids = [nid for nid, n in graph.items() if n.get("type") == "orchestrator"]
    assert len(orch_ids) == 1, f"expected exactly one orchestrator, got {orch_ids}"
    orch = graph[orch_ids[0]]

    brains_inputs = [
        i for i in orch.get("inputs", [])
        if i.get("input_port") == "brains"
    ]
    brain_ids = {i["from_node"] for i in brains_inputs}
    assert brain_ids, "no chain_import is wired to the 'brains' port"
    # Every brain is gated as a tool provider (never runs standalone).
    for bid in brain_ids:
        assert graph[bid]["type"] == "chain_import"
        assert graph[bid].get("tool_provider") is True, f"brain {bid} not gated"
    # Every brain file must resolve on disk (dead wiring would leave the
    # picker choosing a worker that can never run).
    brain_paths = [
        str(graph[bid].get("data", {}).get("chain_file_path") or "")
        for bid in brain_ids
    ]
    assert all(p and os.path.exists(p) for p in brain_paths), brain_paths

    # The goal arrives via both the exec edge and the data edge.
    goal_inputs = [
        i for i in orch.get("inputs", [])
        if i.get("input_port") == "input"
    ]
    assert any(i.get("output_type") == "data" for i in goal_inputs)
    assert any(i.get("output_type") == "output" for i in goal_inputs)

    # The orchestrator's output drives an Output node.
    out_targets = [
        c.get("node_id") for c in (orch.get("connections", {}) or {}).get("output", [])
    ]
    assert any(graph.get(t, {}).get("type") == "output" for t in out_targets)


def test_shipped_router_brains_expose_discoverable_tools():
    """A brain is a TOOL ROUTER or a NESTED orchestrator — and the discovery is
    LEVEL-LOCAL, never a walk down the tree.  What the parent surfaces is what
    the brain ROUTES WITH (an LLM node's 'tools' port, a nested orchestrator's
    'brains' port), never the deterministic 'chains' of a level below: those
    are that level's ACTION SPACE (learned artifacts keep accumulating in
    them) and each level re-runs the ladder with its own discovery.  Flattening
    them upward would make every ancestor re-list its descendants' inventory on
    every activation — OOM for a deep agent (3 brains per level, ten levels)
    before it ever ran.

    Discovery-based on purpose: WHICH chains sit on those ports is the human's
    wiring and changes whenever they rewire.  What is under test is the SCAN
    and its boundary.
    """
    cfg = _load("ORCHESTRATOR.json")
    graph = _build(cfg)
    orch = next(n for n in graph.values() if n.get("type") == "orchestrator")
    scanner = _ToolScanner(graph)
    brains = scanner._orchestrator_collect_brains(orch)

    assert len(brains) >= 2, brains
    by_name = {b["name"].lower(): b for b in brains}
    li = next((b for k, b in by_name.items() if "linkedin" in k), None)
    vis = next((b for k, b in by_name.items() if "vision" in k), None)
    assert li is not None and vis is not None, sorted(by_name)
    # The LinkedIn brain wires its atomic chains on the nested orchestrator's
    # deterministic 'chains' port — that is ITS level, so NOTHING of it may
    # appear in the parent's context.
    assert li["tools"] == [], (
        "a sub-level's chains were flattened into the parent's tools — the "
        "OOM path for deep agents"
    )
    assert "[tools:" not in li["description"]
    assert "linkedin" in li["description"]
    # The Vision brain routes through ITS 'brains' port: those entries belong
    # to the boundary and surface as context — and only those.
    declared = {
        str(n.get("prefix") or "").strip()
        for n in (_load("vision_system_chain.json").get("chain_import_nodes") or [])
    }
    assert {t["alias"] for t in vis["tools"]} <= declared, vis["tools"]
    for t in vis["tools"]:
        assert t["alias"] and os.path.exists(t["chain_file"]), t
    if vis["tools"]:
        assert "[tools:" in vis["description"]


def test_brain_tool_scan_accepts_nested_orchestrator_router(tmp_path):
    """A brain may route through a NESTED Orchestrator node (chain_imports on
    its 'brains' port) instead of an LLM router — the tool scanner must find
    those tools either way.  Its deterministic 'chains' port is NOT a tool
    source: that is the action space of the level below, discovered by that
    level itself (level-local discovery — see
    _orchestrator_collect_brain_tools for the OOM rationale)."""
    tool_chain = tmp_path / "mytool.json"
    tool_chain.write_text(json.dumps(
        {"description": "does the mytool thing"}), encoding="utf-8")
    brain = tmp_path / "brain.json"
    brain.write_text(json.dumps({
        "orchestrator_nodes": [{"node_id": "0xorc", "connections": []}],
        "chain_import_nodes": [{
            "node_id": "0xtool", "prefix": "mytool",
            "chain_file_path": str(tool_chain),
            "connections": [{"target_node_id": "0xorc",
                             "output_port": "output",
                             "input_port": "brains"}],
        }],
    }), encoding="utf-8")

    scanner = _ToolScanner({})
    tools = scanner._orchestrator_collect_brain_tools(str(brain))

    assert [t["alias"] for t in tools] == ["mytool"]
    assert tools[0]["description"] == "does the mytool thing"

    # The deterministic chains port of the nested orchestrator is NOT a tool
    # source: it belongs to that level's own discovery, and listing it here is
    # how a deep agent's context explodes.
    deterministic = tmp_path / "deterministic.json"
    deterministic.write_text(json.dumps({
        "orchestrator_nodes": [{"node_id": "0xorc2", "connections": []}],
        "chain_import_nodes": [{
            "node_id": "0xchain", "prefix": "atomic tool",
            "chain_file_path": str(tool_chain),
            "connections": [{"target_node_id": "0xorc2",
                             "output_port": "output",
                             "input_port": "chains"}],
        }],
    }), encoding="utf-8")

    tools = scanner._orchestrator_collect_brain_tools(str(deterministic))
    assert tools == []


def test_brains_have_descriptions_and_outputs():
    for name in ("BRAIN_SEARCH.json", "BRAIN_NOTES.json"):
        cfg = _load(name)
        assert str(cfg.get("description") or "").strip()
        assert cfg.get("output_nodes"), f"{name} must expose an Output node"


def test_brain_search_has_decision_routing():
    cfg = _load("BRAIN_SEARCH.json")
    graph = _build(cfg)
    din_ids = [
        nid for nid, n in graph.items()
        if n.get("type") == "input" and DependenciesMixin._is_branch_source(n)
    ]
    assert din_ids, "BRAIN_SEARCH has no decision-routed input"
    din = graph[din_ids[0]]
    assert "true" in din["connections"] and "false" in din["connections"]


def test_brain_notes_has_document_input_and_routed_question():
    """Discovery-based: a document-accepting prompt input + a routed yes/no
    question input, whatever ids the user's rewrite gave them."""
    cfg = _load("BRAIN_NOTES.json")
    inputs = list(cfg.get("input_nodes") or [])

    doc = next((i for i in inputs if i.get("accept_documents") is True), None)
    assert doc is not None, "no document-accepting input node"
    assert doc.get("accept_text") is True
    assert str(doc.get("user_prompt") or "").strip()

    ask = next(
        (i for i in inputs
         if i.get("question_mode") == "yes_no" and i.get("route_on_answer") is True),
        None,
    )
    assert ask is not None, "no routed yes/no question input"
    graph = _build(cfg)
    assert DependenciesMixin._is_branch_source(graph[ask["node_id"]]) is True


def test_system_chains_have_exactly_one_entry_node():
    """Interpreter contract: a chain must have exactly ONE starting node.

    Regression: the original BRAIN_NOTES demo left the document input and the
    question input as parallel unconnected blocks - two starting nodes, which
    the interpreter cannot interpret.  Small entry checks: every chain here
    exposes exactly one non-tool-provider node with no incoming edge."""
    for name in ("ORCHESTRATOR.json", "BRAIN_SEARCH.json", "BRAIN_NOTES.json"):
        graph = _build(_load(name))
        incoming = set()
        for n in graph.values():
            for conns in (n.get("connections") or {}).values():
                for c in (conns or []):
                    t = c.get("node_id") or c.get("target_node_id")
                    if t:
                        incoming.add(t)
        entries = [
            nid for nid, n in graph.items()
            if nid not in incoming and not n.get("tool_provider")
        ]
        assert len(entries) == 1, (
            f"{name} has {len(entries)} starting nodes {entries} - "
            "the interpreter requires exactly one"
        )


def test_default_system_chain_selection_prefers_is_default(tmp_path):
    """SimpleChainRouter must pick the is_default System chain over the
    first one on disk (with the old behavior as fallback)."""
    for name, is_default in (("AAA_FIRST.json", False), ("ROOT.json", True)):
        (tmp_path / name).write_text(
            json.dumps({
                "sequences": [],
                "collection": "System",
                "is_default": is_default,
                "name": name,
            }),
            encoding="utf-8",
        )
    from player.agentic_ops.simple_agent import SimpleChainRouter
    router = SimpleChainRouter(model_path="", chains_dir=str(tmp_path))
    picked = {}

    def _fake_run(query, stop_flag=None):
        picked["path"] = router._system_chain_path
        return "ok"

    router._run_system_chain = _fake_run
    router.handle_request("hello")
    assert picked["path"].endswith("ROOT.json")


def test_goal_ledger_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("LOOPER_GOAL_LEDGER", str(tmp_path / "goals.json"))
    from player.agentic_ops import goal_ledger

    g = goal_ledger.start_goal("Find the cheapest monitor")
    assert g["status"] == "open"
    assert goal_ledger.get_active_goal()["id"] == g["id"]

    goal_ledger.record_activation(g["id"], [{"n": 1, "brain": "Search brain"}], "blocked")
    active = goal_ledger.get_active_goal()
    assert active["status"] == "blocked"
    assert active["trace"][0]["brain"] == "Search brain"

    goal_ledger.update_goal(g["id"], status="done")
    assert goal_ledger.get_active_goal() == {}


def test_goal_ledger_corrupt_file_is_empty(tmp_path, monkeypatch):
    p = tmp_path / "goals.json"
    p.write_text("{not json at all", encoding="utf-8")
    monkeypatch.setenv("LOOPER_GOAL_LEDGER", str(p))
    from player.agentic_ops import goal_ledger
    assert goal_ledger.load_goals() == []
