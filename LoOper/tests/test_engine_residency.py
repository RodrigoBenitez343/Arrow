"""Engine residency: the hold that keeps Laya + the embed server loaded.

The burst policy is what makes a form pass pay a cold load per field.  These
tests pin the hold contract without spawning any engine:

  - a held engine is not unloaded by the idle unloader, nor by an unforced
    ``release()``;
  - the hold is refused when the machine is below its RAM budget, so a small
    box keeps the burst policy;
  - the generation server still wins the pool (``force=True``);
  - the embedding server's own start no longer kills Laya.

Run:  python -m pytest LoOper/tests/test_engine_residency.py
"""

import io

import AI.embedding_server as es
import AI.engine_residency as residency
import AI.laya_client as lc
from AI.llama_cpp_engine import LlamaCppEngine


class _FakeProc:
    def __init__(self):
        self.alive = True
        self.stdin = io.StringIO()

    def poll(self):
        return None if self.alive else 0

    def terminate(self):
        self.alive = False


def _fresh_laya(monkeypatch):
    monkeypatch.setattr(lc, "_PROC", None)
    monkeypatch.setattr(lc, "_READY", True)
    monkeypatch.setattr(lc, "_INFLIGHT", 0)
    monkeypatch.setattr(lc, "_HOLDS", 0)
    monkeypatch.setattr(lc, "_KEEP_ALIVE_SECONDS", 0.0)
    monkeypatch.setattr(lc, "_disabled", lambda: False)


def _fresh_embed(monkeypatch):
    monkeypatch.setattr(es, "_emb_engine", None)
    monkeypatch.setattr(es, "_EMB_INFLIGHT", 0)
    monkeypatch.setattr(es, "_EMB_SEQ", 0)
    monkeypatch.setattr(es, "_HOLDS", 0)
    monkeypatch.setattr(es, "_EMB_KEEP_ALIVE_SECONDS", 0.0)


def test_hold_refuses_when_ram_budget_is_not_met(monkeypatch):
    _fresh_laya(monkeypatch)
    monkeypatch.setattr(residency, "residents_allowed", lambda: False)
    assert lc.hold() is False
    assert lc.held() is False


def test_hold_is_a_balanced_refcount(monkeypatch):
    _fresh_laya(monkeypatch)
    monkeypatch.setattr(residency, "residents_allowed", lambda: True)
    assert lc.hold() is True and lc.hold() is True
    assert lc.held() is True
    lc.unhold()
    assert lc.held() is True
    lc.unhold()
    lc.unhold()  # floor at zero, never negative
    assert lc.held() is False and lc._HOLDS == 0


def test_a_held_engine_survives_release_and_idle_unload(monkeypatch):
    _fresh_laya(monkeypatch)
    monkeypatch.setattr(residency, "residents_allowed", lambda: True)
    proc = _FakeProc()
    monkeypatch.setattr(lc, "_PROC", proc)

    spawned = []

    class _NoThread:
        def __init__(self, *a, **k):
            spawned.append(k.get("name"))

        def start(self):
            pass

    monkeypatch.setattr(lc.threading, "Thread", _NoThread)

    assert lc.hold() is True
    assert lc.release(wait=0.0) is False
    assert proc.alive is True, "a held daemon is not killed under its caller"
    lc._schedule_unload(1)
    assert spawned == [], "the idle unloader stands down while held"

    lc.unhold()
    assert lc.release(wait=0.0) is True
    assert proc.alive is False


def test_generation_start_forces_a_held_engine_out(monkeypatch):
    _fresh_laya(monkeypatch)
    monkeypatch.setattr(residency, "residents_allowed", lambda: True)
    proc = _FakeProc()
    monkeypatch.setattr(lc, "_PROC", proc)
    assert lc.hold() is True

    gen = LlamaCppEngine({"embedding_mode": False})
    gen._release_sibling_bursts()
    assert proc.alive is False, "the generation server owns the memory pool"

    lc.unhold()


def test_embedding_start_does_not_kill_laya(monkeypatch):
    _fresh_laya(monkeypatch)
    monkeypatch.setattr(residency, "residents_allowed", lambda: True)
    proc = _FakeProc()
    monkeypatch.setattr(lc, "_PROC", proc)
    assert lc.hold() is True

    embed = LlamaCppEngine({"embedding_mode": True})
    embed._release_sibling_bursts()
    assert proc.alive is True, "an embed cold start must not kill Laya"

    lc.unhold()


def test_embedding_server_holds_the_same_way(monkeypatch):
    _fresh_embed(monkeypatch)
    monkeypatch.setattr(residency, "residents_allowed", lambda: True)

    class _FakeEngine:
        stopped = False

        def stop_server(self, quick=False):
            self.stopped = True

    eng = _FakeEngine()
    monkeypatch.setattr(es, "_emb_engine", eng)
    assert es.hold() is True
    assert es.release(wait=0.0) is False
    assert eng.stopped is False
    es.unhold()
    assert es.release(wait=0.0) is True
    assert eng.stopped is True


def test_the_form_node_holds_once_and_unholds_in_the_end(monkeypatch):
    from player.multi_sequence.worflow_interpreter_modules.executor_modules \
        .form_filler_modules.node import FormFillerNodeMixin

    _fresh_laya(monkeypatch)
    _fresh_embed(monkeypatch)
    monkeypatch.setattr(residency, "residents_allowed", lambda: True)
    held = []
    monkeypatch.setattr(lc, "hold",
                        lambda: (held.append("laya"), True)[1])
    monkeypatch.setattr(es, "hold",
                        lambda: (held.append("embed"), True)[1])
    monkeypatch.setattr(lc, "unhold", lambda: held.remove("laya"))
    monkeypatch.setattr(es, "unhold", lambda: held.remove("embed"))

    modules = FormFillerNodeMixin._ff_hold_engines()
    assert held == ["laya", "embed"], "one hold per engine, per pass"
    FormFillerNodeMixin._ff_release_engines(modules)
    assert held == [], "the pass end gives both engines back"


def test_the_form_node_holds_nothing_on_a_small_machine(monkeypatch):
    from player.multi_sequence.worflow_interpreter_modules.executor_modules \
        .form_filler_modules.node import FormFillerNodeMixin

    _fresh_laya(monkeypatch)
    _fresh_embed(monkeypatch)
    monkeypatch.setattr(residency, "residents_allowed", lambda: False)
    assert FormFillerNodeMixin._ff_hold_engines() == [], \
        "below the budget the burst policy stays in force (and nothing loads)"


def test_ram_budget_gate(monkeypatch):
    monkeypatch.setenv("LOOPER_RESIDENT_BUDGET_MB", "0")
    assert residency.residents_allowed() is True, "0 = operator forces resident"

    monkeypatch.setenv("LOOPER_RESIDENT_BUDGET_MB", "1024")
    monkeypatch.setattr(residency, "available_mb", lambda: 512.0)
    assert residency.residents_allowed() is False

    monkeypatch.setattr(residency, "available_mb", lambda: 4096.0)
    assert residency.residents_allowed() is True
