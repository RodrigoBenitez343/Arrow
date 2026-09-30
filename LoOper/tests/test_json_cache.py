"""Unit tests for player/json_cache.py — JSONCache + path resolution logic.

Covers: sequence-vs-chain shape discrimination, multi-strategy path
resolution (including the chains/ vs sequences/ name collision), preload
of sequences and chain imports, mtime-based invalidation, and the
invalidate_file/invalidate_directory cache hygiene.
"""

import json
import os

import pytest

from player import json_cache as jc


# ---------------------------------------------------------------------------
# _is_sequence_shape
# ---------------------------------------------------------------------------


def test_sequence_shape_detection(tmp_path):
    seq = tmp_path / "seq.json"
    seq.write_text(json.dumps({"actions": []}), encoding="utf-8")
    chain = tmp_path / "chain.json"
    chain.write_text(json.dumps({"sequences": []}), encoding="utf-8")
    assert jc._is_sequence_shape(str(seq)) is True
    assert jc._is_sequence_shape(str(chain)) is False
    assert jc._is_sequence_shape(str(tmp_path / "missing.json")) is False
    assert jc._is_sequence_shape(str(tmp_path / "bad.json")) is False  # non-dict


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def test_resolve_sequence_bare_name_in_sequences_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sequences").mkdir()
    seq = tmp_path / "sequences" / "CLOSE.json"
    seq.write_text(json.dumps({"actions": []}), encoding="utf-8")
    # chains/CLOSE.json exists too but is chain-shaped — must NOT win
    (tmp_path / "chains").mkdir()
    (tmp_path / "chains" / "CLOSE.json").write_text(
        json.dumps({"sequences": []}), encoding="utf-8")

    cache = jc.JSONCache()
    resolved = cache._resolve_sequence_path("CLOSE.json", str(tmp_path / "chains"))
    assert resolved == str(seq)
    assert "chains" not in resolved


def test_resolve_sequence_ignores_chain_shaped_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sequences").mkdir()
    only_chain = tmp_path / "chains"
    only_chain.mkdir()
    (only_chain / "x.json").write_text(json.dumps({"sequences": []}), encoding="utf-8")
    cache = jc.JSONCache()
    assert cache._resolve_sequence_path("x.json", str(only_chain)) is None


