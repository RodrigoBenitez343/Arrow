"""Tests for the orange EXECUTION overlay (desktop PyQt + in-page web).

Load-bearing behaviours pinned here:
* the desktop overlay's shared feed (player.execution_overlay_bus) round-trips
  action / trail / clicks and resets between runs;
* the desktop overlay converts PHYSICAL UIA/pyautogui points into the LOGICAL
  widget space (1/dpr) and refreshes its banner from the feed each tick;
* the desktop overlay self-terminates from the shared stop_event;
* the web overlay payload exposes the hooks the replay engine calls, is gated
  on its own sessionStorage key, and the mark script reuses the shadow-piercing
  deep search.
"""
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from NGUI.widgets.execution_overlay import ExecutionOverlay  # noqa: E402
from player.execution_overlay_bus import bus  # noqa: E402
from player.web.exec_overlay import (  # noqa: E402
    JS_EXEC_MARK,
    _STORAGE_KEY,
    _payload,
)


# --------------------------------------------------------------- desktop feed

def test_bus_round_trips_action_trail_clicks_and_target():
    bus.reset()
    bus.set_action("3: click")
    bus.set_target(11, 22)
    bus.add_trail(10, 20)
    bus.add_click(11, 22)
    snap = bus.snapshot()
    assert snap["action"] == "3: click"
    assert tuple(snap["target"][:2]) == (11, 22)
    assert (10, 20) == tuple(snap["trail"][0][:2])
    assert (11, 22) == tuple(snap["clicks"][0][:2])
    # a click also extends the trail
    assert tuple(snap["trail"][-1][:2]) == (11, 22)
    bus.reset()
    assert bus.snapshot() == {"action": "", "target": None, "trail": [], "clicks": []}


def _overlay():
    ov = ExecutionOverlay(threading.Event())
    ov.setGeometry(0, 0, 1920, 1080)
    return ov


def test_desktop_overlay_scales_physical_points_into_logical_space():
    """pyautogui/UIA report physical pixels; the widget is laid out in logical
    ones, so marks must be divided by devicePixelRatio (2.0 here)."""
    ov = _overlay()
    ov._dpr = 2.0
    assert ov._local(200, 400) == (100, 200)


def test_desktop_overlay_banner_tracks_the_feed():
    ov = _overlay()
    bus.reset()
    bus.set_action("7: type_string")
    ov._tick()
    assert "7: type_string" in ov._banner.text()
    bus.reset()


def test_desktop_overlay_paints_trail_clicks_and_element_box():
    """Exercise the QPainter path for real - a bad draw call (wrong overload)
    would raise here rather than at runtime."""
    ov = _overlay()
    now = time.time()
    ov._snap = {
        "action": "2: click",
        "trail": [(10, 10, now), (200, 120, now), (400, 300, now)],
        "clicks": [(400, 300, now)],
    }
    ov._hit = {"rect": (380, 280, 460, 340), "label": 'button  "Go"',
               "ts": now}
    ov.show()
    pixmap = ov.grab()          # triggers paintEvent
    assert not pixmap.isNull()
    ov._kill.set()


def test_hit_loop_releases_the_lock_before_uia():
    """Regression: the worker must NOT hold _hit_lock across the UIA hit-test.

    UIA can block - over our own full-desktop overlay window it waits on this
    process's UI thread - so calling element_at INSIDE the lock deadlocked
    paintEvent (same lock): the app froze the instant a chain started.  Here the
    fake hit-test asserts the lock is free when it runs.  The loop is driven by
    the feed's target point, never by the live cursor."""
    import NGUI.widgets.execution_overlay as eo

    ov = _overlay()
    free_during = {"ok": True}

    def fake_element_at(x, y):
        got = ov._hit_lock.acquire(blocking=False)
        if got:
            ov._hit_lock.release()
        else:
            free_during["ok"] = False
        ov._stop.set()          # exit the worker loop after this iteration
        return None

    bus.reset()
    bus.set_target(5, 5)
    original_element_at = eo.element_at
    eo.element_at = fake_element_at
    try:
        ov._hit_loop()          # runs one iteration, then stops
    finally:
        eo.element_at = original_element_at
        bus.reset()
    assert free_during["ok"], "UIA ran while _hit_lock was held"


def test_capture_hook_takes_the_overlay_off_screen():
    """The replay must never capture its own decoration: the overlay registers a
    blocking hook and hides when the feed asks for a screen read, then stays off
    briefly so a burst of reads does not blink it back on."""
    ov = _overlay()
    bus.reset()
    assert bus.capture_hook == ov.hide_for_capture
    ov.show()
    assert not ov.isHidden()

    bus.hide_overlay_for_capture()          # same thread here -> hides directly
    assert ov.isHidden()
    assert ov._hidden_until > time.time()   # stays off for the read window

    ov.stop()
    assert bus.capture_hook is None         # never called on a dead overlay


