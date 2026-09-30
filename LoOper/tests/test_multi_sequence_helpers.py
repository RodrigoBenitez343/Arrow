"""Unit tests for the multi_sequence pipeline helpers:
- conditional_fallback_resources/path_resolver.py (PathResolver)
- conditional_fallback_resources/validation_cache.py (ValidationCache)
- worflow_interpreter_modules/navigator.py (WorkflowNavigator)
- llm_executor_resources/config_utils.py (get_default_api_url)
"""

import json
import os

import pytest

from player.multi_sequence.conditional_fallback_resources.path_resolver import PathResolver
from player.multi_sequence.conditional_fallback_resources.validation_cache import ValidationCache
from player.multi_sequence.worflow_interpreter_modules.navigator import WorkflowNavigator
from player.multi_sequence.llm_executor_resources import config_utils


# ---------------------------------------------------------------------------
# PathResolver
# ---------------------------------------------------------------------------


def test_resolve_path_returns_existing_absolute(tmp_path):
    f = tmp_path / "x.png"
    f.write_bytes(b"PNG")
    resolver = PathResolver(str(tmp_path))
    assert resolver.resolve_path(str(f)) == str(f)


def test_resolve_path_relative_to_chain_dir(tmp_path):
    target = tmp_path / "img.png"
    target.write_bytes(b"PNG")
    resolver = PathResolver(str(tmp_path))
    assert resolver.resolve_path("img.png") == str(target)


def test_resolve_path_adds_json(tmp_path):
    target = tmp_path / "sequences" / "seq.json"
    target.parent.mkdir()
    target.write_text(json.dumps({"actions": []}), encoding="utf-8")
    resolver = PathResolver(str(tmp_path))
    assert resolver.resolve_path("seq", add_json=True) == str(target)


