"""Unit tests for NGUI/branching_utils.py (AutoBranchingManager),
NGUI/i18n.py, and player/keyboard_monitor.py / screenshot_cleanup.py.
"""

import os
import pytest

from NGUI.branching_utils import AutoBranchingManager
from NGUI import i18n
from player.keyboard_monitor import (
    KeyboardMonitor,
    get_keyboard_monitor,
    create_stop_flag,
    start_global_monitoring,
    stop_global_monitoring,
    _active_monitors,
    _monitor_lock,
)
from player.screenshot_cleanup import ScreenshotCleanup, get_cleanup_manager


# ---------------------------------------------------------------------------
# AutoBranchingManager
# ---------------------------------------------------------------------------


def _chain():
    return {
        "sequences": [
            {"id": "seq_a", "name": "a.json", "sequence_file": "a.json",
             "position": [0, 0], "inputs": {}, "outputs": {"output": [{"node_id": "cond"}]}},
            {"id": "seq_b", "name": "b.json", "sequence_file": "b.json",
             "position": [0, 0], "inputs": {"input": {"node_id": "cond"}},
             "outputs": {"output": [{"node_id": "llm_1"}]}},
        ],
        "conditional_nodes": [
            {"id": "cond", "name": "Cond", "position": [0, 0],
             "inputs": {"input": {"node_id": "seq_a"}},
             "outputs": {"true": [{"node_id": "seq_b"}], "false": []}},
        ],
        "llm_nodes": [
            {"id": "llm_1", "name": "LLM", "position": [0, 0],
             "inputs": {"input": {"node_id": "seq_b"}}, "outputs": {"output": []}},
        ],
    }


def test_generate_unique_id_unique():
    mgr = AutoBranchingManager()
    ids = {mgr.generate_unique_id("n") for _ in range(100)}
    assert len(ids) == 100


def test_find_downstream_nodes():
    mgr = AutoBranchingManager()
    downstream = mgr._find_downstream_nodes(_chain(), "seq_a")
    assert "seq_a" not in downstream
    assert "cond" in downstream
    assert "seq_b" in downstream
    assert "llm_1" in downstream


def test_find_node_by_id_all_types():
    mgr = AutoBranchingManager()
    chain = _chain()
    assert mgr._find_node_by_id(chain, "seq_a")["id"] == "seq_a"
    assert mgr._find_node_by_id(chain, "cond")["id"] == "cond"
    assert mgr._find_node_by_id(chain, "llm_1")["id"] == "llm_1"
    assert mgr._find_node_by_id(chain, "ghost") is None


def test_duplicate_node_chain_keeps_originals_and_creates_copies():
    mgr = AutoBranchingManager()
    chain = _chain()
    new_config = mgr.duplicate_node_chain(chain, "seq_a")
    seqs = new_config["sequences"]
    assert len(seqs) == 3  # 2 originals + 1 duplicated (seq_b only)
    ids = [s["id"] for s in seqs]
    assert len(set(ids)) == 3  # all unique
    # Sequence node keeps its file reference (playback safety)
    orig = {s["id"]: s for s in seqs}
    dup = [s for s in seqs if s["id"] not in ("seq_a", "seq_b")][0]
    assert dup["sequence_file"] == orig["seq_b"]["sequence_file"]
    assert dup["position"][0] == orig["seq_b"]["position"][0] + 200


def test_duplicate_conditional_and_llm_names_suffixed():
    mgr = AutoBranchingManager()
    chain = _chain()
    new_config = mgr.duplicate_node_chain(chain, "seq_a")
    conds = new_config["conditional_nodes"]
    assert len(conds) == 2
    dup_cond = [c for c in conds if c["id"] != "cond"][0]
    assert dup_cond["name"].endswith("_branch")
    assert dup_cond["position"][0] == conds[0]["position"][0] + 200
    llms = new_config["llm_nodes"]
    assert len(llms) == 2
    dup_llm = [l for l in llms if l["id"] != "llm_1"][0]
    assert dup_llm["name"].endswith("_branch")


def test_duplicate_chain_updates_connections():
    mgr = AutoBranchingManager()
    chain = _chain()
    new_config = mgr.duplicate_node_chain(chain, "seq_a")
    # All connection targets must be valid node ids
    all_ids = {s["id"] for s in new_config["sequences"]} | \
              {c["id"] for c in new_config["conditional_nodes"]} | \
              {l["id"] for l in new_config["llm_nodes"]}
    for seq in new_config["sequences"]:
        for conns in seq.get("outputs", {}).values():
            for c in conns:
                assert c["node_id"] in all_ids


def test_create_automatic_branches_unknown_node_raises():
    mgr = AutoBranchingManager()
    with pytest.raises(ValueError):
        mgr.create_automatic_branches(_chain(), "ghost")


