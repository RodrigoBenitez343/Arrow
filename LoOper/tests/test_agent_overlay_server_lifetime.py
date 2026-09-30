"""Agent-overlay regressions: server lifetime, transcripts, stop semantics.

All from the 2026-09-19 session:
- collapsing the overlay to the dot (still agent mode) killed the web server,
  disconnecting the mobile client;
- messages produced by a running chain were recorded in whatever coworker the
  user had selected *by then*, so switching mid-run mixed the transcripts;
- stopping a run relied on the chain's graceful exit: a chain parked on an
  Input ask never unwound, and the next user message was eaten as the answer.
"""

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from PyQt5.QtWidgets import QApplication  # noqa: E402

from NGUI.widgets.agent_overlay import AgentOverlay  # noqa: E402


class _StubController:
    def __init__(self, chain_id="brain", path="/tmp/brain.json"):
        self._system_chain_id = chain_id
        self._system_chain_path = path
        self._ask_user_callback = None
        self._output_display_callback = None
        self.queries = []

    def list_system_chains(self):
        return [
            {"id": "brain", "name": "Brain", "path": "/tmp/brain.json"},
            {"id": "helper", "name": "Helper", "path": "/tmp/helper.json"},
        ]

    def set_system_chain(self, path):
        self._system_chain_path = path
        return True

    def handle_request(self, text, stop_flag=None):
        self.queries.append(text)
        return "done"


class _StubWebServer:
    def __init__(self):
        self.stopped = 0
        self.state = type("S", (), {"ws_send": None, "ws_loop": None})()

    def stop(self):
        self.stopped += 1


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def overlay(qapp):
    ov = AgentOverlay()
    ov._chat_controller = _StubController()
    ov._web_server = _StubWebServer()
    ov._web_server_started = True
    return ov


def test_dot_collapse_keeps_web_server(overlay):
    overlay.show()
    overlay._collapse_to_dot()
    assert overlay._web_server.stopped == 0
    assert overlay._web_server_started is True


def test_full_close_stops_web_server(overlay):
    overlay.show()
    overlay.hide_overlay()
    assert overlay._web_server.stopped == 1
    assert overlay._web_server_started is False


def test_bubbles_follow_running_chain_not_selected_coworker(overlay):
    overlay._active_chain_id = "brain"
    overlay._request_chain_id = "brain"

    # User switches to another coworker while the brain's run is still going.
    overlay._active_chain_id = "helper"
    overlay._add_bubble("answer from brain", is_user=False)

    assert [m["text"] for m in overlay._conversations["brain"]] == ["answer from brain"]
    assert overlay._conversations.get("helper", []) == []


def test_idle_bubbles_follow_selected_coworker(overlay):
    overlay._request_chain_id = ""
    overlay._active_chain_id = "helper"
    overlay._add_bubble("hi", is_user=True)
    assert [m["text"] for m in overlay._conversations["helper"]] == ["hi"]


# ---------------------------------------------------------------------------
# Stop button / hard stop
# ---------------------------------------------------------------------------


def test_stop_button_visible_only_while_executing(overlay):
    overlay._executing = False
    overlay._update_stop_btn()
    assert overlay._stop_btn.isHidden() is True

    overlay._executing = True
    overlay._update_stop_btn()
    assert overlay._stop_btn.isHidden() is False


def test_stop_releases_pending_asks(overlay):
    """A chain parked on 'Can I do something else for you?' must unwind."""
    overlay._input_ask_active = True
    overlay._input_ask_event.clear()
    overlay._web_ask_active = True
    overlay._web_ask_event.clear()

    overlay._stop_execution()

    assert overlay._input_ask_active is False
    assert overlay._input_ask_event.is_set() is True
    assert overlay._web_ask_active is False
    assert overlay._web_ask_event.is_set() is True


def test_stop_frees_ui_and_latches_the_request_flag(overlay):
    seq, flag = overlay._start_request()
    assert overlay._executing is True

    overlay._stop_execution()

    assert overlay._executing is False
    assert flag.is_set() is True           # the stopped worker stays stopped
    assert overlay._request_stop is None
    assert overlay._finish_request(seq) is False  # stale worker stays mute


def test_new_dispatch_does_not_clear_a_latched_stop(overlay):
    seq, first_flag = overlay._start_request()
    overlay._stop_execution()
    seq2, second_flag = overlay._start_request()

    assert first_flag is not second_flag
    assert first_flag.is_set() is True      # clearing _stop_event must not revive it
    assert overlay._finish_request(seq2) is True


def test_web_stop_does_not_dispatch_a_query(overlay):
    result = overlay._handle_web_message("__STOP__")
    time.sleep(0.05)
    assert result == "Stop signal sent."
    assert overlay._chat_controller.queries == []


def test_stale_answer_becomes_a_new_query(overlay):
    """After a stop the web client may still tag the message as an answer."""
    overlay._web_ask_active = False
    overlay._handle_web_message('{"type":"answer","text":"find me jobs"}')

    deadline = time.time() + 2
    while time.time() < deadline and not overlay._chat_controller.queries:
        time.sleep(0.02)
    assert overlay._chat_controller.queries == ["find me jobs"]


# ---------------------------------------------------------------------------
# Web parity: busy lifecycle, answered asks, history sync
# ---------------------------------------------------------------------------


def _capture_web(overlay):
    """Record the JSON frames pushed to web clients."""
    sent = []
    overlay._web_send_json = lambda payload: sent.append(payload)
    return sent


def test_runs_broadcast_busy_lifecycle(overlay):
    """Any run (desktop- or web-started) tells clients it is live."""
    sent = _capture_web(overlay)

    seq, _flag = overlay._start_request()
    assert {"type": "agent_busy"} in sent

    overlay._finish_request(seq)
    assert {"type": "agent_done"} in sent


def test_sync_state_replays_history_and_pending_ask(overlay):
    """A reconnecting phone must see the transcript and the open question."""
    sent = _capture_web(overlay)
    overlay._active_chain_id = "brain"
    overlay._conversations["brain"] = [
        {"text": "find jobs", "is_user": True, "mode": "text", "asset": None},
        {"text": "Can I do something else?", "is_user": False, "mode": "text", "asset": None},
    ]
    overlay._executing = True
    overlay._web_ask_active = True
    overlay._last_ask_question = "Can I do something else?"

    overlay._web_sync_state()

    history = [p for p in sent if p.get("type") == "history"][0]
    assert [m["text"] for m in history["messages"]] == [
        "find jobs", "Can I do something else?",
    ]
    assert {"type": "agent_busy"} in sent
    assert {"type": "ask", "question": "Can I do something else?"} in sent


def test_plain_text_while_ask_active_is_the_answer(overlay):
    """A phone that missed the ask frame still answers it (no new run)."""
    overlay._web_ask_active = True
    overlay._web_ask_event.clear()

    result = overlay._handle_web_message("yes please")

    assert result == "Answer received."
    assert overlay._web_ask_response == "yes please"
    assert overlay._web_ask_event.is_set() is True
    assert overlay._chat_controller.queries == []


def test_desktop_run_mirrors_final_reply_to_phone(overlay):
    """A desktop-started run's answer must reach the phone too."""
    mirrored = []
    overlay._web_send_text = lambda text: mirrored.append(text)

    overlay._dispatch("hello")

    deadline = time.time() + 2
    while time.time() < deadline and not mirrored:
        time.sleep(0.02)
    assert mirrored == ["done"]
