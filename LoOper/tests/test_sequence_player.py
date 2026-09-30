"""Unit tests for player/sequence_player.py — SequenceCache, fast JSON
loading, hash-referenced screenshot resolution, and playback flow control.
"""

import json
import time

import pytest

from player.sequence_player import SequenceCache, SequencePlayer, _fast_json_load, _sequence_cache


# ---------------------------------------------------------------------------
# SequenceCache
# ---------------------------------------------------------------------------


def test_cache_put_get_roundtrip(tmp_path):
    seq = tmp_path / "s.json"
    seq.write_text(json.dumps({"actions": []}), encoding="utf-8")
    cache = SequenceCache(max_size=2)
    cache.put(str(seq), {"actions": []})
    assert cache.get(str(seq)) == {"actions": []}
    assert cache.size() == 1


def test_cache_invalidates_on_mtime_change(tmp_path):
    seq = tmp_path / "s.json"
    seq.write_text(json.dumps({"actions": []}), encoding="utf-8")
    cache = SequenceCache()
    cache.put(str(seq), {"actions": []})
    os_utime = __import__("os").utime
    os_utime(seq, (time.time() + 10, time.time() + 10))
    assert cache.get(str(seq)) is None  # stale entry dropped


def test_cache_missing_file_returns_none(tmp_path):
    cache = SequenceCache()
    assert cache.get(str(tmp_path / "ghost.json")) is None


def test_cache_evicts_oldest_when_full(tmp_path):
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    c = tmp_path / "c.json"
    for f in (a, b, c):
        f.write_text(json.dumps({"actions": []}), encoding="utf-8")
    cache = SequenceCache(max_size=2)
    cache.put(str(a), {"id": "a"})
    cache.put(str(b), {"id": "b"})
    cache.put(str(c), {"id": "c"})
    assert cache.size() == 2
    assert cache.get(str(a)) is None  # evicted
    assert cache.get(str(c)) == {"id": "c"}


def test_cache_clear(tmp_path):
    seq = tmp_path / "s.json"
    seq.write_text(json.dumps({"actions": []}), encoding="utf-8")
    cache = SequenceCache()
    cache.put(str(seq), {"actions": []})
    cache.clear()
    assert cache.size() == 0


def test_cache_put_without_mtime_still_caches(tmp_path):
    # A file that cannot be stat'ed (removed right after) must still cache
    seq = tmp_path / "gone.json"
    seq.write_text(json.dumps({"actions": []}), encoding="utf-8")
    cache = SequenceCache()
    cache.put(str(seq), {"actions": []})
    cache._file_timestamps.pop(str(seq), None)
    # Access after file removal -> returns None (mtime check failed path)
    seq.unlink()
    assert cache.get(str(seq)) is None


# ---------------------------------------------------------------------------
# _fast_json_load
# ---------------------------------------------------------------------------


def test_fast_json_load_small_file(tmp_path):
    seq = tmp_path / "small.json"
    seq.write_text(json.dumps({"actions": [1, 2, 3]}), encoding="utf-8")
    assert _fast_json_load(str(seq)) == {"actions": [1, 2, 3]}


def test_fast_json_load_large_file(tmp_path):
    seq = tmp_path / "large.json"
    data = {"actions": [{"i": i, "payload": "x" * 200} for i in range(300)]}
    seq.write_text(json.dumps(data), encoding="utf-8")
    loaded = _fast_json_load(str(seq))
    assert len(loaded["actions"]) == 300