def test_content_gate_only_occupies_the_screen_while_something_is_drawn():
    """The full-screen window must exist ONLY while there is something to show,
    so a web-only stretch (or a web-only chain) never gets a flickering window."""
    ov = _overlay()
    now = time.time()
    ov._hit = None
    ov._snap = {"action": "", "target": None,
                "trail": [(1, 1, now)], "clicks": []}
    assert ov._has_content(now)                       # fresh trail
    ov._snap["trail"] = [(1, 1, now - 99)]
    assert not ov._has_content(now)                    # aged out
    ov._snap["trail"] = []
    ov._snap["clicks"] = [(2, 2, now)]
    assert ov._has_content(now)                       # fresh click ring
    ov._snap["clicks"] = []
    ov._hit = {"rect": (0, 0, 10, 10), "label": "", "ts": now}
    assert ov._has_content(now)                       # fresh target box
    ov._hit["ts"] = now - 99
    assert not ov._has_content(now)


def test_desktop_overlay_self_stops_on_the_stop_event():
    ov = _overlay()
    ov.show()
    assert not ov.isHidden()
    ov._stop.set()
    ov._tick()
    assert ov.isHidden()
    ov._kill.set()


# ---------------------------------------------------------------- web overlay

def test_start_playback_delayed_does_not_shadow_threading():
    """Regression: `start_playback_delayed` creates the overlay's stop Event with
    the module-level `threading` BEFORE a nested `import threading` sits further
    down.  A nested import makes `threading` LOCAL to the whole function, so that
    line raised UnboundLocalError and PyQt aborted the app the instant a chain
    started (the log stopped at 'Starting direct chain execution' and left
    orphaned subprocesses behind)."""
    import ast
    import pathlib

    src = pathlib.Path(__file__).resolve().parents[1] / "NGUI" / "main_window.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    fn = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef) and n.name == "start_playback_delayed"),
        None,
    )
    assert fn is not None, "start_playback_delayed moved/renamed - update this test"
    shadowing = [
        n for n in ast.walk(fn)
        if isinstance(n, ast.Import) and any(a.name == "threading" for a in n.names)
    ]
    assert not shadowing, (
        "nested `import threading` shadows the module global, so the overlay's "
        "threading.Event() raises UnboundLocalError and aborts the app"
    )


def test_web_overlay_payload_exposes_the_engine_hooks():
    js = _payload()
    assert js, "exec_overlay.js must be present next to the helper"
    for hook in ("window.__wvpExecMark", "window.__wvpExecOn", "window.__wvpExecOff"):
        assert hook in js
    assert _STORAGE_KEY in js
    assert "255,140,0" in js  # orange


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_web_overlay_payload_is_idempotent_per_document():
    """enable() re-evaluates the payload in the CURRENT document on every pass -
    engine.run() calls it once per replay pass - and re-registers it via CDP.
    A second evaluation must therefore append NOTHING: stacked canvases/banners
    at the same fixed position are what made the playback overlay look like it
    never cleared the previous frame."""
    harness = Path(__file__).parent / "js" / "exec_overlay_idempotent_harness.js"
    proc = subprocess.run(
        ["node", str(harness)], capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    assert data["afterFirst"] == 2     # one canvas + one banner
    assert data["afterSecond"] == 2    # a re-evaluation appended nothing
    assert data["apiOk"] is True and data["onOk"] is True


def test_web_mark_script_reuses_the_deep_search():
    assert "__wvpDeepFindAny" in JS_EXEC_MARK       # shadow-piercing resolve
    assert "__wvpExecMark" in JS_EXEC_MARK          # hands the rect to the overlay
    assert "getBoundingClientRect" in JS_EXEC_MARK


def test_overlay_banner_puts_the_action_beside_the_tag():
    """The action rides BESIDE the EXECUTING tag on ONE line - stacking it
    underneath made the current question read as detached from the tag."""
    js = _payload()
    assert "EXECUTING" in js
    assert "'EXECUTING\\n'" not in js          # no longer stacked
    assert "white-space:nowrap" in js          # a single line
    # The desktop twin matches: action beside the tag, on one line.
    ov = _overlay()
    bus.reset()
    bus.set_action("form: Desired salary")
    ov._tick()
    text = ov._banner.text()
    assert text.startswith("EXECUTING  \u00b7  ")
    assert "form: Desired salary" in text
    bus.reset()
