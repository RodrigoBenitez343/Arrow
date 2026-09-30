"""Regressions for agent-mode routing / chain-import persistence.

Locks the four agent-mode failures seen in the 2026-09-19 session log:

1. A chain-import tool node whose chain file did not resolve on this machine
   silently lost its ``chain_file`` path on the next GUI save (observed:
   all three routing tools of BASE_SYSTEM_CHAIN.json saved as
   ``chain_file_path: ""`` -> "No chain file path specified" at runtime).
2. The Router LLM answered ``USE_TOOL:linkedin_job_search`` for the
   ``<linkedin>`` tool: the exact-match resolver rejected the near-miss token,
   the node finished with EMPTY output and the query was dropped.
3. Embedded chain-import configs lost ``output_nodes`` on save.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json  # noqa: E402

import pytest  # noqa: E402

from PyQt5.QtWidgets import QApplication  # noqa: E402

from player.multi_sequence.llm_executor_resources.executor import (
    _resolve_tool_token,
    _tool_selection_text,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


# ---------------------------------------------------------------------------
# 2. Tool-token resolution (executor)
# ---------------------------------------------------------------------------


def test_resolves_raw_node_id():
    assert _resolve_tool_token("0xabc", ["0xabc"], {}) == "0xabc"


def test_resolves_alias_token_and_bare_form():
    amap = {"<linkedin>": "0xabc"}
    assert _resolve_tool_token("<linkedin>", ["0xabc"], amap) == "0xabc"
    assert _resolve_tool_token("linkedin", ["0xabc"], amap) == "0xabc"


def test_resolves_near_miss_token_built_around_alias():
    """The observed failure: 'linkedin_job_search' for <linkedin>."""
    amap = {"<linkedin>": "0xabc", "<repairtest>": "0xdef"}
    assert _resolve_tool_token("linkedin_job_search", ["0xabc", "0xdef"], amap) == "0xabc"


def test_near_miss_requires_unambiguous_match():
    amap = {"<job>": "0xabc", "<jobs>": "0xdef"}
    # Both aliases appear as words -> ambiguous -> no guess.
    assert _resolve_tool_token("job_jobs_runner", ["0xabc", "0xdef"], amap) is None


def test_unknown_token_stays_unresolved():
    amap = {"<linkedin>": "0xabc"}
    assert _resolve_tool_token("send_email_tool", ["0xabc"], amap) is None


def test_alias_for_unconnected_tool_is_not_used():
    assert _resolve_tool_token("linkedin", ["0xabc"], {"<linkedin>": "0xzzz"}) is None


# ---------------------------------------------------------------------------
# Published output: readable tool text, never the raw routing key
# ---------------------------------------------------------------------------


def test_tool_selection_text_is_the_tool_description():
    assert _tool_selection_text("0xabc", {"0xabc": "does math"}, {}) == "does math"


def test_tool_selection_text_falls_back_to_id_without_description():
    # The alias is only a name and describes nothing — never published; the
    # id keeps the record until the chain declares a description.
    assert _tool_selection_text("0xabc", {}, {}) == "0xabc"


def test_tool_selection_text_appends_input_args():
    text = _tool_selection_text("0xabc", {"0xabc": "does math"}, {"a": 1})
    assert text == 'does math (input: {"a": 1})'


# ---------------------------------------------------------------------------
# 1. Chain-import node persistence (node property survives a save)
# ---------------------------------------------------------------------------


def _chain_import_node(qapp):
    from NodeGraphQt import NodeGraph
    from NGUI.nodes_resources.chain_import_node import ChainImportNode

    graph = NodeGraph()
    graph.register_node(ChainImportNode)
    return graph.create_node("chain_import.ChainImportNode", name="Chain Import", pos=[0, 0])


def _save_config(node):
    from NGUI.graph_elements.config_manager import ConfigManager

    cm = ConfigManager(type("P", (), {})())
    cm._get_node_connections = lambda n: []
    cm._save_chain_import_node(node)
    return cm.chain_config["chain_import_nodes"][-1]


def test_missing_chain_file_keeps_its_path_on_save(qapp, tmp_path):
    node = _chain_import_node(qapp)
    missing = str(tmp_path / "from_another_machine" / "linkedin.json")
    node.set_chain_import_data(missing, import_mode="full", prefix="linkedin",
                               loop_count=1, extra_delay=0.0, enabled=True)

    assert node.get_property("chain_file") == missing
    assert _save_config(node)["chain_file_path"] == missing
    assert "MISSING" in node.name()


def test_existing_chain_file_still_saves_path(qapp, tmp_path):
    chain = tmp_path / "tool.json"
    chain.write_text("{}", encoding="utf-8")
    node = _chain_import_node(qapp)
    node.set_chain_import_data(str(chain), import_mode="full", prefix="tool",
                               loop_count=1, extra_delay=0.0, enabled=True)

    assert _save_config(node)["chain_file_path"] == str(chain)


def test_dict_path_is_coerced_not_stored(qapp):
    """Callers occasionally pass a dict — never store it as the file path."""
    node = _chain_import_node(qapp)
    node.set_chain_import_data({"chain_file": "x.json"})
    assert node.get_property("chain_file") == ""
    assert node.get_property("import_mode") == "full"


def test_embedded_config_round_trips_output_nodes(qapp, tmp_path):
    chain = tmp_path / "tool.json"
    chain.write_text("{}", encoding="utf-8")
    node = _chain_import_node(qapp)
    node.set_chain_import_data(str(chain))
    node.chain_config = {
        "output_nodes": [{"type": "output", "node_id": "0xout"}],
        "sequences": [{"name": "s.json", "node_id": "0xseq"}],
    }
    saved = _save_config(node)
    assert saved["output_nodes"] == [{"type": "output", "node_id": "0xout"}]


# ---------------------------------------------------------------------------
# 3. A stopped run reports the stop, not the fallback chatter
# ---------------------------------------------------------------------------


def test_stopped_run_reports_stopped(tmp_path):
    from player.agentic_ops.chain_executor import ChainExecutor

    chain = tmp_path / "sys.json"
    chain.write_text(json.dumps({"sequences": [], "llm_nodes": []}), encoding="utf-8")

    result = ChainExecutor().run_chain(str(chain), stop_flag=lambda: True)
    assert "Stopped." in result
