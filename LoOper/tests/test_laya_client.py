"""laya_client: kill switch, model/exe resolution, verdict plumbing, and the
load -> use -> unload burst policy.

The daemon process itself is exercised live by the app and the bundle smoke
test; these tests pin the failure paths (None-on-unavailable so consumers fall
back to their LLM evaluators), the request plumbing, and the burst policy
without spawning the engine.

Run:  python -m pytest LoOper/tests/test_laya_client.py
"""

import io
import json

import AI.laya_client as lc


class _FakeProc:
    """Minimal Popen stand-in: alive until terminated."""

    def __init__(self):
        self.alive = True
        self.terminated = False
        self.stdin = io.StringIO()

    def poll(self):
        return None if self.alive else 0

    def terminate(self):
        self.terminated = True
        self.alive = False


def _fresh(monkeypatch):
    monkeypatch.setattr(lc, "_SESSION_DISABLED", False)
    monkeypatch.setattr(lc, "_READY", False)
    monkeypatch.setattr(lc, "_PROC", None)
    monkeypatch.setattr(lc, "_INFLIGHT", 0)
    monkeypatch.setattr(lc, "_KEEP_ALIVE_SECONDS", 0.0)
    monkeypatch.setattr(lc, "_disabled", lambda: False)
    monkeypatch.delenv("LOOPER_LAYA", raising=False)
    monkeypatch.delenv("LOOPER_JEV", raising=False)
    monkeypatch.delenv("LOOPER_LAYA_MODEL", raising=False)


def test_kill_switch(monkeypatch):
    monkeypatch.setenv("LOOPER_LAYA", "off")
    assert lc._disabled() is True
    assert lc.status() == "off"
    monkeypatch.delenv("LOOPER_LAYA")
    monkeypatch.setenv("LOOPER_JEV", "0")  # legacy name of the same switch
    assert lc._disabled() is True
    monkeypatch.delenv("LOOPER_JEV")
    assert lc._disabled() is False


def test_status_down_when_nothing_running(monkeypatch):
    _fresh(monkeypatch)
    assert lc.status() == "down"
    assert lc.available() is False


def test_find_model_env_then_default_then_fallback(tmp_path, monkeypatch):
    _fresh(monkeypatch)
    d = tmp_path / "laya-GGUF"
    d.mkdir()
    (d / "laya_english_q8_0.gguf").write_bytes(b"x")
    monkeypatch.setattr(lc, "_models_dir", lambda: str(d))
    # Default quant missing -> first shipped fallback wins.
    assert lc._find_model().endswith("laya_english_q8_0.gguf")

    (d / "laya_english_ud_q4_k_m.gguf").write_bytes(b"x")
    assert lc._find_model().endswith("laya_english_ud_q4_k_m.gguf")  # default wins

    monkeypatch.setenv("LOOPER_LAYA_MODEL", "laya_english_q8_0.gguf")
    assert lc._find_model().endswith("laya_english_q8_0.gguf")  # env wins


def test_find_model_empty_dir(tmp_path, monkeypatch):
    _fresh(monkeypatch)
    monkeypatch.setattr(lc, "_models_dir", lambda: str(tmp_path / "nope"))
    assert lc._find_model() == ""


def test_ensure_running_disables_session_without_exe(monkeypatch):
    _fresh(monkeypatch)
    monkeypatch.setattr(lc, "_find_exe", lambda: "")
    assert lc.ensure_running() is False
    assert lc.status() == "off"  # disabled for the session (logged once)


def test_decision_and_rerank_plumbing(monkeypatch):
    _fresh(monkeypatch)
    monkeypatch.setattr(lc, "available", lambda: True)
    monkeypatch.setattr(lc, "ensure_running", lambda: True)

    monkeypatch.setattr(lc, "noul", lambda state, instructions: 0.83)
    assert lc.decision("premise", "criterion") is True
    monkeypatch.setattr(lc, "noul", lambda state, instructions: 0.2)
    assert lc.decision("premise", "criterion") is False
    monkeypatch.setattr(lc, "noul", lambda state, instructions: None)
    assert lc.decision("premise", "criterion") is None  # -> LLM fallback

    seen = {}

    def _choice(state, instructions, criteria):
        seen["state"] = state
        seen["criteria"] = list(criteria)
        return "second option"

    monkeypatch.setattr(lc, "choice", _choice)
    assert lc.rerank("premise", ["first option", "second option"]) == 1
    assert seen["state"] == "premise"
    assert seen["criteria"] == ["first option", "second option"]

    monkeypatch.setattr(lc, "choice", lambda state, instructions, criteria: None)
    assert lc.rerank("premise", ["a", "b"]) is None
    assert lc.rerank("premise", []) is None