def test_resolve_path_unresolvable_returns_original(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    resolver = PathResolver(str(tmp_path))
    assert resolver.resolve_path("ghost.png") == "ghost.png"


def test_resolve_path_empty_returns_empty():
    resolver = PathResolver(".")
    assert resolver.resolve_path("") == ""


def test_resolve_code_path(tmp_path):
    script = tmp_path / "scripts" / "tool.py"
    script.parent.mkdir()
    script.write_text("print(1)", encoding="utf-8")
    resolver = PathResolver(str(tmp_path))
    assert resolver.resolve_code_path("tool.py") == str(script)


def test_resolve_node_paths_code_node(tmp_path):
    script = tmp_path / "scripts" / "tool.py"
    script.parent.mkdir()
    script.write_text("print(1)", encoding="utf-8")
    resolver = PathResolver(str(tmp_path))
    resolved = resolver.resolve_node_paths({
        "type": "CodeNode",
        "data": {"file_path": "tool.py"},
    })
    assert resolved["file_path"] == str(script)


def test_resolve_node_paths_conditional_loop(tmp_path):
    seq = tmp_path / "sequences" / "loop.json"
    seq.parent.mkdir()
    seq.write_text(json.dumps({"actions": []}), encoding="utf-8")
    resolver = PathResolver(str(tmp_path))
    resolved = resolver.resolve_node_paths({
        "type": "conditional",
        "data": {"trigger_type": "loop", "sequence_file": "loop.json"},
    })
    assert resolved["sequence_file"] == str(seq)


def test_resolve_node_paths_unknown_type_returns_empty():
    resolver = PathResolver(".")
    assert resolver.resolve_node_paths({"type": "mystery", "data": {}}) == {}


def test_set_get_chain_file_dir(tmp_path):
    resolver = PathResolver(str(tmp_path))
    assert resolver.get_chain_file_dir() == str(tmp_path)
    resolver.set_chain_file_dir("/other")
    assert resolver.get_chain_file_dir() == "/other"


# ---------------------------------------------------------------------------
# ValidationCache
# ---------------------------------------------------------------------------


def test_screen_hash_consistent_for_same_input():
    vc = ValidationCache()
    import numpy as np
    screen = np.zeros((300, 400, 3), dtype=np.uint8)
    h1 = vc.get_screen_hash(screen)
    h2 = vc.get_screen_hash(screen)
    assert h1 == h2 and h1 is not None


def test_screen_hash_changes_with_content():
    vc = ValidationCache()
    import numpy as np
    a = np.zeros((300, 400, 3), dtype=np.uint8)
    b = np.full((300, 400, 3), 255, dtype=np.uint8)
    assert vc.get_screen_hash(a) != vc.get_screen_hash(b)


def test_screen_hash_none_without_screen():
    vc = ValidationCache()
    assert vc.get_screen_hash(None, template_matcher=None) is None


def test_cache_key_deterministic():
    vc = ValidationCache()
    cfg = {"image_path": "a.png", "confidence": 0.8, "trigger_type": "presence"}
    k1 = vc.get_validation_cache_key(cfg)
    k2 = vc.get_validation_cache_key(cfg)
    assert k1 == k2
    assert len(k1) == 32  # md5 hex


def test_cache_key_changes_with_screen_hash():
    vc = ValidationCache()
    cfg = {"image_path": "a.png", "confidence": 0.8, "trigger_type": "presence"}
    assert vc.get_validation_cache_key(cfg, "screen1") != vc.get_validation_cache_key(cfg, "screen2")


def test_cached_result_disabled_currently_returns_none():
    # The cache intentionally returns None to force real screen checks
    vc = ValidationCache()
    assert vc.get_cached_validation_result({"image_path": "x.png"}) is None


def test_cache_validation_result_is_noop():
    vc = ValidationCache()
    vc.cache_validation_result({"image_path": "x.png"}, True)
    assert vc._validation_cache == {}


def test_clear_cache():
    vc = ValidationCache()
    vc._validation_cache["k"] = {"result": True, "timestamp": 0}
    vc.clear_cache()
    assert vc._validation_cache == {}


# ---------------------------------------------------------------------------
# WorkflowNavigator
# ---------------------------------------------------------------------------


def _graph():
    return {
        "start": {"type": "sequence", "index": 0, "inputs": [], "connections": {"output": [{"node_id": "mid"}]}},
        "mid": {"type": "sequence", "index": 1, "inputs": [{"from_node": "start"}], "connections": {}},
        "cond": {"type": "conditional", "index": 2, "inputs": [{"from_node": "mid"}], "connections": {}},
    }


def test_find_starting_node():
    nav = WorkflowNavigator(_graph())
    assert nav.find_starting_node() == "start"


def test_find_starting_node_empty_graph():
    assert WorkflowNavigator({}).find_starting_node() is None


def test_find_starting_node_falls_back_to_sequence():
    graph = {
        "s1": {"type": "sequence", "index": 0, "inputs": [{"from_node": "s2"}]},
        "s2": {"type": "sequence", "index": 1, "inputs": [{"from_node": "s1"}]},
    }
    assert WorkflowNavigator(graph).find_starting_node() == "s1"


def test_find_starting_node_falls_back_to_llm():
    graph = {
        "l1": {"type": "llm", "inputs": [{"from_node": "l2"}]},
        "l2": {"type": "llm", "inputs": [{"from_node": "l1"}]},
    }
    assert WorkflowNavigator(graph).find_starting_node() == "l1"


def test_find_next_sequence_node_explicit_connection():
    nav = WorkflowNavigator(_graph())
    assert nav.find_next_sequence_node({"index": 0}) == "mid"


def test_find_next_sequence_node_by_index():
    graph = {
        "a": {"type": "sequence", "index": 0, "inputs": [], "connections": {}},
        "b": {"type": "sequence", "index": 1, "inputs": [], "connections": {}},
    }
    nav = WorkflowNavigator(graph)
    assert nav.find_next_sequence_node({"index": 0}) == "b"


def test_find_next_sequence_node_end():
    nav = WorkflowNavigator(_graph())
    assert nav.find_next_sequence_node({"index": 2}) is None


def test_get_node_connections_and_lookup():
    nav = WorkflowNavigator(_graph())
    assert nav.get_node_connections("start") == [{"node_id": "mid"}]
    assert nav.get_node_connections("start", "true") == []
    assert nav.get_node_connections("ghost") == []
    assert nav.get_node_by_id("mid")["type"] == "sequence"
    assert nav.get_node_by_id("ghost") is None


def test_get_nodes_by_type():
    nav = WorkflowNavigator(_graph())
    seqs = nav.get_nodes_by_type("sequence")
    conds = nav.get_nodes_by_type("conditional")
    assert len(seqs) == 2
    assert len(conds) == 1


# ---------------------------------------------------------------------------
# config_utils.get_default_api_url
# ---------------------------------------------------------------------------


def test_get_default_api_url_direct_mode_raises(monkeypatch):
    monkeypatch.setenv("ARROW_DIRECT_ENGINE", "1")
    with pytest.raises(RuntimeError):
        config_utils.get_default_api_url()


def test_get_default_api_url_env_based(monkeypatch):
    monkeypatch.delenv("ARROW_DIRECT_ENGINE", raising=False)
    monkeypatch.delenv("OVERWATCH_API_URL", raising=False)  # load_ai_config syncs it
    monkeypatch.setenv("API_PORT", "9999")
    import AI.config_loader as cl

    def boom():
        raise RuntimeError("no config")

    monkeypatch.setattr(cl, "get_api_url", boom)
    assert config_utils.get_default_api_url() == "http://localhost:9999"


def test_get_default_api_url_overwatch_env(monkeypatch):
    monkeypatch.delenv("ARROW_DIRECT_ENGINE", raising=False)
    monkeypatch.setenv("OVERWATCH_API_URL", "http://192.168.1.10:8080/")
    import AI.config_loader as cl

    def boom():
        raise RuntimeError("no config")

    monkeypatch.setattr(cl, "get_api_url", boom)
    assert config_utils.get_default_api_url() == "http://192.168.1.10:8080"


# ---------------------------------------------------------------------------
# Loop-boundary stale-channel cleanup (core.WorkflowExecutor)
# ---------------------------------------------------------------------------


def test_clear_node_legacy_vars_drops_input_value_channels():
    """A stale node must not keep any flat value channel across a loop
    boundary — an Input node's value lives in ``node_<id>_data`` and would
    otherwise be concatenated into the next iteration's LLM context instead
    of being replaced by the fresh value."""
    from types import SimpleNamespace
    from player.multi_sequence.worflow_interpreter_modules.executor_modules.core import (
        WorkflowExecutor,
    )

    ex = WorkflowExecutor.__new__(WorkflowExecutor)
    ex.llm_executor = SimpleNamespace(variables={
        "node_IN_data": "first query",
        "node_IN_input_context": {"input": "first query"},
        "node_IN_is_input_passthrough": True,
        "node_IN_output": "legacy",
        "node_IN_context": "legacy",
        "node_OTHER_data": "keep me",
        "unrelated": 1,
    })

    ex._clear_node_legacy_vars("IN")

    assert "node_IN_data" not in ex.llm_executor.variables
    assert "node_IN_input_context" not in ex.llm_executor.variables
    assert "node_IN_is_input_passthrough" not in ex.llm_executor.variables
    assert "node_IN_output" not in ex.llm_executor.variables
    assert "node_IN_context" not in ex.llm_executor.variables
    # Other nodes and unrelated variables are untouched.
    assert ex.llm_executor.variables["node_OTHER_data"] == "keep me"
    assert ex.llm_executor.variables["unrelated"] == 1


# ---------------------------------------------------------------------------
# SequenceExecutor: per-sequence Click Drift plumbing
# ---------------------------------------------------------------------------


def _execute_with_player(tmp_path, monkeypatch, item):
    """Run execute_sequence() for *item* against one real SequencePlayer."""
    from player.multi_sequence import sequence_executor as se

    seq = tmp_path / "s.json"
    seq.write_text(json.dumps({"actions": [{"type": "click"}]}), encoding="utf-8")
    player = se.SequencePlayer(str(seq))
    played = []
    player.play_sequence = lambda **k: played.append(True)

    class _FixedPlayer:
        def __new__(cls, *a, **k):
            return player  # every construction hands back the same instance

    monkeypatch.setattr(se, "SequencePlayer", _FixedPlayer)
    executor = se.SequenceExecutor(chain_file_dir=str(tmp_path))
    ok = executor.execute_sequence(dict(item, sequence_file=str(seq)), stop_flag=None)
    assert ok is True and played
    return player


def test_sequence_click_drift_reaches_the_player(tmp_path, monkeypatch):
    player = _execute_with_player(
        tmp_path, monkeypatch,
        {"loop_count": 1, "click_drift_min": 0, "click_drift_max": 0},
    )
    assert (player.click_drift_min, player.click_drift_max) == (0.0, 0.0)


def test_sequence_without_drift_keeps_player_defaults(tmp_path, monkeypatch):
    player = _execute_with_player(tmp_path, monkeypatch, {"loop_count": 1})
    assert not hasattr(player, "click_drift_min")
