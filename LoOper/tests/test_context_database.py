"""Unit tests for AI/context_database.py — SQLite-backed context store.
"""

import json
import time

import pytest

from AI.context_database import ContextDatabase


@pytest.fixture
def db(tmp_path):
    database = ContextDatabase(str(tmp_path / "test_context.db"))
    yield database
    database.close()


# ---------------------------------------------------------------------------
# normalize_chain_id
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("chain_id,expected", [
    ("BASE_SYSTEM_CHAIN", "BASE_SYSTEM_CHAIN"),
    ("BASE_SYSTEM_CHAIN.json", "BASE_SYSTEM_CHAIN"),
    (r"D:\chains\BASE_SYSTEM_CHAIN.json", "BASE_SYSTEM_CHAIN"),
    ("sub/dir/chain.json", "chain"),
    ("", ""),
])
def test_normalize_chain_id(chain_id, expected):
    assert ContextDatabase.normalize_chain_id(chain_id) == expected


# ---------------------------------------------------------------------------
# push / pull
# ---------------------------------------------------------------------------


def test_push_pull_roundtrip(db):
    row_id = db.push("chains/MyChain.json", "ctx1", "query", "hello world")
    assert row_id > 0
    result = db.pull("MyChain", "ctx1")
    assert result["query"] == ["hello world"]


def test_push_stores_json_values(db):
    db.push("c", "n", "k", {"nested": [1, 2, 3]})
    db.push("c", "n", "k", 42)
    result = db.pull("c", "n", key="k")
    # pull() returns newest first
    assert result["k"] == [42, {"nested": [1, 2, 3]}]


def test_pull_key_filter(db):
    db.push("c", "n", "query", "q1")
    db.push("c", "n", "result", "r1")
    result = db.pull("c", "n", key="query")
    assert result == {"query": ["q1"]}


def test_pull_returns_newest_first_with_limit(db):
    for i in range(10):
        db.push("c", "n", "k", str(i))
        time.sleep(0.002)  # distinct created_at timestamps
    result = db.pull("c", "n", limit=3)
    # String values that look like numbers round-trip through json.loads as ints
    assert result["k"] == [9, 8, 7]


def test_pull_chain_scope_is_isolated(db):
    db.push("chain_a", "n", "k", "a-value")
    db.push("chain_b", "n", "k", "b-value")
    assert db.pull("chain_a", "n")["k"] == ["a-value"]
    assert db.pull("chain_b", "n")["k"] == ["b-value"]


def test_push_rolling_row_cap(tmp_path):
    capped = ContextDatabase(str(tmp_path / "capped.db"), row_cap=3)
    try:
        for i in range(10):
            capped.push("c", "n", "k", str(i))
            time.sleep(0.002)  # distinct created_at timestamps
        result = capped.pull("c", "n", limit=10)
        assert result["k"] == [9, 8, 7]  # newest 3 kept, oldest dropped
        # other (chain, node) untouched
        capped.push("other", "n", "k", "v")
        assert capped.pull("other", "n")["k"] == ["v"]
    finally:
        capped.close()


def test_push_row_cap_zero_disables_trim(tmp_path):
    uncapped = ContextDatabase(str(tmp_path / "uncapped.db"), row_cap=0)
    try:
        for i in range(5):
            uncapped.push("c", "n", "k", str(i))
        assert len(uncapped.pull("c", "n", limit=10)["k"]) == 5
    finally:
        uncapped.close()


def test_pull_missing_returns_empty(db):
    assert db.pull("ghost", "n") == {}


# ---------------------------------------------------------------------------
# delete / clear
# ---------------------------------------------------------------------------


def test_delete_by_key(db):
    db.push("c", "n", "k1", "a")
    db.push("c", "n", "k2", "b")
    db.delete("c", "n", key="k1")
    result = db.pull("c", "n")
    assert "k1" not in result
    assert result["k2"] == ["b"]


def test_delete_by_source_node(db):
    db.push("c", "n", "k", "v", source_node_id="llm1")
    db.push("c", "n", "k", "v2", source_node_id="llm2")
    db.delete("c", "n", source_node_id="llm1")
    result = db.pull("c", "n", key="k")
    assert result["k"] == ["v2"]


def test_delete_by_age(db):
    db.push("c", "n", "k", "old")
    time.sleep(0.01)
    db.push("c", "n", "k", "new")
    db.delete("c", "n", max_age_seconds=0.005)
    result = db.pull("c", "n", key="k")
    assert result["k"] == ["new"]


def test_clear_chain(db):
    db.push("c1", "n", "k", "v1")
    db.push("c1", "n2", "k", "v2")
    db.push("c2", "n", "k", "v3")
    assert db.clear("c1") == 2
    assert db.pull("c1", "n") == {}
    assert db.pull("c2", "n")["k"] == ["v3"]


def test_clear_node(db):
    db.push("c", "n1", "k", "v1")
    db.push("c", "n2", "k", "v2")
    assert db.clear("c", "n1") == 1
    assert db.pull("c", "n1") == {}
    assert db.pull("c", "n2")["k"] == ["v2"]


def test_clear_empty_returns_zero(db):
    assert db.clear("ghost") == 0


# ---------------------------------------------------------------------------
# export_context (GraphRAG alias)
# ---------------------------------------------------------------------------


def test_export_context_shape(db):
    db.push("c", "n", "query", "q")
    exported = db.export_context("c", "n")
    assert exported["node_id"] == "n"
    assert exported["keys"]["query"] == ["q"]


def test_context_database_isolation_between_instances(tmp_path):
    db1 = ContextDatabase(str(tmp_path / "a.db"))
    db2 = ContextDatabase(str(tmp_path / "b.db"))
    try:
        db1.push("c", "n", "k", "only-in-a")
        assert db2.pull("c", "n") == {}
    finally:
        db1.close()
        db2.close()


def test_source_metadata_stored(db):
    db.push("c", "n", "k", "v", source_node_id="src", source_type="llm")
    conn = db._get_conn()
    row = conn.execute(
        "SELECT source_node_id, source_type FROM context_entries"
    ).fetchone()
    assert row == ("src", "llm")
