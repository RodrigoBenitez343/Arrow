"""Integration test: the agent overlay's Scheduler panel toggle.

Locks the header-button → panel swap so the panel/chat visibility pairing keeps
working (it depends on the explicit hidden flag, not isVisible(), because the
overlay is off-screen in tests and when collapsed).
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from PyQt5.QtWidgets import QApplication  # noqa: E402

from NGUI.scheduler_service import SchedulerService  # noqa: E402
from NGUI.widgets.agent_overlay import AgentOverlay  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _overlay(qapp, tmp_path):
    overlay = AgentOverlay()
    service = SchedulerService(project_root=str(tmp_path), check_interval_seconds=5)
    overlay.set_scheduler_service(service)
    return overlay, service


def test_button_hidden_without_service(qapp):
    overlay = AgentOverlay()
    assert overlay._scheduler_btn.isHidden()


def test_toggle_swaps_chat_and_panel(qapp, tmp_path):
    overlay, service = _overlay(qapp, tmp_path)
    assert not overlay._scheduler_btn.isHidden()

    overlay._toggle_scheduler()
    assert not overlay._scheduler_panel.isHidden()
    assert overlay._chat_scroll.isHidden()
    assert overlay._scheduler_btn.isChecked()

    overlay._toggle_scheduler()
    assert overlay._scheduler_panel.isHidden()
    assert not overlay._chat_scroll.isHidden()
    assert not overlay._scheduler_btn.isChecked()


def test_toggle_noop_without_service(qapp):
    overlay = AgentOverlay()
    overlay._toggle_scheduler()
    assert overlay._chat_scroll.isHidden() is False  # chat untouched
