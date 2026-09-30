"""LLM node ``orchestrator`` switch — the additive bridge to OrchestratorMixin.

Run:  python -m pytest LoOper/tests/test_llm_orchestrator_mode.py

The bridge only reshapes node data (no loop is executed here), so every case
is deterministic and model-free.  The switch is ON/OFF; ON always uses Laya.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from player.multi_sequence.worflow_interpreter_modules.executor_modules.orchestrator_ops import (  # noqa: E402
    OrchestratorMixin,
)


class _Harness(OrchestratorMixin):
    """The bridge methods need no executor state."""


def _llm_node(on=True, tools=(), **cfg):
    data = {"node_id": "L1", "orchestrator_mode": bool(on)}
    data.update(cfg)
    inputs = [
        {"from_node": t, "input_port": "tools", "output_type": "output"}
        for t in tools
    ]
    return {
        "type": "llm",
        "id": "L1",
        "data": data,
        "inputs": inputs,
        "connections": {"output": []},
    }


def test_switch_detection():
    h = _Harness()
    assert h._llm_node_is_orchestrator(_llm_node(on=True))
    assert not h._llm_node_is_orchestrator(_llm_node(on=False))
    assert not h._llm_node_is_orchestrator({"data": {}})
    # Legacy values are still honoured.
    assert h._llm_node_is_orchestrator({"data": {"mode": "orchestrator"}})
    assert h._llm_node_is_orchestrator(
        {"data": {"llm_configuration": {"orchestrator_mode": True}}}
    )


def test_orchestrator_like():
    h = _Harness()
    assert h._is_orchestrator_like({"type": "orchestrator"})
    assert h._is_orchestrator_like(_llm_node(on=True))
    assert not h._is_orchestrator_like(_llm_node(on=False))


def test_tools_are_retagged_as_chains():
    h = _Harness()
    orch = h._orchestrator_from_llm_node(_llm_node(tools=["c1", "c2"]))
    assert orch["type"] == "orchestrator"
    assert orch["id"] == "L1"
    assert [i["input_port"] for i in orch["inputs"]] == ["chains", "chains"]
    assert {i["from_node"] for i in orch["inputs"]} == {"c1", "c2"}


def test_laya_is_fixed_and_steps_map():
    h = _Harness()
    orch = h._orchestrator_from_llm_node(_llm_node(tools=["c1"], orch_max_steps=7))
    assert orch["data"]["max_steps"] == 7
    assert orch["data"]["picker"] == "laya"  # switch ON => Laya, no choice
