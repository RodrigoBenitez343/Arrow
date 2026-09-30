"""Unit tests for the tree-integrity layer: every JSON artifact in the
LoOper data tree (chains, sequences, tutorial chains, schedules) must be
well-formed and internally consistent. These act as invariant guards for
the file tree the user modularized.
"""

import json
import os

import pytest

_LOOPER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHAINS_DIR = os.path.join(_LOOPER_DIR, "chains")
SEQUENCES_DIR = os.path.join(_LOOPER_DIR, "sequences")
DATA_DIR = os.path.join(_LOOPER_DIR, "data")


def _iter_json_files(directory):
    if not os.path.isdir(directory):
        return
    for root, _dirs, files in os.walk(directory):
        for fname in sorted(files):
            if fname.endswith(".json"):
                yield os.path.join(root, fname)


@pytest.mark.parametrize("path", list(_iter_json_files(CHAINS_DIR)),
                         ids=lambda p: os.path.relpath(p, _LOOPER_DIR))
def test_all_chain_files_are_valid_json(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert isinstance(data, (dict, list))


@pytest.mark.parametrize("path", list(_iter_json_files(CHAINS_DIR)),
                         ids=lambda p: os.path.relpath(p, _LOOPER_DIR))
def test_chain_files_have_valid_structure(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        for key in ("sequences", "llm_nodes", "conditional_nodes",
                    "chain_import_nodes", "context_nodes", "input_nodes"):
            if key in data:
                assert isinstance(data[key], list), f"{key} must be a list in {path}"


@pytest.mark.parametrize("path", list(_iter_json_files(SEQUENCES_DIR)),
                         ids=lambda p: os.path.relpath(p, _LOOPER_DIR))
def test_all_sequence_files_valid_and_well_shaped(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert isinstance(data, dict)
    assert isinstance(data.get("actions"), list), f"{path} is not sequence-shaped"
    for action in data["actions"]:
        assert isinstance(action, dict), f"action must be a dict in {path}"
        assert "type" in action, f"action missing 'type' in {path}"


@pytest.mark.parametrize("path", list(_iter_json_files(CHAINS_DIR)),
                         ids=lambda p: os.path.relpath(p, _LOOPER_DIR))
def test_chain_files_reference_existing_sequences(path):
    """Every sequence referenced by a NON-tutorial chain must resolve to a
    real, sequence-shaped file. Tutorial chains are educational examples and
    may reference files the user must create, so they are exempt here."""
    if "tutorial" in os.path.normpath(path).split(os.sep):
        pytest.skip("tutorial chains may reference user-created sequences")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    for seq in data.get("sequences", []):
        ref = (seq.get("sequence_file") or "").strip()
        if not ref:
            continue
        candidates = [
            os.path.join(SEQUENCES_DIR, os.path.basename(ref)),
            os.path.join(CHAINS_DIR, ref),
        ]
        assert any(os.path.exists(c) for c in candidates), \
            f"{path} references missing sequence {ref}"


def test_chain_node_ids_are_unique_within_file():
    for path in _iter_json_files(CHAINS_DIR):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            continue
        seen = {}
        for key in ("sequences", "llm_nodes", "conditional_nodes",
                    "chain_import_nodes", "context_nodes", "input_nodes",
                    "code_nodes", "tts_nodes", "handle_nodes", "mcp_nodes",
                    "output_nodes", "container_nodes"):
            for node in data.get(key, []):
                nid = node.get("id")
                if nid is None:
                    nid = node.get("node_id")
                if nid is None:
                    continue
                assert nid not in seen, f"duplicate node id {nid!r} in {path}"
                seen[nid] = key


def test_schedules_file_is_valid():
    path = os.path.join(_LOOPER_DIR, "schedules.json")
    if not os.path.exists(path):
        pytest.skip("schedules.json not present")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert isinstance(data, (dict, list))


def test_data_cache_files_are_valid_json():
    for path in _iter_json_files(DATA_DIR):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data is not None


def test_all_python_modules_importable():
    """Every top-level module in the LoOper tree must import without error
    (catches broken refactors early). GUI-free modules only."""
    import importlib
    import sys

    sys.path.insert(0, _LOOPER_DIR)
    modules = [
        "AI.api", "AI.comorag_engine", "AI.config_loader", "AI.consult",
        "AI.context_collector", "AI.context_database", "AI.gguf_model_info",
        "AI.inprocess_transport", "AI.llama_cpp_engine", "AI.model_cache",
        "AI.model_downloads", "AI.ollama_engine", "AI.recursive_vision_reasoner",
        "AI.runtime_paths", "AI.settings",
        "NGUI.branching_utils", "NGUI.constants", "NGUI.i18n",
        "NGUI.scheduler_service",
        "builder.agent_exporter", "builder.agent_standalone_entry",
        "player.base_bot", "player.image_utils", "player.json_cache",
        "player.key_defs", "player.keyboard_monitor", "player.screenshot_cleanup",
        "player.sequence_player",
        "player.agentic_ops.chain_executor", "player.agentic_ops.simple_agent",
        "player.multi_sequence.conditional_fallback_handler",
        "player.multi_sequence.conditional_fallback_resources.path_resolver",
        "player.multi_sequence.conditional_fallback_resources.validation_cache",
        "player.multi_sequence.llm_executor_resources.prompt_utils",
        "player.multi_sequence.llm_executor_resources.rag_utils",
        "player.multi_sequence.llm_executor_resources.tool_retriever",
        "player.multi_sequence.llm_executor_resources.config_utils",
        "player.multi_sequence.llm_executor_resources.vision_utils",
        "player.multi_sequence.worflow_interpreter_modules.builder",
        "player.multi_sequence.worflow_interpreter_modules.navigator",
        "player.multi_sequence.worflow_interpreter_modules.executor_modules.port_store",
        "recorder.keyboard_handler", "recorder.mouse_handler",
        "recorder.scroll_manager", "recorder.sequence_manager",
        "sandbox_agent",
    ]
    for mod in modules:
        importlib.import_module(mod)
