"""Tests for the desktop recording overlay.

Load-bearing behaviours pinned here: the overlay must self-terminate from the
shared ``stop_event`` (it cannot rely on ``_restore_window``, whose
``QTimer.singleShot(0, lambda)`` never fires from the recorder's worker thread),
it must hide every mark while a button is held (so the 35x35 replay-template
crop is never tinted), and the cheat sheet must list the real modifier
gestures.
"""
import os
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from NGUI.widgets.recording_overlay import (  # noqa: E402
    RecordingOverlay,
    element_at,
    _CHEAT_TEXT,
)


def _overlay():
    """Overlay with a fixed geometry; the hit-test thread is not started."""
    ov = RecordingOverlay(threading.Event(), threading.Event())
    ov.setGeometry(0, 0, 1920, 1080)
    return ov


def test_cheat_sheet_lists_the_modifier_gestures():
    for hint in ("RECORDING", "Right Ctrl", "visual match", "relative click",
                 "relative drag", "absolute click", "ctrl_click", "shift_click",
                 "ESC"):
        assert hint in _CHEAT_TEXT


def test_paint_outlines_the_published_element():
    ov = _overlay()
    ov._hit = {"rect": (100, 200, 300, 260), "label": 'Button  "OK"'}
    ov._paint()
    assert not ov._box.isHidden()
    assert (ov._box.x(), ov._box.y(), ov._box.width(), ov._box.height()) == (100, 200, 200, 60)
    assert not ov._label.isHidden()
    assert ov._label.text() == 'Button  "OK"'


def test_paint_scales_physical_rects_into_the_logical_widget_space():
    """UIA reports physical pixels; the widget is laid out in logical ones, so
    marks must be divided by devicePixelRatio (2.0 here) before drawing."""
    ov = _overlay()
    ov._dpr = 2.0
    ov._hit = {"rect": (200, 400, 600, 520), "label": ""}
    ov._paint()
    assert (ov._box.x(), ov._box.y(), ov._box.width(), ov._box.height()) == (100, 200, 200, 60)


def test_paint_hides_every_mark_while_a_button_is_held():
    ov = _overlay()
    ov._hit = {"rect": (100, 200, 300, 260), "label": 'Button  "OK"',
               "element": True, "x": 200, "y": 230}
    ov._paint()
    assert not ov._box.isHidden() and not ov._crop.isHidden()

    ov._mouse_down.set()
    ov._paint()
    assert (ov._box.isHidden() and ov._label.isHidden()
            and ov._crop.isHidden() and ov._crop_label.isHidden()
            and ov._cheat.isHidden())


def test_paint_draws_the_template_crop_region_under_the_cursor():
    """The dashed box IS the region the click's replay template crops - the
    legacy square when no element is under the cursor."""
    ov = _overlay()
    ov._hit = {"rect": (100, 200, 300, 260), "label": '',
               "element": False, "x": 500, "y": 400}
    ov._paint()
    assert not ov._crop.isHidden()
    assert (ov._crop.x(), ov._crop.y(), ov._crop.width(), ov._crop.height()) == \
        (500 - 17, 400 - 17, 35, 35)
    assert ov._crop_label.isHidden()


def test_crop_preview_follows_the_element_box_when_element_matched():
    ov = _overlay()
    ov._hit = {"rect": (440, 380, 560, 420), "label": '',
               "element": True, "x": 500, "y": 400}
    ov._paint()
    assert (ov._crop.x(), ov._crop.y(), ov._crop.width(), ov._crop.height()) == \
        (440, 380, 120, 40)


def test_paint_captions_the_crop_while_the_visual_marker_is_held():
    """Holding right Ctrl switches the preview to the legacy patch - exactly
    what the recorded visual click will search for at replay."""
    ov = _overlay()
    ov._hit = {"rect": (440, 380, 560, 420), "label": '',
               "element": True, "x": 500, "y": 400}
    ov.set_visual_provider(lambda: True)
    ov._paint()
    # The element bbox is ignored for a visual click: the 35px patch wins.
    assert (ov._crop.x(), ov._crop.y(), ov._crop.width(), ov._crop.height()) == \
        (500 - 17, 400 - 17, 35, 35)
    assert not ov._crop_label.isHidden()
    assert ov._crop_label.text() == "visual match 35x35"


def test_paint_stops_the_overlay_when_the_stop_event_is_set():
    ov = _overlay()
    ov.show()
    assert not ov.isHidden()

    ov._stop.set()
    ov._paint()
    assert ov.isHidden()


def test_element_at_returns_a_rect_or_none():
    """Smoke test: UIA is optional, but when it answers it must be a real box."""
    hit = element_at(10, 10)
    if hit is None:
        return  # UIA unavailable on this machine - reticle fallback covers it
    l, t, r, b = hit["rect"]
    assert r > l and b > t
    assert isinstance(hit["label"], str)
