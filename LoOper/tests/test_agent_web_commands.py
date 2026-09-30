"""Tests for the agent-mode web panel commands (chains / schedules / coworkers).

The web client drives these over the WebSocket; the overlay answers with JSON
payloads pushed through ``_web_send_json`` (captured here instead of a live WS).
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from PyQt5.QtWidgets import QApplication  # noqa: E402

from NGUI.scheduler_service import SchedulerService  # noqa: E402
from NGUI.widgets.agent_overlay import AgentOverlay  # noqa: E402


class _StubController:
    """Minimal stand-in for SimpleChainRouter (chains dir + system chains)."""

    def __init__(self, chains_dir):
        self._chains_dir = chains_dir
        self._system_chain_path = ""
        self.selected = None

    def list_system_chains(self):
        return [
            {"id": "brain", "name": "Brain",
             "path": os.path.join(self._chains_dir, "brain.json")},
            {"id": "helper", "name": "Helper",
             "path": os.path.join(self._chains_dir, "helper.json")},
        ]

    def set_system_chain(self, path):
        self.selected = path
        self._system_chain_path = path
        return True


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def overlay(qapp, tmp_path):
    chains = tmp_path / "chains"
    chains.mkdir()
    for name in ("brain", "helper", "demo"):
        (chains / f"{name}.json").write_text("{}", encoding="utf-8")

    ov = AgentOverlay()
    ov._chat_controller = _StubController(str(chains))
    service = SchedulerService(project_root=str(tmp_path), check_interval_seconds=5)
    ov._scheduler_service = service
    ov._launched = []
    service.run_chain_now = lambda path, **kw: ov._launched.append((path, kw))
    ov._sent = []
    ov._web_send_json = lambda payload: ov._sent.append(payload)
    return ov


def _last(ov, kind):
    for payload in reversed(ov._sent):
        if payload.get("type") == kind:
            return payload
    return None


def test_list_chains(overlay):
    assert overlay._handle_web_command({"type": "list_chains"}) is True
    payload = _last(overlay, "chains")
    assert sorted(c["name"] for c in payload["chains"]) == ["brain", "demo", "helper"]


def test_run_chain(overlay):
    assert overlay._handle_web_command({"type": "run_chain", "rel": "demo.json"}) is True
    path, kw = overlay._launched[0]
    assert os.path.basename(path) == "demo.json"
    # A chain the phone started must be answerable and stoppable from the
    # phone: the run gets the chat ask surface (Input-node questions), the
    # overlay's stop flag and a completion hook, and the client is told it is
    # busy so its Stop button appears at all.
    assert callable(kw.get("ask_user_callback"))
    assert kw["stop_flag"]() is False
    assert callable(kw.get("on_complete"))
    assert _last(overlay, "agent_busy") is not None


def test_web_started_chain_is_stoppable_and_reports_done(overlay):
    overlay._handle_web_command({"type": "run_chain", "rel": "demo.json"})
    _path, kw = overlay._launched[0]
    assert overlay._executing is True

    overlay._stop_execution()

    assert kw["stop_flag"]() is True
    kw["on_complete"]()
    assert overlay._executing is False
    assert _last(overlay, "agent_done") is not None


def test_list_coworkers(overlay):
    overlay._handle_web_command({"type": "list_coworkers"})
    payload = _last(overlay, "coworkers")
    assert {c["id"] for c in payload["coworkers"]} == {"brain", "helper"}


def test_select_coworker(overlay):
    overlay._handle_web_command({"type": "select_coworker", "id": "helper"})
    assert overlay._chat_controller.selected.endswith("helper.json")
    assert _last(overlay, "coworkers") is not None


def test_schedule_crud(overlay):
    overlay._handle_web_command({"type": "save_schedule", "data": {
        "name": "daily demo", "type": "daily", "daily_time": "09:00",
        "chain_path": "demo.json"}})
    schedules = _last(overlay, "schedules")["schedules"]
    assert len(schedules) == 1
    sid = schedules[0]["id"]

    overlay._handle_web_command({"type": "toggle_schedule", "id": sid, "enabled": False})
    assert _last(overlay, "schedules")["schedules"][0]["enabled"] is False

    overlay._handle_web_command({"type": "delete_schedule", "id": sid})
    assert _last(overlay, "schedules")["schedules"] == []


def test_unknown_type_falls_through_to_chat(overlay):
    assert overlay._handle_web_command({"type": "not_a_command"}) is False
