"""Unit tests for llm_executor_resources helpers:
- prompt_utils (variable substitution, prompt assembly)
- executor._strip_think_tags (chain-of-thought stripping)
- executor._find_bundled_embedding_dir
- tool_retriever (index/retrieve passthrough)
- vision_utils (prepare_vision_images)
"""

import json
import os
import sys

import pytest

from player.multi_sequence.llm_executor_resources import prompt_utils
from player.multi_sequence.llm_executor_resources import executor as llm_exec
from player.multi_sequence.llm_executor_resources import tool_retriever as tr
from player.multi_sequence.llm_executor_resources import vision_utils


# ---------------------------------------------------------------------------
# prompt_utils
# ---------------------------------------------------------------------------


def test_substitute_variables_basic():
    out = prompt_utils.substitute_variables(
        "Hello {name}, you have {count} items",
        {"name": "Alice", "count": 3},
    )
    assert out == "Hello Alice, you have 3 items"


def test_substitute_variables_skips_internal():
    out = prompt_utils.substitute_variables("A {_secret} B {x}", {"_secret": "LEAK", "x": "ok"})
    assert out == "A {_secret} B ok"


def test_substitute_variables_truncates_long_values():
    big = "x" * (prompt_utils._MAX_VAR_LENGTH + 500)
    out = prompt_utils.substitute_variables("{big}", {"big": big})
    assert "[TRUNCATED" in out
    assert len(out) < len(big)


def test_substitute_variables_no_variables():
    assert prompt_utils.substitute_variables("plain text", None) == "plain text"
    assert prompt_utils.substitute_variables("", {"a": 1}) == ""


def test_assemble_enhanced_prompt_with_input():
    out = prompt_utils.assemble_enhanced_prompt("What is this?", "clipboard", "some text")
    assert out.startswith("some text")
    assert prompt_utils._HISTORY_INSERT_MARKER in out
    assert out.endswith("What is this?")


def test_assemble_enhanced_prompt_without_input():
    out = prompt_utils.assemble_enhanced_prompt("Just ask", "none", "")
    assert out == f"{prompt_utils._HISTORY_INSERT_MARKER}\nJust ask"


def test_resolve_rag_embedding_model_llamacpp_normalizes_ollama_name():
    # An Ollama-era model name on a llama.cpp node must resolve to the local
    # GGUF so consolidation actually runs (raw-text fallback prevented).
    cfg = {"rag_embedding_model": "nomic-embed-text"}
    assert (
        llm_exec._resolve_rag_embedding_model(cfg, use_llamacpp=True)
        == "embeddinggemma-300M-Q8_0.gguf"
    )


def test_resolve_rag_embedding_model_llamacpp_keeps_gguf():
    cfg = {"rag_embedding_model": "embeddinggemma-300M-Q8_0.gguf"}
    assert (
        llm_exec._resolve_rag_embedding_model(cfg, use_llamacpp=True)
        == "embeddinggemma-300M-Q8_0.gguf"
    )


def test_resolve_rag_embedding_model_ollama_keeps_name():
    cfg = {"rag_embedding_model": "nomic-embed-text"}
    assert (
        llm_exec._resolve_rag_embedding_model(cfg, use_llamacpp=False)
        == "nomic-embed-text"
    )


def test_trim_to_context_budget_trims_newest_tail():
    cfg = {"llamacpp_context_size": 256}
    text = "A" * 4000
    out = llm_exec._trim_to_context_budget(text, cfg, reserve_chars=100)
    assert len(out) < len(text)
    # Newest tail is kept, oldest head dropped.
    assert out.endswith(text[-200:])


def test_trim_to_context_budget_noop_without_context_size():
    # Native/unknown context (0) must pass the text through untouched.
    text = "A" * 100000
    assert llm_exec._trim_to_context_budget(text, {"llamacpp_context_size": 0}) == text
    assert llm_exec._trim_to_context_budget(text, {}) == text


def test_trim_to_context_budget_fits_unchanged():
    text = "short"
    cfg = {"llamacpp_context_size": 4096}
    assert llm_exec._trim_to_context_budget(text, cfg) == text