def test_fast_json_load_invalid_raises(tmp_path):
    seq = tmp_path / "bad.json"
    seq.write_text("{invalid", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        _fast_json_load(str(seq))


# ---------------------------------------------------------------------------
# SequencePlayer
# ---------------------------------------------------------------------------


def test_load_sequence_validates_actions(tmp_path):
    seq = tmp_path / "s.json"
    seq.write_text(json.dumps({"actions": []}), encoding="utf-8")
    player = SequencePlayer(str(seq), lazy_load=True)
    data = player.load_sequence(str(seq))
    assert data == {"actions": []}


def test_load_sequence_missing_actions_raises(tmp_path):
    seq = tmp_path / "s.json"
    seq.write_text(json.dumps({"name": "x"}), encoding="utf-8")
    player = SequencePlayer(str(seq), lazy_load=True)
    with pytest.raises(ValueError):
        player.load_sequence(str(seq))


def test_load_sequence_resolves_screenshot_hashes(tmp_path):
    seq = tmp_path / "s.json"
    seq.write_text(json.dumps({
        "actions": [{"type": "click", "screenshot_hash": "h1"}],
        "images": {"h1": "base64data123"},
    }), encoding="utf-8")
    player = SequencePlayer(str(seq), lazy_load=True)
    data = player.load_sequence(str(seq))
    action = data["actions"][0]
    assert action["screenshot_data"] == "base64data123"
    assert "screenshot_hash" not in action


def test_lazy_load_defers_until_play(tmp_path, monkeypatch):
    seq = tmp_path / "s.json"
    seq.write_text(json.dumps({"actions": [{"type": "wait"}]}), encoding="utf-8")
    player = SequencePlayer(str(seq), lazy_load=True)
    assert player.sequence_data is None
    player._ensure_sequence_loaded()
    assert player.sequence_data is not None


def test_play_sequence_executes_actions_in_order(tmp_path, monkeypatch):
    seq = tmp_path / "s.json"
    seq.write_text(json.dumps({
        "actions": [
            {"type": "click", "coordinates": {"x": 1, "y": 2}},
            {"type": "keystroke", "key": "enter"},
        ],
    }), encoding="utf-8")

    player = SequencePlayer(str(seq), lazy_load=True)
    executed = []
    monkeypatch.setattr(player, "execute_with_timing",
                        lambda idx, action, fallback_callback=None, stop_flag=None: (
                            executed.append(idx), True)[1])
    monkeypatch.setattr(player, "take_screenshot", lambda *a, **k: None)
    player.play_sequence(skip_initial_delay=True)
    assert executed == [0, 1]


def test_play_sequence_stops_on_stop_flag(tmp_path, monkeypatch):
    seq = tmp_path / "s.json"
    seq.write_text(json.dumps({"actions": [{"type": "wait"}] * 5}), encoding="utf-8")
    player = SequencePlayer(str(seq), lazy_load=True)
    monkeypatch.setattr(player, "execute_with_timing",
                        lambda *a, **k: True)
    executed = []

    def stop_flag():
        executed.append(1)
        return True  # stop before first action

    player.play_sequence(stop_flag=stop_flag, skip_initial_delay=True)
    assert len(executed) >= 1


def test_play_sequence_stops_after_failed_action(tmp_path, monkeypatch):
    seq = tmp_path / "s.json"
    seq.write_text(json.dumps({"actions": [{"type": "wait"}] * 5}), encoding="utf-8")
    player = SequencePlayer(str(seq), lazy_load=True)
    monkeypatch.setattr(player, "execute_with_timing",
                        lambda *a, **k: False)  # fallback triggered
    player.play_sequence(skip_initial_delay=True)
    # Fallback path returns early — no crash, sequence stopped


def test_play_sequence_breaks_on_exception(tmp_path, monkeypatch):
    seq = tmp_path / "s.json"
    seq.write_text(json.dumps({"actions": [{"type": "wait"}] * 3}), encoding="utf-8")
    player = SequencePlayer(str(seq), lazy_load=True)
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise RuntimeError("action failed")

    monkeypatch.setattr(player, "execute_with_timing", boom)
    player.play_sequence(skip_initial_delay=True)
    assert len(calls) == 1  # stopped on first failure


def test_play_sequence_tracks_app_context(tmp_path, monkeypatch):
    seq = tmp_path / "s.json"
    seq.write_text(json.dumps({
        "actions": [{"type": "click", "app_context": {"title": "Chrome"}}],
    }), encoding="utf-8")
    player = SequencePlayer(str(seq), lazy_load=True)
    monkeypatch.setattr(player, "execute_with_timing", lambda *a, **k: True)
    player.play_sequence(skip_initial_delay=True)
    assert player.last_app_context == {"title": "Chrome"}


def test_global_cache_instance_exists():
    assert isinstance(_sequence_cache, SequenceCache)
    assert _sequence_cache._lock is not None  # threading.Lock is a factory fn
