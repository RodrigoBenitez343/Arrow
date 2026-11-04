"""Unit tests for player/agentic_ops/simple_agent.py (SimpleChainRouter),
the workspace registry it now dispatches over, and chain_executor helpers.

Agent mode is bound to a WORKSPACE (one agent + its own chains folder); the
old "System chain" collection is retired, and a workspace that pins a
``legacy_brain`` keeps that behaviour.
"""

import json
import os

import pytest

from player import workspace as ws
from player.agentic_ops.simple_agent import SimpleChainRouter
from player.agentic_ops.system_chains import (
    is_system_chain,
    read_chains,
    read_system_chains,
)
from player.agentic_ops import chain_executor as ce


# ---------------------------------------------------------------------------
# SimpleChainRouter
# ---------------------------------------------------------------------------


def _chains_dir(tmp_path):
    folder = tmp_path / "chains"
    folder.mkdir(exist_ok=True)
    return str(folder)


def _write_chain(chains_dir, name, **extra):
    path = os.path.join(chains_dir, name)
    data = {"name": name.replace(".json", ""), "sequences": []}
    data.update(extra)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle)
    return path


def _workspace(tmp_path, name="Job hunt", agent="Scout", chains=(), brain=""):
    """A workspace with its own chains; returns (chains_dir, manifest)."""
    chains_dir = _chains_dir(tmp_path)
    root = ws.root_for_chains(chains_dir)
    manifest = ws.create(root, name, agent)
    for chain_name in chains:
        _write_chain(ws.chains_dir(root, manifest["slug"]), chain_name)
    if brain:
        stored = ws.load(root, manifest["slug"])
        stored["agent"]["legacy_brain"] = brain
        ws.save(root, stored)
    return chains_dir, manifest


def test_refresh_chains_scans_directory(tmp_path):
    folder = _chains_dir(tmp_path)
    _write_chain(folder, "tool_a.json")
    _write_chain(folder, "tool_b.json")
    router = SimpleChainRouter(chains_dir=folder)
    assert len(router._chains) == 2
    assert {c["id"] for c in router._chains} == {"tool_a", "tool_b"}


def test_refresh_chains_missing_dir():
    router = SimpleChainRouter(chains_dir="C:\\does\\not\\exist")
    assert router._chains == []
    assert router.list_agents() == []


def test_refresh_chains_skips_invalid_json(tmp_path):
    folder = _chains_dir(tmp_path)
    (tmp_path / "chains" / "bad.json").write_text("{invalid", encoding="utf-8")
    _write_chain(folder, "good.json")
    router = SimpleChainRouter(chains_dir=folder)
    assert [c["id"] for c in router._chains] == ["good"]


def test_list_agents_lists_workspaces_not_collections(tmp_path):
    chains_dir, first = _workspace(tmp_path, name="Job hunt", agent="Scout",
                                   chains=("a.json", "b.json"))
    _workspace(tmp_path, name="Writing", agent="Pen")
    router = SimpleChainRouter(chains_dir=chains_dir)

    agents = router.list_agents()
    assert [a["id"] for a in agents] == [first["slug"], "writing"]
    assert agents[0]["name"] == "Job hunt · Scout"
    assert agents[0]["description"] == "routes over 2 chain(s)"
    assert agents[0]["path"].endswith(ws.WORKSPACE_FILE)


def test_a_brain_workspace_reports_its_brain(tmp_path):
    chains_dir, manifest = _workspace(tmp_path, chains=("brain.json",))
    brain = os.path.join(ws.chains_dir(ws.root_for_chains(chains_dir),
                                       manifest["slug"]), "brain.json")
    stored = ws.load(ws.root_for_chains(chains_dir), manifest["slug"])
    stored["agent"]["legacy_brain"] = brain
    ws.save(ws.root_for_chains(chains_dir), stored)

    (agent,) = SimpleChainRouter(chains_dir=chains_dir).list_agents()
    assert agent["description"] == "fixed brain: brain.json"
    assert agent["brain"] == brain