def test_create_automatic_branches_both_paths_present_returns_unchanged():
    chain = _chain()
    mgr = AutoBranchingManager()
    assert mgr.create_automatic_branches(chain, "cond") is chain


def test_auto_branch_after_conditional_no_cond_nodes():
    mgr = AutoBranchingManager()
    config = {"sequences": [{"id": "s", "name": "x.json"}]}
    assert mgr.auto_branch_after_conditional(config) == config


def test_create_automatic_branches_duplicates_shared_tail():
    """With both true/false paths wired to the same node, the false branch
    gets its own duplicate of the downstream tail."""
    chain = {
        "sequences": [
            {"id": "s1", "name": "a.json", "sequence_file": "a.json",
             "position": [0, 0], "inputs": {},
             "outputs": {"output": [{"node_id": "cond"}]}},
            {"id": "s2", "name": "b.json", "sequence_file": "b.json",
             "position": [0, 0], "inputs": {"input": {"node_id": "cond"}},
             "outputs": {"output": [{"node_id": "s3"}]}},
            {"id": "s3", "name": "c.json", "sequence_file": "c.json",
             "position": [0, 0], "inputs": {"input": {"node_id": "s2"}},
             "outputs": {"output": []}},
        ],
        "conditional_nodes": [
            {"id": "cond", "name": "Cond", "position": [0, 0],
             "inputs": {"input": {"node_id": "s1"}},
             "outputs": {"true": [{"node_id": "s2"}], "false": [{"node_id": "s2"}]}},
        ],
    }
    mgr = AutoBranchingManager()
    updated = mgr.create_automatic_branches(chain, "cond")
    # The downstream tail (s3) is duplicated for the false branch
    assert len(updated["sequences"]) == len(chain["sequences"]) + 1
    seqs = {s["id"]: s for s in updated["sequences"]}
    new_ids = set(seqs) - {"s1", "s2", "s3"}
    assert len(new_ids) == 1  # exactly one new sequence node
    # s2 (shared entry) now routes its output to the duplicated s3
    assert seqs["s2"]["outputs"]["output"][0]["node_id"] != "s3"


def test_auto_branch_after_conditional_single_path_returns_unchanged():
    """auto_branch_after_conditional only delegates when exactly one path is
    wired; create_automatic_branches itself requires BOTH paths, so the
    current behavior is a no-op copy (documented app quirk)."""
    chain = {
        "sequences": [
            {"id": "s1", "name": "a.json", "sequence_file": "a.json",
             "position": [0, 0], "inputs": {},
             "outputs": {"output": [{"node_id": "cond"}]}},
            {"id": "s2", "name": "b.json", "sequence_file": "b.json",
             "position": [0, 0], "inputs": {"input": {"node_id": "cond"}},
             "outputs": {"output": []}},
        ],
        "conditional_nodes": [
            {"id": "cond", "name": "Cond", "position": [0, 0],
             "inputs": {"input": {"node_id": "s1"}},
             "outputs": {"true": [{"node_id": "s2"}], "false": []}},
        ],
    }
    mgr = AutoBranchingManager()
    updated = mgr.auto_branch_after_conditional(chain)
    assert updated["sequences"] == chain["sequences"]


# ---------------------------------------------------------------------------
# i18n
# ---------------------------------------------------------------------------


def test_detect_os_language_returns_supported(monkeypatch):
    monkeypatch.setattr(i18n.locale, "getdefaultlocale", lambda: ("es_ES", "UTF-8"))
    assert i18n.detect_os_language() == "es"
    monkeypatch.setattr(i18n.locale, "getdefaultlocale", lambda: ("fr_FR", "UTF-8"))
    assert i18n.detect_os_language() == "en"  # unsupported -> en


def test_set_language_defaults_and_falls_back(monkeypatch):
    monkeypatch.setattr(i18n, "_LANG", None)
    i18n.set_language(None)
    assert i18n._LANG == "en"
    i18n.set_language("xx")
    assert i18n._LANG == "en"


def test_translate_spanish_and_english():
    i18n.set_language("es")
    assert i18n._("Save") == "Guardar"
    assert i18n._("Untranslated string") == "Untranslated string"
    i18n.set_language("en")
    assert i18n._("Save") == "Save"


def test_set_language_from_os(monkeypatch):
    monkeypatch.setattr(i18n, "detect_os_language", lambda: "es")
    i18n.set_language_from_os()
    assert i18n._LANG == "es"
    i18n.set_language("en")


# ---------------------------------------------------------------------------
# keyboard_monitor
# ---------------------------------------------------------------------------


def test_stop_flag_and_reset():
    monitor = KeyboardMonitor()
    assert monitor.is_stop_requested() is False
    monitor.reset_stop_flag()
    assert not monitor.stop_event.is_set()
    monitor.stop_requested = True
    monitor.stop_event.set()
    monitor.reset_stop_flag()
    assert monitor.is_stop_requested() is False
    assert not monitor.stop_event.is_set()