# ---------------------------------------------------------------------------
# _strip_think_tags
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw,expected", [
    ("<think>reasoning</think>Answer", "Answer"),
    ("answer", "answer"),
    ("<answer>final answer</answer>", "final answer"),
    ("[answer]final answer[/answer]", "final answer"),
    ("reasoning text\n</think>\nNo.", "No."),
    ("```think\ncode block\n```\nResult", "Result"),
    ("<reasoning>deep</reasoning>Out", "Out"),
    ("<scratchpad>deep</scratchpad>Out", "Out"),
    ("[thinking]inner[/thinking]Out", "Out"),
    ("## Thinking\nOut", "Out"),
    (None, None),
    (123, 123),
])
def test_strip_think_tags(raw, expected):
    assert llm_exec._strip_think_tags(raw) == expected


# ---------------------------------------------------------------------------
# _find_bundled_embedding_dir
# ---------------------------------------------------------------------------


def test_find_bundled_embedding_dir_source_tree(monkeypatch, tmp_path):
    monkeypatch.setattr(llm_exec.sys, "frozen", False, raising=False)
    monkeypatch.delattr(llm_exec.sys, "_MEIPASS", raising=False)
    models_dir = tmp_path / "models"
    model_dir = models_dir / "all-MiniLM-L6-v2"
    model_dir.mkdir(parents=True)
    (model_dir / "config.json").write_text("{}", encoding="utf-8")

    import AI.config_loader as cl
    monkeypatch.setattr(cl, "get_models_dir", lambda: str(models_dir))
    found = llm_exec._find_bundled_embedding_dir("all-MiniLM-L6-v2")
    assert found == str(model_dir)


def test_find_bundled_embedding_dir_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(llm_exec.sys, "frozen", False, raising=False)
    monkeypatch.delattr(llm_exec.sys, "_MEIPASS", raising=False)
    models_dir = tmp_path / "models"
    models_dir.mkdir()

    import AI.config_loader as cl
    monkeypatch.setattr(cl, "get_models_dir", lambda: str(models_dir))
    assert llm_exec._find_bundled_embedding_dir("ghost-model") is None


def test_find_bundled_embedding_dir_flattened_fallback(monkeypatch, tmp_path):
    """Older builds flatten embedding files directly in the models dir."""
    monkeypatch.setattr(llm_exec.sys, "frozen", False, raising=False)
    monkeypatch.delattr(llm_exec.sys, "_MEIPASS", raising=False)
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    (models_dir / "model.safetensors").write_bytes(b"x")
    (models_dir / "modules.json").write_text("[]", encoding="utf-8")
    (models_dir / "config.json").write_text("{}", encoding="utf-8")
    (models_dir / "tokenizer.json").write_text("{}", encoding="utf-8")

    import AI.config_loader as cl
    import AI.runtime_paths as rp
    monkeypatch.setattr(cl, "get_models_dir", lambda: str(models_dir))
    monkeypatch.setattr(rp, "get_runtime_dir", lambda: str(tmp_path / "runtime"))

    found = llm_exec._find_bundled_embedding_dir("all-MiniLM-L6-v2")
    assert found is not None
    assert os.path.isfile(os.path.join(found, "config.json"))
    assert "runtime" in found


# ---------------------------------------------------------------------------
# ToolRetriever
# ---------------------------------------------------------------------------


def test_tool_retriever_index_without_model_is_passthrough(monkeypatch):
    retriever = tr.ToolRetriever()
    monkeypatch.setattr(retriever, "_lazy_load_model", lambda: None)  # model stays None
    built = retriever.build_index({"tool_a": "does a", "tool_b": "does b"})
    assert built is True
    assert retriever.retrieve("anything", score_threshold=0.0) == ["tool_a", "tool_b"]
    assert retriever.retrieve("anything", score_threshold=0.5) == []


def test_tool_retriever_cache_skip_rebuild():
    retriever = tr.ToolRetriever()

    class FakeModel:
        def encode(self, texts, **kwargs):
            return object()

    retriever._model = FakeModel()

    # First build returns True, second with identical map returns False
    assert retriever.build_index({"a": "desc"}) is True
    assert retriever.build_index({"a": "desc"}) is False


def test_tool_retriever_empty_map_clears():
    retriever = tr.ToolRetriever()
    retriever._model = object()
    retriever._tool_ids = ["a"]
    retriever._tool_embeddings = object()
    assert retriever.build_index({}) is True
    assert retriever._tool_ids == []


def test_tool_retriever_retrieve_empty_index():
    retriever = tr.ToolRetriever()
    assert retriever.retrieve("q") == []


def test_tool_retriever_clear():
    retriever = tr.ToolRetriever()
    retriever._tool_ids = ["a"]
    retriever.clear()
    assert retriever._tool_ids == []
    assert retriever._model is None


def test_tool_retriever_singleton_exists():
    assert isinstance(tr._RETRIEVER, tr.ToolRetriever)


