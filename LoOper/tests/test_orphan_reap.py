"""Orphan llama.cpp process reaper.

Windows children outlive the parent: a crashed run leaves llama-server
holding its model (and its port), so the next run lands on a FRESH port and
the orphan's RAM is never reclaimed — a long multi-LLM chain then OOMs.
`reap_orphan_llama_servers` kills those leftovers at gateway startup.

Run:  python -m pytest LoOper/tests/test_orphan_reap.py
"""

import os

from AI import llama_cpp_engine as eng


def _csv(rows):
    """Build the tasklist /FO CSV /NH body for (image, pid) rows."""
    return "\n".join('"%s","%s","Console","1","9,876 K"' % (img, pid)
                     for img, pid in rows)


def test_reaps_only_llama_binaries_and_skips_self(monkeypatch):
    """llama.cpp binaries (except this process) are killed; anything else
    (a browser, the app itself) is left alone."""
    me = os.getpid()
    rows = [
        ("llama-server.exe", "1111"),
        ("llama-mtmd-cli.exe", "2222"),
        ("chrome.exe", "3333"),
        ("python.exe", me),
        ("llama-server.exe", me),          # never kill ourselves
        ("llama-embedding.exe", "4444"),
    ]
    killed = []

    class _Done:
        def __init__(self, out=""):
            self.stdout = out
            self.returncode = 0

    def _fake_run(cmd, **kw):
        if cmd and cmd[0] == "tasklist":
            return _Done(_csv(rows))
        if cmd and cmd[0] == "taskkill":
            killed.append(cmd[-1])
            return _Done("")
        return _Done("")

    monkeypatch.setattr(eng.subprocess, "run", _fake_run)
    n = eng.reap_orphan_llama_servers()

    assert n == 3                                  # the three real orphans
    assert set(killed) == {"1111", "2222", "4444"}
    assert "3333" not in killed                    # chrome untouched
    assert str(me) not in killed                   # self untouched


def test_reap_is_a_noop_when_nothing_is_running(monkeypatch):
    calls = []

    class _Done:
        stdout = 'INFO: No tasks are running which match the specified criteria.'

    def _fake_run(cmd, **kw):
        calls.append(cmd[0] if cmd else "")
        return _Done()

    monkeypatch.setattr(eng.subprocess, "run", _fake_run)
    assert eng.reap_orphan_llama_servers() == 0
    assert "taskkill" not in calls                 # nothing to kill


# --------------------------------------------------------------- burst intent

def test_quick_stop_keeps_ever_started_for_later_sweep():
    """A quick per-burst unload clears _server_started but NOT _ever_started,
    so a later non-quick stop_server() still runs the port sweep and can reap
    a lingering process (the port-sweep gate is `started or ever_started`)."""
    e = eng.LlamaCppEngine({"model_path": "x.gguf", "server_port": 19999})
    e._server_started = True
    e._ever_started = True
    e.stop_server(quick=True)                       # no process -> no I/O
    assert e._server_started is False              # live flag cleared
    assert e._ever_started is True                 # sweep stays enabled
    assert (e._server_started or e._ever_started) is True


def test_burst_release_has_no_timeout_and_retries(monkeypatch):
    """_fire_llamacpp_release posts with NO timeout and retries, so a release
    blocked by the gateway's lifecycle lock (another node generating) or a
    transient hiccup is never silently dropped."""
    import time
    import requests
    from player.multi_sequence.llm_executor_resources.executor import (
        _fire_llamacpp_release,
    )
    calls = []

    class _Resp:
        status_code = 200

    def _post(url, json=None, timeout="missing"):
        calls.append(timeout)
        if len(calls) < 3:
            raise RuntimeError("gateway busy")
        return _Resp()

    monkeypatch.setattr(requests, "post", _post)
    monkeypatch.setattr(time, "sleep", lambda *a, **k: None)
    _fire_llamacpp_release("http://localhost:8000")
    assert len(calls) == 3                          # retried, succeeded
    assert all(t is None for t in calls)           # never a timeout


def test_burst_release_gives_up_after_three_failures(monkeypatch):
    """Total gateway failure is logged, not raised, and bounded to 3 tries."""
    import time
    import requests
    from player.multi_sequence.llm_executor_resources.executor import (
        _fire_llamacpp_release,
    )
    calls = []

    def _post(url, json=None, timeout=None):
        calls.append(url)
        raise RuntimeError("down")

    monkeypatch.setattr(requests, "post", _post)
    monkeypatch.setattr(time, "sleep", lambda *a, **k: None)
    _fire_llamacpp_release("http://localhost:8000")   # must not raise
    assert len(calls) == 3


def test_burst_release_is_a_noop_without_a_base(monkeypatch):
    import requests
    from player.multi_sequence.llm_executor_resources.executor import (
        _fire_llamacpp_release,
    )
    calls = []
    monkeypatch.setattr(requests, "post", lambda *a, **k: calls.append(1))
    _fire_llamacpp_release("")
    assert calls == []
