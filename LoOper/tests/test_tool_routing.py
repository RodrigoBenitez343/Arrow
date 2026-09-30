"""Unit tests for the embedding-first tool-routing changes (plan W1/W3):

- confidence-ladder candidate selection (small-model-safe tool lists)
- ToolRetriever scored retrieval, bounded passthrough, alias-free index text
- Input value propagation semantics: input_source="none" never walks the graph
"""

import sys
import types

import numpy as np
import pytest

from player.multi_sequence.llm_executor_resources.tool_retriever import (
    ToolRetriever,
    select_candidates_by_confidence,
)


# ---------------------------------------------------------------------------
# Confidence ladder (pure function)
# ---------------------------------------------------------------------------


def test_ladder_very_high_passes_two_candidates():
    ranked = [("a", 0.80), ("b", 0.30), ("c", 0.20)]
    ids, allow_natural = select_candidates_by_confidence(ranked, top_k=10)
    assert ids == ["a", "b"]
    assert allow_natural is False


def test_ladder_very_high_requires_margin():
    # Best 0.8 but runner-up 0.75: no clear margin -> falls to 'high' tier.
    ranked = [("a", 0.80), ("b", 0.75), ("c", 0.30)]
    ids, allow_natural = select_candidates_by_confidence(ranked, top_k=10)
    assert len(ids) == 3
    assert allow_natural is False


def test_ladder_high_gives_three_candidates():
    ranked = [("a", 0.60), ("b", 0.50), ("c", 0.40), ("d", 0.20), ("e", 0.10)]
    ids, allow_natural = select_candidates_by_confidence(ranked, top_k=10)
    assert ids == ["a", "b", "c"]
    assert allow_natural is False


def test_ladder_mid_gives_three_candidates():
    ranked = [(f"t{i}", 0.45 - i * 0.01) for i in range(6)]
    ids, allow_natural = select_candidates_by_confidence(ranked, top_k=10)
    assert len(ids) == 3
    assert allow_natural is False


def test_ladder_never_exceeds_three_candidates():
    # Even with many tools above threshold the Router LLM context stays <= 3.
    ranked = [(f"t{i}", 0.9 - i * 0.05) for i in range(8)]
    ids, allow_natural = select_candidates_by_confidence(ranked, top_k=10)
    assert len(ids) == 3
    assert allow_natural is False


def test_ladder_low_allows_natural_reply():
    ranked = [("a", 0.20), ("b", 0.15), ("c", 0.10)]
    ids, allow_natural = select_candidates_by_confidence(ranked, top_k=10)
    assert len(ids) == 3  # bounded by available tools
    assert allow_natural is True


def test_ladder_respects_top_k_cap_and_empty_input():
    ranked = [(f"t{i}", 0.9 - i * 0.1) for i in range(8)]
    ids, _ = select_candidates_by_confidence(ranked, top_k=2)
    assert len(ids) == 2
    ids, allow_natural = select_candidates_by_confidence([], top_k=10)
    assert ids == []
    assert allow_natural is True


# ---------------------------------------------------------------------------
# ToolRetriever: bounded passthrough + alias-free display-name index text
# ---------------------------------------------------------------------------


def _retriever_with_model(retriever, fake_model):
    retriever._model = fake_model
    return retriever


class _FakeEmbedModel:
    """Deterministic embedder keyed by exact description text."""

    _KNOWN = {
        "opens browser": [1.0, 0.0],
        "sends mail": [0.0, 1.0],
        "query-a": [1.0, 0.0],
    }

    def encode(self, texts, convert_to_tensor=False, **kwargs):
        texts = list(texts) if isinstance(texts, (list, tuple)) else [texts]
        rows = []
        for t in texts:
            rows.append(self._KNOWN.get(str(t), [0.0, 0.0]))
        arr = np.asarray(rows, dtype=np.float32)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return arr / norms


def test_retriever_scored_ranking_threshold_and_cap(monkeypatch):
    retriever = ToolRetriever()
    _retriever_with_model(retriever, _FakeEmbedModel())
    retriever.build_index(
        {"tid-a": "opens browser", "tid-b": "sends mail"},
        display_names={"tid-a": "a", "tid-b": "b"},
    )
    # Query closest to 'a'.
    ranked = retriever.retrieve_scored("query-a", top_k=10, score_threshold=-1.0)
    assert ranked[0][0] == "tid-a"
    assert ranked[0][1] > ranked[1][1]
    # Threshold filter.
    filtered = retriever.retrieve_scored("query-a", top_k=10, score_threshold=0.99)
    assert filtered == [("tid-a", pytest.approx(1.0, abs=1e-6))]
    # ids-only wrapper stays consistent.
    assert retriever.retrieve("query-a", top_k=10, score_threshold=0.0)[0] == "tid-a"


def test_retriever_index_text_is_description_only(monkeypatch):
    """The ranking corpus embeds each tool's DESCRIPTION, never its alias or
    raw hex id — the alias is only a fallback token when no description
    exists."""
    retriever = ToolRetriever()
    _retriever_with_model(retriever, _FakeEmbedModel())
    retriever.build_index(
        {"0xabc123": "opens the brave browser"},
        display_names={"0xabc123": "brave"},
    )
    # Alias/hex never prefix or replace the description text.
    assert retriever._tool_texts == ["opens the brave browser"]
    # Description-less tool falls back to the readable label (non-empty corpus).
    retriever.build_index(
        {"0xabc123": ""},
        display_names={"0xabc123": "brave"},
    )
    assert retriever._tool_texts == ["brave: [no description]"]