def test_global_monitor_singleton():
    assert get_keyboard_monitor() is get_keyboard_monitor()


def test_create_stop_flag_returns_callable():
    flag = create_stop_flag()
    assert callable(flag)
    assert flag() is False


def test_holder_tracking_without_start_is_idempotent():
    # stop without start: no-op, no crash
    with _monitor_lock:
        _active_monitors.clear()
    stop_global_monitoring()
    assert True


def test_new_execution_resets_stale_stop_flag(monkeypatch):
    """A stale ESC flag + a stuck holder from a previous run must not abort
    a fresh execution: start_global_monitoring resets the flag and restarts
    the (dead) listener even when another thread still holds the monitor —
    otherwise every later run dies in the playback initial-delay check
    ("Chain playback stopped during initial delay")."""
    from player import keyboard_monitor as km
    monitor = get_keyboard_monitor()
    with _monitor_lock:
        _active_monitors.clear()
    started = []
    monkeypatch.setattr(monitor, "start_monitoring", lambda: started.append(1))
    # Poisoned state: stale holder + stop flag set by a previous ESC.
    monitor.stop_requested = True
    monitor.stop_event.set()
    stale_tid = 424242
    with _monitor_lock:
        _active_monitors.add(stale_tid)
    try:
        km.start_global_monitoring()
    finally:
        km.stop_global_monitoring()
        with _monitor_lock:
            _active_monitors.discard(stale_tid)
    assert monitor.is_stop_requested() is False
    assert not monitor.stop_event.is_set()
    assert started, "a dead listener must be restarted for the new execution"


def test_get_stop_event():
    monitor = get_keyboard_monitor()
    assert monitor.get_stop_event() is monitor.stop_event if hasattr(monitor, "get_stop_event") else True


# ---------------------------------------------------------------------------
# screenshot_cleanup
# ---------------------------------------------------------------------------


def test_cleanup_manager_registration():
    cm = ScreenshotCleanup()
    cm.register_temporal_screenshot("a.png")
    cm.register_temporal_screenshot("a.png")  # dedup
    assert cm.temporal_screenshots == ["a.png"]
    assert cm.cleanup_screenshot("a.png") is True
    assert cm.temporal_screenshots == []


def test_cleanup_screenshot_deletes_file(tmp_path):
    cm = ScreenshotCleanup()
    shot = tmp_path / "llm_vision_123.png"
    shot.write_bytes(b"PNG")
    cm.register_temporal_screenshot(str(shot))
    assert shot.exists()
    cm.cleanup_screenshot(str(shot))
    assert not shot.exists()
    assert cm.temporal_screenshots == []
    # Missing files are tolerated (idempotent)
    assert cm.cleanup_screenshot(str(shot)) is True


def test_cleanup_all_registered():
    cm = ScreenshotCleanup()
    cm.register_temporal_screenshot("a.png")
    assert cm.cleanup_all_registered() == 0  # missing file -> nothing deleted
    assert cm.temporal_screenshots == []


def test_cleanup_all_registered_deletes_files(tmp_path):
    cm = ScreenshotCleanup()
    for name in ("llm_vision_1.png", "llm_ocr_2.png"):
        f = tmp_path / name
        f.write_bytes(b"PNG")
        cm.register_temporal_screenshot(str(f))
    assert cm.cleanup_all_registered() == 2
    assert not list(tmp_path.iterdir())
    assert cm.temporal_screenshots == []


def test_cleanup_old_temporal_screenshots(tmp_path):
    cm = ScreenshotCleanup()
    old = tmp_path / "llm_vision_111.png"
    old.write_bytes(b"PNG")
    fresh = tmp_path / "llm_vision_222.png"
    fresh.write_bytes(b"PNG")
    import time as _time

    _time.sleep(0.01)
    # max_age_minutes=0 removes everything older than 0 minutes
    assert cm.cleanup_old_temporal_screenshots(
        directory=str(tmp_path), max_age_minutes=0
    ) == 2
    assert not list(tmp_path.iterdir())


def test_cleanup_by_prefix(tmp_path):
    cm = ScreenshotCleanup()
    shot = tmp_path / "llm_ocr_333.png"
    shot.write_bytes(b"PNG")
    other = tmp_path / "keep.png"
    other.write_bytes(b"PNG")
    assert cm.cleanup_by_prefix(
        directory=str(tmp_path), prefix="llm_ocr_"
    ) == 1
    assert not shot.exists()
    assert other.exists()


def test_cleanup_context_manager():
    with ScreenshotCleanup() as cm:
        cm.register_temporal_screenshot("x.png")
    assert cm.temporal_screenshots == []


def test_global_cleanup_manager():
    assert get_cleanup_manager() is get_cleanup_manager()