def test_set_agent_binds_agent_mode_to_one_workspace(tmp_path):
    chains_dir, manifest = _workspace(tmp_path, chains=("a.json",))
    router = SimpleChainRouter(chains_dir=chains_dir)
    assert router.set_agent(manifest["slug"]) is True
    assert router.active_agent() == manifest["slug"]
    assert router._system_chain_id == manifest["slug"]
    assert router._system_chain_path.endswith(ws.WORKSPACE_FILE)
    assert router.set_agent("does-not-exist") is False


def test_set_system_chain_accepts_a_workspace_file(tmp_path):
    chains_dir, manifest = _workspace(tmp_path, chains=("a.json",))
    router = SimpleChainRouter(chains_dir=chains_dir)
    workspace_file = os.path.join(ws.workspace_dir(
        ws.root_for_chains(chains_dir), manifest["slug"]), ws.WORKSPACE_FILE)
    assert router.set_system_chain(workspace_file) is True
    assert router.active_agent() == manifest["slug"]


def test_set_system_chain_still_pins_a_brain_chain(tmp_path):
    folder = _chains_dir(tmp_path)
    path = _write_chain(folder, "sys.json")
    router = SimpleChainRouter(chains_dir=folder)
    assert router.set_system_chain(path) is True
    assert router._system_chain_id == "sys"
    assert router._brain_path == os.path.normpath(path)
    assert router.set_system_chain(os.path.join(folder, "missing.json")) is False
    assert router._system_chain_path == ""


def test_handle_request_without_an_agent_returns_error(tmp_path):
    router = SimpleChainRouter(chains_dir=_chains_dir(tmp_path))
    result = router.handle_request("hello")
    assert "No agent available" in result


def test_handle_request_routes_through_the_workspace_agent(tmp_path,
                                                          monkeypatch):
    chains_dir, manifest = _workspace(tmp_path, chains=("a.json",))
    router = SimpleChainRouter(chains_dir=chains_dir)
    called = {}

    def fake_run(query, stop_flag=None):
        called["query"] = query
        called["agent"] = router._workspace_slug
        return "composed answer"

    monkeypatch.setattr(router, "_run_workspace", fake_run)
    assert router.handle_request("hi") == "composed answer"
    assert called["query"] == "hi"
    assert called["agent"] == manifest["slug"]


def test_a_pinned_brain_runs_that_chain(tmp_path, monkeypatch):
    chains_dir, manifest = _workspace(tmp_path, chains=("brain.json",))
    brain = os.path.join(ws.chains_dir(ws.root_for_chains(chains_dir),
                                       manifest["slug"]), "brain.json")
    stored = ws.load(ws.root_for_chains(chains_dir), manifest["slug"])
    stored["agent"]["legacy_brain"] = brain
    ws.save(ws.root_for_chains(chains_dir), stored)

    router = SimpleChainRouter(chains_dir=chains_dir,
                               workspace_slug=manifest["slug"])
    seen = {}

    def fake_run(query, stop_flag=None):
        seen["chain"] = router._brain_path
        return "brain answer"

    monkeypatch.setattr(router, "_run_system_chain", fake_run)
    assert router.handle_request("hi") == "brain answer"
    assert seen["chain"] == brain


def test_handle_request_stop_flag(tmp_path):
    chains_dir, _manifest = _workspace(tmp_path, chains=("a.json",))
    router = SimpleChainRouter(chains_dir=chains_dir)
    assert router.handle_request("x", stop_flag=lambda: True) == "Cancelled."


def test_callbacks_settable(tmp_path):
    router = SimpleChainRouter(chains_dir=_chains_dir(tmp_path))
    router.set_ask_user_callback(lambda q: "user answer")
    router.set_output_display_callback(lambda label, content: None)
    assert callable(router._ask_user_callback)
    assert callable(router._output_display_callback)


def test_shutdown_and_del(tmp_path):
    router = SimpleChainRouter(chains_dir=_chains_dir(tmp_path))
    router.shutdown()  # no-op, no crash


