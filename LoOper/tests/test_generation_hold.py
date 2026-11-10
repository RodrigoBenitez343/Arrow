"""The generation server's resident hold: ref count, gate, and the client.

Live evidence this guards: a 44 min form pass started and stopped ``llama-server``
29 times (121.9 s of pure startup) and paid a full prompt re-prefill on every
field, because nothing survived between fields for ``--cache-reuse`` / ``--keep``
to reuse.  The hold is what makes those flags mean anything.
"""

import asyncio
import sys
import time

import pytest

from AI import api, engine_metrics, generation_hold


class _Engine:
    """A llama-server that records how often it was stopped."""

    is_running = True

    def __init__(self):
        self.stopped = 0
        # The unloader only stops the exact process it recognises; without one
        # there is nothing to stop (server already gone).
        self.server_process = object()

    def stop_server(self, quick=False):
        self.stopped += 1


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    engine_metrics.reset()
    monkeypatch.setattr(engine_metrics, "_append", lambda line: None)
    monkeypatch.setattr(api, "_LLAMACPP_HOLDS", 0)
    monkeypatch.setattr(api, "_LLAMACPP_UNLOAD_IDLE_SECONDS", 0.0)
    monkeypatch.setattr("AI.engine_residency.residents_allowed", lambda: True)
    return monkeypatch


def test_the_hold_is_ref_counted_and_the_last_unhold_releases(clean):
    engine = _Engine()
    clean.setattr(api, "LLAMACPP_ENGINE", engine)
    released = []
    clean.setattr(api, "_unload_llamacpp_after_idle",
                  lambda seq: released.append(seq))

    assert api.hold_llamacpp() is True
    assert api.hold_llamacpp() is True
    assert api.llamacpp_held() is True

    api.unhold_llamacpp()
    assert api.llamacpp_held() is True, "a nested hold still stands"
    time.sleep(0.1)
    assert released == [], "a held engine is not released mid-pass"

    api.unhold_llamacpp()
    assert api.llamacpp_held() is False
    for _ in range(50):
        if released:
            break
        time.sleep(0.02)
    assert released, "the LAST unhold schedules the release it stood in for"


def test_a_held_engine_is_never_stopped_by_the_idle_unloader(clean):
    engine = _Engine()
    clean.setattr(api, "LLAMACPP_ENGINE", engine)
    api.hold_llamacpp()

    api._unload_llamacpp_after_idle(api._LLAMACPP_REQUEST_SEQ)
    assert engine.stopped == 0, "the burst unloader must stand down"


def test_an_unheld_engine_is_still_released_by_the_idle_unloader(clean):
    engine = _Engine()
    clean.setattr(api, "LLAMACPP_ENGINE", engine)

    api._unload_llamacpp_after_idle(api._LLAMACPP_REQUEST_SEQ)
    assert engine.stopped == 1, "nothing holds it, so the burst policy stands"


def test_below_the_resident_budget_the_hold_is_refused(clean):
    clean.setattr("AI.engine_residency.residents_allowed", lambda: False)
    assert api.hold_llamacpp() is False
    assert api.llamacpp_held() is False


def test_the_release_endpoint_ignores_a_held_engine(clean):
    engine = _Engine()
    clean.setattr(api, "LLAMACPP_ENGINE", engine)
    api.hold_llamacpp()
    out = asyncio.run(api.llamacpp_release())
    assert out.get("held") is True
    assert engine.stopped == 0, "a burst-end release must not undo a hold"


def test_the_hold_is_measurable(clean):
    clean.setattr(api, "LLAMACPP_ENGINE", _Engine())
    clean.setattr(api, "_unload_llamacpp_after_idle", lambda seq: None)
    api.hold_llamacpp()
    api.unhold_llamacpp()

    rows = [r for r in engine_metrics.snapshot() if r.get("kind") == "engine_hold"]
    assert len(rows) == 2, "one sample per hold change"
    assert rows[0]["held"] == 1 and rows[0]["engine_code"] == 3
    assert rows[1]["held"] == 0


def test_the_client_posts_to_the_gateway(clean):
    seen = []

    class _Response:
        status_code = 200

        @staticmethod
        def json():
            return {"held": True}

    class _Requests:
        @staticmethod
        def post(url, timeout=None):
            seen.append(url)
            return _Response()

    clean.setitem(sys.modules, "requests", _Requests)
    clean.setattr(generation_hold, "_api_url", lambda: "http://127.0.0.1:8000")

    assert generation_hold.hold() is True
    generation_hold.unhold()
    assert seen == ["http://127.0.0.1:8000/llamacpp/hold",
                    "http://127.0.0.1:8000/llamacpp/unhold"]


def test_no_gateway_is_a_no_op_not_a_fault(clean):
    clean.setattr(generation_hold, "_api_url", lambda: "")
    assert generation_hold.hold() is False
    generation_hold.unhold()          # must not raise


def test_a_failing_gateway_does_not_break_the_pass(clean):
    class _Requests:
        @staticmethod
        def post(url, timeout=None):
            raise OSError("connection refused")

    clean.setitem(sys.modules, "requests", _Requests)
    clean.setattr(generation_hold, "_api_url", lambda: "http://127.0.0.1:8000")
    assert generation_hold.hold() is False


def test_only_the_local_engine_is_held():
    from player.multi_sequence.worflow_interpreter_modules.executor_modules \
        .form_filler_modules.node import _ff_engine_is_local

    assert _ff_engine_is_local({"data": {"engine": "llamacpp"}}) is True
    assert _ff_engine_is_local({"data": {"engine": ""}}) is True
    assert _ff_engine_is_local({"data": {"engine": "ollama"}}) is False
    assert _ff_engine_is_local({"data": {"engine": "openai"}}) is False
