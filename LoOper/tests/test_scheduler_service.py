"""Unit tests for NGUI/scheduler_service.py — schedule CRUD, persistence,
and next-run computation for all four schedule types.
"""

import json
from datetime import datetime, timedelta

import pytest

from NGUI.scheduler_service import SchedulerService


@pytest.fixture
def service(tmp_path):
    return SchedulerService(project_root=str(tmp_path), check_interval_seconds=5)


# ---------------------------------------------------------------------------
# next_run computation
# ---------------------------------------------------------------------------


def test_once_future(service):
    future = datetime.now() + timedelta(hours=1)
    sched = {"type": "once", "once_datetime": future.isoformat()}
    assert service._compute_next_run(sched) == future.isoformat()


def test_once_past_returns_none(service):
    past = datetime.now() - timedelta(hours=1)
    sched = {"type": "once", "once_datetime": past.isoformat()}
    assert service._compute_next_run(sched) is None


def test_once_missing_datetime(service):
    assert service._compute_next_run({"type": "once"}) is None


def test_daily_today_future(service):
    now = datetime(2026, 1, 1, 10, 0, 0)
    sched = {"type": "daily", "daily_time": "23:30"}
    result = service._compute_next_run(sched, from_time=now)
    assert result == "2026-01-01T23:30:00"


def test_daily_rolls_to_tomorrow(service):
    now = datetime(2026, 1, 1, 10, 0, 0)
    sched = {"type": "daily", "daily_time": "09:00"}
    result = service._compute_next_run(sched, from_time=now)
    assert result == "2026-01-02T09:00:00"


def test_daily_missing_time(service):
    assert service._compute_next_run({"type": "daily"}) is None


def test_weekly_picks_next_day(service):
    # 2026-01-01 is a Thursday (weekday 3); schedule for Monday(0)+Friday(4)
    now = datetime(2026, 1, 1, 12, 0, 0)
    sched = {"type": "weekly", "weekly_time": "08:00", "days_of_week": [0, 4]}
    result = service._compute_next_run(sched, from_time=now)
    dt = datetime.fromisoformat(result)
    assert dt.weekday() == 4  # Friday comes first
    assert result == "2026-01-02T08:00:00"


def test_weekly_rolls_into_next_week(service):
    now = datetime(2026, 1, 1, 12, 0, 0)  # Thursday
    sched = {"type": "weekly", "weekly_time": "08:00", "days_of_week": [0]}  # Monday
    result = service._compute_next_run(sched, from_time=now)
    dt = datetime.fromisoformat(result)
    assert dt.weekday() == 0
    assert dt.date() == now.date() + timedelta(days=4)


def test_weekly_missing_fields(service):
    assert service._compute_next_run({"type": "weekly"}) is None
    assert service._compute_next_run({"type": "weekly", "weekly_time": "08:00"}) is None


def test_interval_from_start_datetime(service):
    now = datetime(2026, 1, 1, 10, 0, 0)
    future_start = (now + timedelta(minutes=30)).isoformat()
    sched = {"type": "interval", "interval_minutes": 15, "start_datetime": future_start}
    assert service._compute_next_run(sched, from_time=now) == future_start


def test_interval_start_immediately_when_past(service):
    now = datetime(2026, 1, 1, 10, 0, 0)
    past_start = (now - timedelta(hours=1)).isoformat()
    sched = {"type": "interval", "interval_minutes": 15, "start_datetime": past_start}
    assert service._compute_next_run(sched, from_time=now) == now.isoformat()


def test_interval_from_last_run(service):
    now = datetime(2026, 1, 1, 10, 0, 0)
    last = (now - timedelta(minutes=5)).isoformat()
    sched = {"type": "interval", "interval_minutes": 30, "last_run": last}
    result = service._compute_next_run(sched, from_time=now)
    # last_run + 30min = 10:25, which is in the future relative to 10:00
    assert result == (datetime.fromisoformat(last) + timedelta(minutes=30)).isoformat()


def test_interval_invalid(service):
    assert service._compute_next_run({"type": "interval", "interval_minutes": 0}) is None
    assert service._compute_next_run({"type": "unknown"}) is None


# ---------------------------------------------------------------------------
# Parse helpers
# ---------------------------------------------------------------------------


