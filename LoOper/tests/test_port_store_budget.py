"""Turn history: the char budget that bounds what the model reads.

A turn COUNT says nothing about cost (one tool result can be 4000 chars, the
next 40), so the store now also trims by size.  The rules pinned here are the
deterministic ones: the node's first turn (its instructions) survives, the
newest turns stay verbatim, the middle becomes one marker, and another node's
turns are never touched.

Run:  python -m pytest LoOper/tests/test_port_store_budget.py
"""

import pytest

from player.multi_sequence.worflow_interpreter_modules.executor_modules.port_store import (
    PortScopedStore,
)


@pytest.fixture
def store():
    return PortScopedStore()


def _turns(store, node_id="ctx"):
    return [t for t in store.get_turns("c", node_id=node_id, limit=999)
            if t.get("context_node_id") == node_id]


def test_under_budget_keeps_everything(store):
    for i in range(3):
        store.add_turn("c", "ctx", {"content": "x" * 10}, max_chars=1000)
    assert len(_turns(store)) == 3
    assert not any(t.get("role") == "note" for t in _turns(store))


def test_over_budget_keeps_head_and_tail_and_inserts_one_marker(store):
    store.add_turn("c", "ctx", {"content": "INSTRUCTIONS"}, max_chars=200)
    for i in range(8):
        store.add_turn("c", "ctx", {"content": "body-%d-" % i + "y" * 60},
                       max_chars=200)

    turns = _turns(store)
    assert turns[0]["content"] == "INSTRUCTIONS", "the first turn survives"
    markers = [t for t in turns if t.get("role") == "note"]
    assert len(markers) == 1, "the dropped middle collapses to ONE marker"
    assert markers[0]["content"].startswith("[older turns dropped")
    assert turns[-1]["content"].startswith("body-7"), "newest stays verbatim"
    assert len(turns) < 9, "the middle was actually dropped"


def test_repeated_trims_do_not_stack_markers(store):
    store.add_turn("c", "ctx", {"content": "INSTRUCTIONS"}, max_chars=120)
    for i in range(12):
        store.add_turn("c", "ctx", {"content": "b%d" % i + "z" * 60},
                       max_chars=120)
    markers = [t for t in _turns(store) if t.get("role") == "note"]
    assert len(markers) == 1


def test_other_nodes_turns_are_untouched(store):
    store.add_turn("c", "other", {"content": "keep-" + "q" * 80}, max_chars=100)
    for i in range(6):
        store.add_turn("c", "ctx", {"content": "b%d" % i + "z" * 80},
                       max_chars=100)
    other = _turns(store, node_id="other")
    assert [t["content"] for t in other] == ["keep-" + "q" * 80]


def test_max_turns_still_applies(store):
    for i in range(6):
        store.add_turn("c", "ctx", {"i": i}, max_turns=3)
    assert len(_turns(store)) == 3


def test_env_budget_applies_when_the_caller_passes_nothing(store, monkeypatch):
    monkeypatch.setenv("LOOPER_CONTEXT_MAX_CHARS", "150")
    store.add_turn("c", "ctx", {"content": "HEAD"})
    for i in range(6):
        store.add_turn("c", "ctx", {"content": "b%d" % i + "z" * 70})
    assert any(t.get("role") == "note" for t in _turns(store))


def test_budget_off_by_default(store, monkeypatch):
    monkeypatch.delenv("LOOPER_CONTEXT_MAX_CHARS", raising=False)
    for i in range(10):
        store.add_turn("c", "ctx", {"content": "z" * 500})
    assert len(_turns(store)) == 10
