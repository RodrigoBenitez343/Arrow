"""Unit tests for AI/model_downloads.py — the download tracker state machine.
"""

import os

import pytest

from AI.model_downloads import (
    DownloadStatus,
    ModelState,
    ModelDownloadTracker,
    _resolve_models_dir,
)


@pytest.fixture
def tracker():
    t = ModelDownloadTracker()
    t._models = {}
    return t


# ---------------------------------------------------------------------------
# Enums / dataclass
# ---------------------------------------------------------------------------


def test_download_status_values():
    assert DownloadStatus.PENDING.value == "pending"
    assert DownloadStatus.COMPLETE.value == "complete"
    assert DownloadStatus.ERROR.value == "error"


def test_model_state_defaults():
    state = ModelState(model_id="m", display_name="M", save_path="p", url="u")
    assert state.status == DownloadStatus.PENDING
    assert state.total_bytes == 0
    assert state.error_message == ""


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def test_resolve_models_dir_source(monkeypatch):
    import sys as _sys
    monkeypatch.setattr(_sys, "frozen", False, raising=False)
    from AI import model_downloads as md
    expected = os.path.join(os.path.dirname(os.path.abspath(md.__file__)), "models")
    assert md._resolve_models_dir() == expected


# ---------------------------------------------------------------------------
# Tracker state machine
# ---------------------------------------------------------------------------


def test_register_base_models(tracker):
    tracker.register_base_models()
    assert tracker.get_state("smollm3") is not None
    assert tracker.get_state("ghost") is None


def test_register_base_models_idempotent(tracker):
    tracker.register_base_models()
    count = len(tracker._models)
    tracker.register_base_models()
    assert len(tracker._models) == count


def test_update_progress_transitions_pending(tracker):
    tracker.register_base_models()
    tracker.update_progress("smollm3", 500, 1000)
    state = tracker.get_state("smollm3")
    assert state.status == DownloadStatus.DOWNLOADING
    assert state.downloaded_bytes == 500


def test_update_progress_unknown_model_noop(tracker):
    tracker.update_progress("ghost", 1, 2)  # no crash


def test_mark_complete(tracker):
    tracker.register_base_models()
    tracker.update_progress("smollm3", 100, 100)
    tracker.mark_complete("smollm3")
    state = tracker.get_state("smollm3")
    assert state.status == DownloadStatus.COMPLETE
    assert state.downloaded_bytes == state.total_bytes


def test_mark_error(tracker):
    tracker.register_base_models()
    tracker.mark_error("smollm3", "network down")
    state = tracker.get_state("smollm3")
    assert state.status == DownloadStatus.ERROR
    assert state.error_message == "network down"


def test_get_state_returns_copy(tracker):
    tracker.register_base_models()
    s1 = tracker.get_state("smollm3")
    s1.status = DownloadStatus.COMPLETE
    assert tracker.get_state("smollm3").status == DownloadStatus.PENDING


def test_get_all_states(tracker):
    tracker.register_base_models()
    states = tracker.get_all_states()
    assert len(states) == len(tracker._models)
    assert all(isinstance(s, ModelState) for s in states)


# ---------------------------------------------------------------------------
# get_summary
# ---------------------------------------------------------------------------


def test_summary_empty_tracker(tracker):
    assert tracker.get_summary() == (False, "", 0.0, True, False)


def test_summary_all_complete(tracker):
    tracker.register_base_models()
    for mid in list(tracker._models):
        tracker.mark_complete(mid)
    downloading, name, pct, all_done, has_error = tracker.get_summary()
    assert downloading is False
    assert all_done is True
    assert has_error is False


def test_summary_downloading(tracker):
    tracker.register_base_models()
    tracker.update_progress("smollm3", 250, 1000)
    downloading, name, pct, all_done, has_error = tracker.get_summary()
    assert downloading is True
    assert pct == pytest.approx(25.0)
    assert all_done is False


def test_summary_error_flag(tracker):
    tracker.register_base_models()
    tracker.mark_error("smollm3", "failed")
    _d, _n, _p, _a, has_error = tracker.get_summary()
    assert has_error is True


# ---------------------------------------------------------------------------
# get_missing_models
# ---------------------------------------------------------------------------


def test_missing_models_detects_absent_files(tracker, tmp_path):
    tracker._models["m1"] = ModelState(
        model_id="m1", display_name="M1",
        save_path=str(tmp_path / "missing.gguf"), url="u")
    missing = tracker.get_missing_models()
    assert missing == [("m1", "M1", str(tmp_path / "missing.gguf"))]


def test_missing_models_marks_existing_complete(tracker, tmp_path):
    model_file = tmp_path / "present.gguf"
    model_file.write_bytes(b"GGUF")
    tracker._models["m1"] = ModelState(
        model_id="m1", display_name="M1", save_path=str(model_file), url="u")
    missing = tracker.get_missing_models()
    assert missing == []
    assert tracker.get_state("m1").status == DownloadStatus.COMPLETE


def test_missing_skips_complete_models(tracker):
    tracker._models["m1"] = ModelState(
        model_id="m1", display_name="M1", save_path="x",
        url="u", status=DownloadStatus.COMPLETE)
    assert tracker.get_missing_models() == []


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


def test_singleton_get_instance():
    ModelDownloadTracker._instance = None
    assert ModelDownloadTracker.get_instance() is ModelDownloadTracker.get_instance()
