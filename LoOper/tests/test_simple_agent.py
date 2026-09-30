"""Unit tests for player/agentic_ops/simple_agent.py (SimpleChainRouter) and
player/agentic_ops/chain_executor.py helper functions.
"""

import json
import os

import pytest

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


def _write_chain(tmp_path, name, collection=""):
    path = tmp_path / name
    path.write_text(json.dumps({
        "name": name.replace(".json", ""),
        "collection": collection,
        "sequences": [],
    }), encoding="utf-8")
    return str(path)


def test_refresh_chains_scans_directory(tmp_path):
    _write_chain(tmp_path, "tool_a.json")
    _write_chain(tmp_path, "tool_b.json", collection="System")
    router = SimpleChainRouter(chains_dir=str(tmp_path))
    assert len(router._chains) == 2
    ids = {c["id"] for c in router._chains}
    assert ids == {"tool_a", "tool_b"}
    sys_chain = [c for c in router._chains if c["collection"] == "System"]
    assert sys_chain[0]["id"] == "tool_b"


def test_refresh_chains_missing_dir():
    router = SimpleChainRouter(chains_dir="C:\\does\\not\\exist")
    assert router._chains == []


def test_refresh_chains_skips_invalid_json(tmp_path):
    (tmp_path / "bad.json").write_text("{invalid", encoding="utf-8")
    _write_chain(tmp_path, "good.json")
    router = SimpleChainRouter(chains_dir=str(tmp_path))
    assert [c["id"] for c in router._chains] == ["good"]


def test_set_system_chain(tmp_path):
    path = _write_chain(tmp_path, "sys.json", collection="System")
    router = SimpleChainRouter(chains_dir=str(tmp_path))
    assert router.set_system_chain(path) is True
    assert router._system_chain_id == "sys"
    assert router.set_system_chain(str(tmp_path / "missing.json")) is False
    assert router._system_chain_path == ""


def test_handle_request_without_system_chain_returns_error(tmp_path):
    router = SimpleChainRouter(chains_dir=str(tmp_path))
    result = router.handle_request("hello")
    assert "No system chain loaded" in result


def test_handle_request_auto_detects_system_chain(tmp_path, monkeypatch):
    _write_chain(tmp_path, "sys.json", collection="System")
    router = SimpleChainRouter(chains_dir=str(tmp_path))
    called = {}

    def fake_run(query, stop_flag=None):
        called["query"] = query
        return "chain response"

    monkeypatch.setattr(router, "_run_system_chain", fake_run)
    assert router.handle_request("hi") == "chain response"
    assert router._system_chain_path.endswith("sys.json")
    assert called["query"] == "hi"


def test_handle_request_stop_flag(tmp_path):
    _write_chain(tmp_path, "sys.json", collection="System")
    router = SimpleChainRouter(chains_dir=str(tmp_path))
    router.handle_request("x")  # triggers auto-detect
    assert router.handle_request("x", stop_flag=lambda: True) == "Cancelled."


def test_callbacks_settable(tmp_path):
    router = SimpleChainRouter(chains_dir=str(tmp_path))
    router.set_ask_user_callback(lambda q: "user answer")
    router.set_output_display_callback(lambda label, content: None)
    assert callable(router._ask_user_callback)
    assert callable(router._output_display_callback)


def test_shutdown_and_del(tmp_path):
    router = SimpleChainRouter(chains_dir=str(tmp_path))
    router.shutdown()  # no-op, no crash


# ---------------------------------------------------------------------------
# Multiple System chains ("coworkers")
# ---------------------------------------------------------------------------


def test_list_system_chains_returns_every_system_chain(tmp_path):
    _write_chain(tmp_path, "alpha.json", collection="System")
    _write_chain(tmp_path, "beta.json", collection="system")  # case-insensitive
    _write_chain(tmp_path, "tool.json", collection="Default")
    router = SimpleChainRouter(chains_dir=str(tmp_path))
    assert [c["id"] for c in router.list_system_chains()] == ["alpha", "beta"]


def test_handle_request_keeps_explicit_selection(tmp_path, monkeypatch):
    _write_chain(tmp_path, "alpha.json", collection="System")
    beta = _write_chain(tmp_path, "beta.json", collection="System")
    router = SimpleChainRouter(chains_dir=str(tmp_path))
    assert router.set_system_chain(beta) is True
    monkeypatch.setattr(router, "_run_system_chain", lambda q, stop_flag=None: "ok")
    assert router.handle_request("hi") == "ok"
    # The user's pick survives the per-request rescan.
    assert router._system_chain_id == "beta"
    assert router._system_chain_path == os.path.normpath(beta)


def test_reader_filters_and_normalises(tmp_path):
    (tmp_path / "sys.json").write_text(
        json.dumps({"collection": " System ", "description": " d "}),
        encoding="utf-8",
    )
    (tmp_path / "tool.json").write_text(
        json.dumps({"collection": "Default"}), encoding="utf-8"
    )
    (tmp_path / "broken.json").write_text("{nope", encoding="utf-8")

    assert [c["id"] for c in read_chains(str(tmp_path))] == ["sys", "tool"]
    sys_chains = read_system_chains(str(tmp_path))
    assert [c["id"] for c in sys_chains] == ["sys"]
    assert sys_chains[0]["name"] == "sys"  # falls back to the file id
    assert sys_chains[0]["description"] == "d"
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
