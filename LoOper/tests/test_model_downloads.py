"""Unit tests for AI/model_downloads.py — the download tracker state machine.
"""

import os
import sys
import types
import zipfile
import io

import pytest

from AI.model_downloads import (
    DownloadStatus,
    ModelState,
    ModelDownloadTracker,
    _resolve_models_dir,
    _download_asset,
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


# ---------------------------------------------------------------------------
# Full asset manifest / provisioner
# ---------------------------------------------------------------------------


def test_manifest_covers_models_and_utilities():
    from AI.model_downloads import BASE_ASSETS, MODELS, UTILITIES
    ids = {a["model_id"] for a in BASE_ASSETS}
    for required in (
        "smollm3", "lfm_vision", "lfm_mmproj",
        "locateanything", "locateanything_mmproj",
        "embeddinggemma", "laya_ud_q4_k_m", "laya_q8_0",
        "laya_exe", "llama_engine", "chrome", "chromedriver",
        "vosk_stt", "paddle_det", "paddle_rec", "paddle_cls",
    ):
        assert required in ids, required
    # Every asset declares a destination root.
    for a in BASE_ASSETS:
        assert a.get("root") in ("models", "bin", "data", "paddle")


def test_register_resolves_each_root(tracker, monkeypatch, tmp_path):
    from AI import model_downloads as md
    monkeypatch.setattr(md, "_resolve_models_dir", lambda: str(tmp_path / "m"))
    monkeypatch.setattr(md, "_resolve_bin_dir", lambda: str(tmp_path / "b"))
    monkeypatch.setattr(md, "_resolve_data_dir", lambda: str(tmp_path / "d"))
    monkeypatch.setattr(md, "_resolve_paddle_dir", lambda: str(tmp_path / "p"))
    tracker.register_base_models()
    assert tracker.get_state("smollm3").save_path == os.path.join(
        str(tmp_path / "m"), "SmolLM3-Q4_K_M.gguf")
    assert tracker.get_state("laya_exe").save_path == os.path.join(
        str(tmp_path / "b"), "laya.exe")
    assert tracker.get_state("chrome").kind == "zip"
    assert tracker.get_state("chrome").extract_to == os.path.join(
        str(tmp_path / "b"), "chrome")
    assert tracker.get_state("vosk_stt").save_path == os.path.join(
        str(tmp_path / "d"), "vosk_models", "vosk-model-small-en-us-0.15")
    # PaddleOCR models are frozen-only (source uses ~/.paddleocr/whl).
    assert tracker.get_state("paddle_det") is None


def test_missing_marks_existing_dir_complete(tracker, tmp_path):
    d = tmp_path / "modeldir"
    d.mkdir()
    tracker._models["m1"] = ModelState(
        model_id="m1", display_name="M1", save_path=str(d), url="u")
    assert tracker.get_missing_models() == []
    assert tracker.get_state("m1").status == DownloadStatus.COMPLETE


def test_download_asset_extracts_zip_member(tmp_path, monkeypatch):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("payload/nested/laya.exe", b"binary")
    data = buf.getvalue()

    class _Resp:
        headers = {"content-length": str(len(data))}

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size=0):
            yield data

    monkeypatch.setitem(sys.modules, "requests",
                        types.SimpleNamespace(get=lambda *a, **k: _Resp()))

    st = ModelState(
        model_id="laya_exe", display_name="L", url="http://x/laya.zip",
        save_path=str(tmp_path / "laya.exe"), root="bin", kind="zip",
        extract_member="laya.exe")
    _download_asset(st, lambda done, total: None)
    assert (tmp_path / "laya.exe").read_bytes() == b"binary"
