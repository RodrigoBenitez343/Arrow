"""OCR worker subprocess resolution (source + frozen).

The isolated paddle OCR worker runs as a subprocess started from
``ocr_worker.py``.  In a compiled build that script must be a loose data file
(see LoOperApp.spec) and the interpreter must be the app exe (whose
interpreter mode execs the .py) - otherwise OCR silently returns empty.
"""
import os
import sys

from player import ocr_engine


def test_worker_script_resolves_in_source():
    script = ocr_engine._ocr_worker_script()
    assert script and os.path.isfile(script)
    assert os.path.basename(script) == "ocr_worker.py"


def test_worker_python_exists_in_source():
    py = ocr_engine._ocr_worker_python()
    assert py and os.path.isfile(py)


def test_frozen_uses_the_app_exe(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert ocr_engine._ocr_worker_python() == sys.executable


def test_missing_worker_returns_empty(monkeypatch):
    monkeypatch.setattr(ocr_engine, "_ocr_worker_script", lambda: None)
    assert ocr_engine._ocr_via_subprocess(object(), 0.5, "en") == []