def test_resolve_sequence_numbered_suffix(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sequences").mkdir()
    seq = tmp_path / "sequences" / "1.json"
    seq.write_text(json.dumps({"actions": []}), encoding="utf-8")
    cache = jc.JSONCache()
    # "1.json 2" — numbered reference must be stripped
    resolved = cache._resolve_sequence_path("1.json 2", str(tmp_path))
    assert resolved == str(seq)


def test_resolve_sequence_appends_json_extension(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sequences").mkdir()
    seq = tmp_path / "sequences" / "test.json"
    seq.write_text(json.dumps({"actions": []}), encoding="utf-8")
    cache = jc.JSONCache()
    assert cache._resolve_sequence_path("test", str(tmp_path)) == str(seq)


def test_resolve_sequence_absolute_path(tmp_path):
    seq = tmp_path / "abs.json"
    seq.write_text(json.dumps({"actions": []}), encoding="utf-8")
    cache = jc.JSONCache()
    assert cache._resolve_sequence_path(str(seq)) == str(seq)


def test_path_cache_hit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sequences").mkdir()
    seq = tmp_path / "sequences" / "hit.json"
    seq.write_text(json.dumps({"actions": []}), encoding="utf-8")
    cache = jc.JSONCache()
    first = cache._resolve_sequence_path("hit.json", str(tmp_path))
    second = cache._resolve_sequence_path("hit.json", str(tmp_path))
    assert first == second == str(seq)
    assert len(cache._path_cache) == 1


# ---------------------------------------------------------------------------
# Sequence caching / mtime invalidation
# ---------------------------------------------------------------------------


def test_get_sequence_loads_and_caches(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sequences").mkdir()
    seq = tmp_path / "sequences" / "a.json"
    seq.write_text(json.dumps({"actions": [{"type": "wait"}]}), encoding="utf-8")

    cache = jc.JSONCache()
    data = cache.get_sequence("a.json", str(tmp_path))
    assert data["actions"] == [{"type": "wait"}]
    assert cache.get_cache_stats()["sequences_cached"] == 1


def test_get_sequence_returns_none_when_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cache = jc.JSONCache()
    assert cache.get_sequence("ghost.json", str(tmp_path)) is None


def test_get_sequence_reloads_after_mtime_change(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sequences").mkdir()
    seq = tmp_path / "sequences" / "b.json"
    seq.write_text(json.dumps({"actions": [{"type": "wait"}]}), encoding="utf-8")

    cache = jc.JSONCache()
    first = cache.get_sequence("b.json", str(tmp_path))
    assert len(first["actions"]) == 1

    os.utime(seq, (os.path.getmtime(seq) + 10, os.path.getmtime(seq) + 10))
    second = cache.get_sequence("b.json", str(tmp_path))
    assert second is not None


# ---------------------------------------------------------------------------
# Chain preloading (sequences + chain imports)
# ---------------------------------------------------------------------------


def test_preload_chain_loads_sequences_and_imports(
        tmp_path, monkeypatch, make_chain_file):
    monkeypatch.chdir(tmp_path)
    seq_dir = tmp_path / "sequences"
    seq_dir.mkdir()
    (seq_dir / "main.json").write_text(
        json.dumps({"actions": [{"type": "wait"}]}), encoding="utf-8")
    (seq_dir / "imported.json").write_text(
        json.dumps({"actions": [{"type": "wait"}]}), encoding="utf-8")
    make_chain_file("sub.json", sequences=[
        {"id": "s1", "sequence_file": "imported.json"}])
    chain = make_chain_file("main.json", sequences=[
        {"id": "s1", "name": "seq: main.json"},
    ], chain_import_nodes=[
        {"chain_file": "sub.json"},
    ])

    cache = jc.JSONCache()
    config = cache.preload_chain(chain)
    assert config["sequences"][0]["id"] == "s1"
    stats = cache.get_cache_stats()
    assert stats["sequences_cached"] == 2
    assert stats["chains_cached"] == 2


def test_preload_chain_missing_file_raises(tmp_path):
    cache = jc.JSONCache()
    with pytest.raises(FileNotFoundError):
        cache.preload_chain(str(tmp_path / "nope.json"))


def test_preload_chain_cycle_safe(tmp_path, monkeypatch, make_chain_file):
    monkeypatch.chdir(tmp_path)
    a = make_chain_file("a.json", chain_import_nodes=[{"chain_file": "b.json"}])
    b = make_chain_file("b.json", chain_import_nodes=[{"chain_file": "a.json"}])
    cache = jc.JSONCache()
    cache.preload_chain(a)
    # Second preload of the same chain must not recurse infinitely
    cache.preload_chain(a)
    assert cache.get_chain(a) is not None
    assert cache.get_chain(b) is not None


def test_get_chain_returns_copy(tmp_path, monkeypatch, make_chain_file):
    monkeypatch.chdir(tmp_path)
    chain = make_chain_file("c.json", sequences=[])
    cache = jc.JSONCache()
    cache.preload_chain(chain)
    first = cache.get_chain(chain)
    first["mutated"] = True
    second = cache.get_chain(chain)
    assert "mutated" not in second  # copy isolation


# ---------------------------------------------------------------------------
# Invalidation
# ---------------------------------------------------------------------------


def test_invalidate_file_removes_entries(tmp_path, monkeypatch, make_chain_file):
    monkeypatch.chdir(tmp_path)
    chain = make_chain_file("d.json", sequences=[])
    cache = jc.JSONCache()
    cache.preload_chain(chain)
    assert cache.get_chain(chain) is not None

    cache.invalidate_file(chain)
    assert cache.get_chain(chain) is None


def test_invalidate_directory(tmp_path, monkeypatch, make_chain_file):
    monkeypatch.chdir(tmp_path)
    chain = make_chain_file("e.json", sequences=[])
    cache = jc.JSONCache()
    cache.preload_chain(chain)
    cache.invalidate_directory(str(tmp_path))
    assert cache.get_chain(chain) is None


def test_chain_depends_on_file(tmp_path, monkeypatch, make_chain_file, make_sequence_file):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sequences").mkdir()
    seq_path = make_sequence_file("dep.json")
    chain = make_chain_file("f.json", sequences=[
        {"id": "s1", "sequence_file": "dep.json"}])
    cache = jc.JSONCache()
    assert cache._chain_depends_on_file(
        json.loads(open(chain, encoding="utf-8").read()), seq_path, str(tmp_path)) is True


def test_clear_cache_resets_stats(tmp_path, monkeypatch, make_chain_file):
    monkeypatch.chdir(tmp_path)
    chain = make_chain_file("g.json", sequences=[])
    cache = jc.JSONCache()
    cache.preload_chain(chain)
    cache.clear_cache()
    assert cache.get_cache_stats() == {"sequences_cached": 0, "chains_cached": 0, "paths_cached": 0}