# ------------------------------------------------- load -> use -> unload


def test_idle_unload_stops_the_daemon(monkeypatch):
    _fresh(monkeypatch)
    proc = _FakeProc()
    monkeypatch.setattr(lc, "_PROC", proc)
    monkeypatch.setattr(lc, "_READY", True)

    lc._unload_after_idle(seq_at_schedule=lc._SEQ)

    assert proc.terminated is True
    assert lc._PROC is None
    assert lc._READY is False
    assert lc.status() == "down"


def test_idle_unload_bails_when_a_newer_request_landed(monkeypatch):
    _fresh(monkeypatch)
    proc = _FakeProc()
    monkeypatch.setattr(lc, "_PROC", proc)
    monkeypatch.setattr(lc, "_READY", True)

    lc._unload_after_idle(seq_at_schedule=lc._SEQ - 1)  # burst continued

    assert proc.terminated is False
    assert lc._PROC is proc, "a burst in progress keeps its loaded model"


def test_idle_unload_never_kills_an_inflight_forward(monkeypatch):
    _fresh(monkeypatch)
    proc = _FakeProc()
    monkeypatch.setattr(lc, "_PROC", proc)
    monkeypatch.setattr(lc, "_READY", True)
    monkeypatch.setattr(lc, "_INFLIGHT", 1)

    lc._unload_after_idle(seq_at_schedule=lc._SEQ)

    assert proc.terminated is False
    assert lc._PROC is proc


def test_release_stops_now_and_next_request_relaunches(monkeypatch):
    _fresh(monkeypatch)
    proc = _FakeProc()
    monkeypatch.setattr(lc, "_PROC", proc)
    monkeypatch.setattr(lc, "_READY", True)

    assert lc.release(wait=0.0) is True
    assert proc.terminated is True
    assert lc.status() == "down"  # ensure_running() relaunches on the next use
    assert lc.release(wait=0.0) is False  # idempotent


def test_release_gives_up_on_a_stuck_inflight_forward(monkeypatch):
    _fresh(monkeypatch)
    proc = _FakeProc()
    monkeypatch.setattr(lc, "_PROC", proc)
    monkeypatch.setattr(lc, "_READY", True)
    monkeypatch.setattr(lc, "_INFLIGHT", 1)

    assert lc.release(wait=0.0) is False
    assert proc.terminated is False
    assert lc._PROC is proc


def test_negative_keep_alive_pins_the_model_resident(monkeypatch):
    _fresh(monkeypatch)
    spawned = []

    class _NoThread:
        def __init__(self, *a, **k):
            spawned.append(k.get("name"))

        def start(self):
            pass

    monkeypatch.setattr(lc.threading, "Thread", _NoThread)

    monkeypatch.setattr(lc, "_KEEP_ALIVE_SECONDS", -1.0)
    lc._schedule_unload(1)
    assert spawned == [], "a negative grace opts out of the whole policy"

    monkeypatch.setattr(lc, "_KEEP_ALIVE_SECONDS", 2.0)
    lc._schedule_unload(1)
    assert spawned == ["laya-idle-unloader"]


def test_request_arms_the_idle_unloader(monkeypatch):
    """Every completed round-trip schedules the burst-end check."""
    _fresh(monkeypatch)
    proc = _FakeProc()
    monkeypatch.setattr(lc, "_PROC", proc)
    monkeypatch.setattr(lc, "_READY", True)
    monkeypatch.setattr(lc, "available", lambda: True)
    monkeypatch.setattr(lc, "ensure_running", lambda: True)
    spawned = []

    class _NoThread:
        def __init__(self, *a, **k):
            spawned.append(k.get("name"))

        def start(self):
            pass

    monkeypatch.setattr(lc.threading, "Thread", _NoThread)
    lc._QUEUE.put(json.dumps({
        "id": str(lc._SEQ + 1), "answers": {"q": {"noul": 0.75}},
    }))

    assert lc.noul("state", "instructions") == 0.75
    assert "q" in proc.stdin.getvalue()
    assert spawned == ["laya-idle-unloader"]
    assert lc._INFLIGHT == 0