def test_the_agents_choice_survives_a_request(tmp_path):
    chains_dir, first = _workspace(tmp_path, name="One", chains=("a.json",))
    _workspace(tmp_path, name="Two", chains=("b.json",))
    router = SimpleChainRouter(chains_dir=chains_dir)
    assert router.set_agent("two") is True
    assert router.handle_request("hi", stop_flag=lambda: True) == "Cancelled."
    assert router.active_agent() == "two"


def test_reader_filters_and_normalises(tmp_path):
    (tmp_path / "sys.json").write_text(
        json.dumps({"collection": " System ", "description": " typed "}),
        encoding="utf-8",
    )
    (tmp_path / "tool.json").write_text(
        json.dumps({"collection": "Default",
                    "flow": {"summary": "Fills a web form."}}),
        encoding="utf-8",
    )
    (tmp_path / "broken.json").write_text("{nope", encoding="utf-8")

    assert [c["id"] for c in read_chains(str(tmp_path))] == ["sys", "tool"]
    sys_chains = read_system_chains(str(tmp_path))
    assert [c["id"] for c in sys_chains] == ["sys"]
    assert sys_chains[0]["name"] == "sys"  # falls back to the file id
    # The routing line is DERIVED: hand-typed prose is not what readers get.
    assert sys_chains[0]["description"] == ""
    by_id = {c["id"]: c for c in read_chains(str(tmp_path))}
    assert by_id["tool"]["description"].startswith("Fills a web form")
    assert "typed" not in by_id["sys"]["description"]
    assert is_system_chain({"collection": "SYSTEM"}) is True
    assert is_system_chain({"collection": ""}) is False


# ---------------------------------------------------------------------------
# chain_executor helpers
# ---------------------------------------------------------------------------


def test_preprocess_context_output_empty():
    assert ce._preprocess_context_output("") == ""
    assert ce._preprocess_context_output(None) == ""
    assert ce._preprocess_context_output("   ") == ""


def test_preprocess_context_output_plain_text():
    assert ce._preprocess_context_output("  hello world  ") == "hello world"


def test_preprocess_context_output_strips_goal_id_comments():
    text = "before <!-- goal_id:abc123 --> after"
    assert ce._preprocess_context_output(text) == "before  after"


def test_preprocess_context_output_json_dict():
    text = json.dumps({"query": "hello", "result": [{"value": "v1"}, {"value": "v2"}]})
    out = ce._preprocess_context_output(text)
    assert "hello" in out
    assert "v1" in out and "v2" in out


def test_preprocess_context_output_json_list():
    text = json.dumps([{"value": "a"}, "b"])
    out = ce._preprocess_context_output(text)
    assert out == "a\nb"


def test_preprocess_context_output_truncates_long():
    text = json.dumps({"q": "x" * 5000})
    out = ce._preprocess_context_output(text)
    assert len(out) <= 2003  # 2000 + "..."
    assert out.endswith("...")


def test_get_node_output_port_store_first():
    class FakePortStore:
        def get_output(self, chain_id, node_id, port):
            return "port-value"

    class FakeExecutor:
        port_store = FakePortStore()
        chain_id = "chain"
        llm_executor = None

    assert ce._get_node_output(FakeExecutor(), "node1") == "port-value"


def test_get_node_output_legacy_fallback():
    class FakeLLM:
        def get_variable(self, name):
            return "legacy-value" if name == "node_n1_output" else None

    class FakeExecutor:
        port_store = None
        chain_id = "chain"
        llm_executor = FakeLLM()

    assert ce._get_node_output(FakeExecutor(), "n1") == "legacy-value"


def test_get_node_output_none():
    assert ce._get_node_output(None, "x") is None

    class Empty:
        port_store = None
        chain_id = "chain"
        llm_executor = None

    assert ce._get_node_output(Empty(), "x") is None


def test_tag_wraps_goal_id():
    assert ce._tag("text", "goal_1") == "text<!-- goal_id:goal_1 -->"
