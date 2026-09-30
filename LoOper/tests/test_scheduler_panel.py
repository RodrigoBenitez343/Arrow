"""Tests for NGUI/widgets/scheduler_panel.py — the in-overlay Scheduler panel.

Runs headless (offscreen Qt).  Covers the form→schedule mapping, list rendering
and the display helpers; dialog-based paths (delete confirm / error boxes) are
intentionally not exercised.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from PyQt5.QtCore import QEvent, QTime  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from NGUI.scheduler_service import SchedulerService  # noqa: E402
from NGUI.widgets.scheduler_panel import SchedulerPanel  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def service(tmp_path):
    return SchedulerService(project_root=str(tmp_path), check_interval_seconds=5)


def _panel(qapp, service, tmp_path):
    panel = SchedulerPanel()
    panel.set_service(service)
    chain = tmp_path / "chains"
    chain.mkdir(exist_ok=True)
    chain_file = chain / "report.json"
    chain_file.write_text("{}", encoding="utf-8")
    return panel, str(chain_file)


def _flush_deferred_deletes(qapp):
    """Make Qt process deleteLater() deletions like the real event loop does."""
    qapp.sendPostedEvents(None, QEvent.DeferredDelete)
    qapp.processEvents()


# ── display helpers ──────────────────────────────────────────────────────

def test_when_text_all_types():
    assert "daily" in SchedulerPanel._when_text({"type": "daily", "daily_time": "09:00"})
    assert "weekly" in SchedulerPanel._when_text(
        {"type": "weekly", "weekly_time": "08:00", "days_of_week": [0, 2]}
    )
    assert "every 15 min" in SchedulerPanel._when_text(
        {"type": "interval", "interval_minutes": 15}
    )
    assert "once" in SchedulerPanel._when_text(
        {"type": "once", "once_datetime": "2026-09-20T09:00:00"}
    )


def test_chain_name():
    assert SchedulerPanel._chain_name({"chain_path": r"a\b\report.json"}) == "report.json"
    assert SchedulerPanel._chain_name({}) == "\u2014"


# ── form → schedule ──────────────────────────────────────────────────────

def test_save_daily_creates_schedule(qapp, service, tmp_path):
    panel, chain_file = _panel(qapp, service, tmp_path)
    panel._open_form(None)
    panel._f_chain.setText(chain_file)
    panel._f_type.setCurrentText("daily")
    panel._f_daily.setTime(QTime(9, 30))
    panel._save()

    schedules = service.list_schedules()
    assert len(schedules) == 1
    assert schedules[0]["type"] == "daily"
    assert schedules[0]["daily_time"] == "09:30"
    assert os.path.normpath(schedules[0]["chain_path"]) == os.path.normpath(chain_file)


def test_save_weekly_days(qapp, service, tmp_path):
    panel, chain_file = _panel(qapp, service, tmp_path)
    panel._open_form(None)
    panel._f_chain.setText(chain_file)
    panel._f_type.setCurrentText("weekly")
    panel._f_weekly.setTime(QTime(8, 0))
    panel._f_days[0].setChecked(True)
    panel._f_days[4].setChecked(True)
    panel._save()

    sched = service.list_schedules()[0]
    assert sched["type"] == "weekly"
    assert sched["weekly_time"] == "08:00"
    assert sched["days_of_week"] == [0, 4]


def test_edit_updates_existing(qapp, service, tmp_path):
    panel, chain_file = _panel(qapp, service, tmp_path)
    created = service.add_schedule({
        "type": "daily", "daily_time": "07:00", "chain_path": chain_file, "name": "morning",
    })
    panel.refresh()
    panel._open_form(service.get_schedule(created["id"]))
    assert panel._f_name.text() == "morning"
    panel._f_daily.setTime(QTime(6, 15))
    panel._save()

    assert service.get_schedule(created["id"])["daily_time"] == "06:15"


def test_set_service_renders_rows(qapp, service, tmp_path):
    panel, chain_file = _panel(qapp, service, tmp_path)
    service.add_schedule({"type": "daily", "daily_time": "07:00", "chain_path": chain_file})
    panel.refresh()
    assert panel._empty_label.isHidden()
    assert len(panel._row_widgets) == 1


def test_refresh_survives_deferred_deletes(qapp, service, tmp_path):
    """Regression: refresh() must not touch a deleteLater()-ed empty label.

    The real app processes DeferredDelete between button clicks; the old
    _clear_list() deleted the persistent label, so the next refresh() hit a
    stale C++ wrapper and aborted the whole application.
    """
    panel, chain_file = _panel(qapp, service, tmp_path)
    panel.refresh()  # empty -> empty label shown
    _flush_deferred_deletes(qapp)

    created = service.add_schedule(
        {"type": "daily", "daily_time": "07:00", "chain_path": chain_file}
    )
    panel.refresh()  # crashed here before the fix
    _flush_deferred_deletes(qapp)
    assert len(panel._row_widgets) == 1
    assert panel._empty_label.isHidden()

    service.remove_schedule(created["id"])
    panel.refresh()
    _flush_deferred_deletes(qapp)
    panel.refresh()
    assert not panel._empty_label.isHidden()


def test_refresh_replaces_rows(qapp, service, tmp_path):
    panel, chain_file = _panel(qapp, service, tmp_path)
    service.add_schedule({"type": "daily", "daily_time": "07:00", "chain_path": chain_file})
    panel.refresh()
    first = list(panel._row_widgets)
    _flush_deferred_deletes(qapp)
    panel.refresh()
    assert len(panel._row_widgets) == 1
    assert panel._row_widgets[0] is not first[0]


def test_no_service_shows_hint(qapp):
    panel = SchedulerPanel()
    panel.set_service(None)
    assert "not available" in panel._empty_label.text()