def test_parse_dt():
    assert SchedulerService._parse_dt(None) is None
    assert SchedulerService._parse_dt("garbage") is None
    assert SchedulerService._parse_dt("2026-01-01T10:00:00") == datetime(2026, 1, 1, 10, 0, 0)


def test_parse_hhmm():
    assert SchedulerService._parse_hhmm("08:30") == (8, 30)


# ---------------------------------------------------------------------------
# CRUD + persistence
# ---------------------------------------------------------------------------


def test_add_schedule_assigns_id_and_computes_next_run(service, tmp_path):
    sched = service.add_schedule({
        "type": "once",
        "once_datetime": (datetime.now() + timedelta(hours=2)).isoformat(),
        "chain_path": "chains/demo.json",
    })
    assert "id" in sched
    assert sched["enabled"] is True
    assert sched["next_run"] is not None
    assert service.get_schedule(sched["id"]) is not None
    # Persisted to disk
    assert (tmp_path / "schedules.json").exists()


def test_list_schedules_includes_readable_next_run(service):
    sched = service.add_schedule({
        "type": "daily",
        "daily_time": "12:00",
        "chain_path": "chains/demo.json",
    })
    listed = service.list_schedules()[0]
    assert "next_run_readable" in listed
    assert listed["id"] == sched["id"]


def test_update_schedule_requires_id(service):
    with pytest.raises(ValueError):
        service.update_schedule({"type": "daily"})


def test_update_schedule_unknown_id(service):
    with pytest.raises(KeyError):
        service.update_schedule({"id": "ghost", "type": "daily"})


def test_update_schedule_recomputes_next_run(service):
    sched = service.add_schedule({
        "type": "once",
        "once_datetime": (datetime.now() + timedelta(hours=2)).isoformat(),
        "chain_path": "chains/demo.json",
    })
    updated = service.update_schedule({
        "id": sched["id"],
        "type": "daily",
        "daily_time": "06:15",
        "chain_path": "chains/demo.json",
    })
    assert updated["type"] == "daily"
    assert updated["next_run"] is not None


def test_remove_schedule(service):
    sched = service.add_schedule({"type": "once", "chain_path": "c.json"})
    service.remove_schedule(sched["id"])
    assert service.get_schedule(sched["id"]) is None
    service.remove_schedule("ghost")  # no error


def test_set_enabled_toggles(service):
    sched = service.add_schedule({"type": "once", "chain_path": "c.json"})
    service.set_enabled(sched["id"], False)
    assert service.get_schedule(sched["id"])["enabled"] is False
    with pytest.raises(KeyError):
        service.set_enabled("ghost", True)


def test_run_now_unknown_id(service):
    with pytest.raises(KeyError):
        service.run_now("ghost")


def test_run_now_launches_chain(service, tmp_path, monkeypatch):
    sched = service.add_schedule({
        "type": "interval",
        "interval_minutes": 60,
        "chain_path": "chains/demo.json",
    })
    launched = []
    monkeypatch.setattr(service, "_launch_chain",
                        lambda schedule: launched.append(schedule["id"]))
    service.run_now(sched["id"])
    assert launched == [sched["id"]]
    assert service.get_schedule(sched["id"])["last_run"] is not None


def test_load_migrates_list_format(tmp_path):
    storage = tmp_path / "schedules.json"
    storage.write_text(json.dumps([
        {"id": "s1", "type": "once", "chain_path": "c.json"},
    ]), encoding="utf-8")
    service = SchedulerService(project_root=str(tmp_path))
    assert service.get_schedule("s1") is not None


def test_load_corrupt_file_defaults_empty(tmp_path):
    storage = tmp_path / "schedules.json"
    storage.write_text("{corrupt", encoding="utf-8")
    service = SchedulerService(project_root=str(tmp_path))
    assert service.list_schedules() == []


def test_load_missing_file_defaults_empty(tmp_path):
    service = SchedulerService(project_root=str(tmp_path))
    assert service.list_schedules() == []


def test_start_stop_lifecycle(service):
    service.start()
    service.start()  # idempotent
    assert service._running is True
    service.stop()
    assert service._running is False
    service.stop()  # idempotent