# ---------------------------------------------------------------------------
# vision_utils
# ---------------------------------------------------------------------------


def test_prepare_vision_images_disabled():
    assert vision_utils.prepare_vision_images(False, True, None) == (None, None)


def test_prepare_vision_images_screenshots_disabled():
    assert vision_utils.prepare_vision_images(True, False, None) == (None, None)


def test_prepare_vision_images_capture_failure(monkeypatch):
    monkeypatch.setattr(vision_utils, "capture_screenshot", lambda stop_flag: None)
    assert vision_utils.prepare_vision_images(True, True, None) == (None, None)


def test_prepare_vision_images_success(monkeypatch, tmp_path):
    img = tmp_path / "shot.png"
    img.write_bytes(b"PNG")
    monkeypatch.setattr(vision_utils, "capture_screenshot", lambda stop_flag: str(img))
    images, _ = vision_utils.prepare_vision_images(True, True, None)
    assert images == [str(img)]


def test_capture_screenshot_stop_flag(monkeypatch):
    monkeypatch.setattr(vision_utils, "capture_screenshot",
                        lambda stop_flag: None if stop_flag() else "x")
    assert vision_utils.prepare_vision_images(True, True, lambda: True) == (None, None)


# ---------------------------------------------------------------------------
# Consolidation relevance verdict + tool re-selection
# ---------------------------------------------------------------------------


def test_parse_relevance_lines_reads_numbered_verdicts():
    assert llm_exec._parse_relevance_lines("1: YES\n2: NO\n3: yes\n", 3) == [
        True, False, True]


def test_parse_relevance_lines_accepts_the_shapes_models_emit():
    assert llm_exec._parse_relevance_lines("- 1. no\n* 2) YES\n", 2) == [
        False, True]
    assert llm_exec._parse_relevance_lines(
        "Claim: ...\n1: NO not the same field\n2: TRUE", 2) == [False, True]


def test_parse_relevance_lines_fails_open_on_a_partial_answer():
    # A truncated reply must NEVER be read as "every claim is irrelevant".
    assert llm_exec._parse_relevance_lines("1: YES\n", 2) is None
    assert llm_exec._parse_relevance_lines("", 2) is None
    assert llm_exec._parse_relevance_lines(None, 1) is None
    assert llm_exec._parse_relevance_lines("honestly I cannot tell", 2) is None


def test_reselect_tool_with_model_resolves_against_the_candidate_list(monkeypatch):
    """The re-selection call hands the model the ACTUAL tool list, so its
    answer resolves through the same resolver the router output uses."""
    seen = []
    monkeypatch.setattr(
        llm_exec, "_llamacpp_raw_completion",
        lambda prompt, *a, **k: seen.append(prompt) or "USE_TOOL:<brave>\n",
    )
    hit = llm_exec._reselect_tool_with_model(
        "search the web for the news", ["n1", "n2"],
        {"n1": "open a browser", "n2": "search the web"},
        {"brave": "n2"}, "USE_TOOL:tavily", {"use_llamacpp": True},
        "http://x", True,
    )
    assert hit == "n2"
    # The prompt carries the names the router was offered, not raw node ids.
    assert seen and "- brave: search the web" in seen[0]


def test_reselect_tool_with_model_invents_nothing(monkeypatch):
    monkeypatch.setattr(
        llm_exec, "_llamacpp_raw_completion",
        lambda *a, **k: "USE_TOOL:ghost_tool",
    )
    # A name outside the list is not a tool id.
    assert llm_exec._reselect_tool_with_model(
        "r", ["n1"], {"n1": "d"}, {}, "x", {}, "u", True) is None
    # NONE means the model found no fit — the caller keeps its heuristics.
    monkeypatch.setattr(
        llm_exec, "_llamacpp_raw_completion", lambda *a, **k: "NONE",
    )
    assert llm_exec._reselect_tool_with_model(
        "r", ["n1"], {"n1": "d"}, {}, "x", {}, "u", True) is None


def test_reselect_tool_with_model_skipped_off_the_llamacpp_path(monkeypatch):
    calls = []
    monkeypatch.setattr(
        llm_exec, "_llamacpp_raw_completion",
        lambda *a, **k: calls.append(1) or "USE_TOOL:n1",
    )
    assert llm_exec._reselect_tool_with_model(
        "r", ["n1"], {"n1": "d"}, {}, "x", {}, "u", False) is None
    assert calls == []                  # an Ollama node never pays this call
