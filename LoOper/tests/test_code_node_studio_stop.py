"""Regression: pressing Stop must actually cut off a running coding-agent
request, not only a node Run.

The header Stop button was previously wired only to the in-process exec
(`_stop_ref`).  When the agent loop spun on retries / hallucinated streams
there was no flag to break the stream and the loop guards were not armed, so
the only escape was killing the whole app.  `_on_stop` now sets BOTH the run
flag and the shared chat-stream flag, arms the abort guard, and hands the
header buttons back to the user.

Run:  python -m pytest LoOper/tests/test_code_node_studio_stop.py
"""

from player.code_agent_ops.panel.mixin_files_run import FilesRunMixin
from player.code_agent_ops.chat_stream import _ChatStreamWorker, _Aborted


class _FakeBtn:
    def __init__(self):
        self.enabled = None

    def setEnabled(self, v):
        self.enabled = v


class _StopHarness(FilesRunMixin):
    """Only the surface _on_stop touches (no Qt widgets)."""

    def __init__(self):
        self._stop_ref = {'flag': False}
        self._chat_stop_ref = {'flag': False}
        self._loop_aborted = False
        self._request_goal = 'write a todo app'
        self._pending_run = True
        self._stop_btn = _FakeBtn()
        self._run_btn = _FakeBtn()
        self.consoles = []

    # Stub the two live-UI helpers _on_stop calls.
    def _finish_live(self):
        self.finished_live = True

    def _append_console(self, text):
        self.consoles.append(text)


def test_on_stop_cuts_run_and_stream_and_rearms_buttons():
    h = _StopHarness()
    h.finished_live = False

    FilesRunMixin._on_stop(h)

    # Both workers told to stop, abort guard armed, request cleared.
    assert h._stop_ref['flag'] is True
    assert h._chat_stop_ref['flag'] is True
    assert h._loop_aborted is True
    assert h._request_goal is None
    assert h._pending_run is False
    assert h.finished_live is True
    # Buttons handed back: Stop idle, Run usable again.
    assert h._stop_btn.enabled is False
    assert h._run_btn.enabled is True


def test_stream_worker_honours_shared_stop_flag():
    # The stream decides to die purely from the flag the Stop button sets.
    ref = {'flag': False}
    w = _ChatStreamWorker('p', 'm', use_llamacpp=True, system='', stop_ref=ref)
    assert w._stopped() is False
    ref['flag'] = True
    assert w._stopped() is True
    # _Aborted subclasses RuntimeError so a mid-stream cancel can never be
    # salvaged as a completed (partial) reply.
    assert issubclass(_Aborted, RuntimeError)