def test_retriever_unavailable_model_passthrough_capped_to_top_k(monkeypatch):
    retriever = ToolRetriever()
    monkeypatch.setattr(retriever, "_lazy_load_model", lambda: None)  # no model
    retriever.build_index({"a": "desc a", "b": "desc b", "c": "desc c"})
    ranked = retriever.retrieve_scored("anything", top_k=2, score_threshold=0.0)
    # Bounded passthrough in declaration order — never the full catalog.
    assert ranked == [("a", 0.0), ("b", 0.0)]
    # Above-zero threshold with no model -> nothing.
    assert retriever.retrieve_scored("anything", top_k=2, score_threshold=0.1) == []
    # ids-only wrapper: same bounds.
    assert retriever.retrieve("anything", top_k=2, score_threshold=0.0) == ["a", "b"]


# ---------------------------------------------------------------------------
# Input semantics: input_source="none" never walks the graph (plan W3)
# ---------------------------------------------------------------------------


def test_get_input_text_none_is_empty_without_graph_walk(monkeypatch):
    # Stub out the AI stack so the test does not touch Ollama/model imports.
    ai_pkg = types.ModuleType("AI")
    ai_pkg.__path__ = []
    ai_consult = types.ModuleType("AI.consult")
    ai_consult.OllamaClient = object
    monkeypatch.setitem(sys.modules, "AI", ai_pkg)
    monkeypatch.setitem(sys.modules, "AI.consult", ai_consult)

    from player.multi_sequence.llm_executor_resources.inputs import get_input_text

    # A conditional + Input passthrough upstream used to be back-filled by the
    # walk; input_source="none" must now return empty regardless of the graph.
    node = {
        "id": "llm1",
        "inputs": [{"from_node": "cond1", "input_port": "input"}],
    }
    variables = {"node_in1_output": "user text", "node_in1_is_input_passthrough": True}
    graph = {
        "cond1": {"type": "conditional", "inputs": [{"from_node": "in1"}]},
        "in1": {"type": "input"},
    }
    text, use_in_prompt = get_input_text(
        node, variables, "none", 0.6, lambda: False, False, workflow_graph=graph,
    )
    assert text is None
    assert use_in_prompt is False


def test_get_input_text_input_source_reads_connected_input_only(monkeypatch):
    ai_pkg = types.ModuleType("AI")
    ai_pkg.__path__ = []
    ai_consult = types.ModuleType("AI.consult")
    ai_consult.OllamaClient = object
    monkeypatch.setitem(sys.modules, "AI", ai_pkg)
    monkeypatch.setitem(sys.modules, "AI.consult", ai_consult)

    from player.multi_sequence.llm_executor_resources.inputs import get_input_text

    node = {
        "id": "llm1",
        "inputs": [{"from_node": "in1", "input_port": "data",
                    "output_type": "data"}],
    }
    variables = {"node_in1_data": "search milo j"}
    text, use_in_prompt = get_input_text(
        node, variables, "input", 0.6, lambda: False, False, workflow_graph=None,
    )
    assert text == "search milo j"


# ---------------------------------------------------------------------------
# Strict data-port semantics: Input values ride only explicit data edges
# ---------------------------------------------------------------------------


def test_upstream_value_resolves_by_source_output_port():
    from player.multi_sequence.llm_executor_resources.inputs import (
        _record_output_type,
        upstream_value,
    )

    vars_ = {
        "node_a_output": "exec-value",
        "node_b_data": "data-value",
        "node_c_context": "ctx-value",
        "node_e_output_summary": "named-value",
    }
    # Base/branch source ports drive execution ONLY — they carry no data.
    assert upstream_value(vars_, "a", "output") is None
    assert upstream_value(vars_, "a", "") is None
    assert upstream_value(vars_, "a", "true") is None
    assert upstream_value(vars_, "a", "false") is None
    # ... named ports carry data: data / ctx_out / context / <named>.
    assert upstream_value(vars_, "b", "data") == "data-value"
    assert upstream_value(vars_, "c", "ctx_out") == "ctx-value"
    assert upstream_value(vars_, "c", "context") == "ctx-value"
    assert upstream_value(vars_, "e", "summary") == "named-value"
    # An Input node publishes ONLY data: exec-only base edges see nothing.
    assert upstream_value(vars_, "b", "output") is None
    assert upstream_value({"node_d_data": "only"}, "d", "output") is None
    # Record output_type drives the resolution.
    rec = {"from_node": "b", "output_type": "data", "input_port": "input"}
    assert _record_output_type(rec) == "data"
    assert upstream_value(vars_, "b", _record_output_type(rec)) == "data-value"
    rec_exec = {"from_node": "b", "output_type": "output", "input_port": "input"}
    assert upstream_value(vars_, "b", _record_output_type(rec_exec)) is None

