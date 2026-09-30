"""OCR conditional search-box (picked element) round-trip.

Pins the path added for element-targeted OCR: the dialog returns the picked
box, reloading a saved trigger restores it, and the runtime coerces the stored
form (JSON string on the node property, or a list in the chain config) into the
``(x, y, w, h)`` tuple the OCR crop expects.  If any link breaks, an
"element" OCR conditional silently falls back to scanning the whole screen.
"""
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from NGUI.dialogs.trigger_dialogs import OCRTriggerConfigDialog  # noqa: E402
from NGUI.graph_elements.node_operations_modules.conditional import _parse_region  # noqa: E402
from player.multi_sequence.conditional_fallback_resources.ocr_trigger import OCRTrigger  # noqa: E402


def test_dialog_returns_and_reloads_the_picked_region():
    dlg = OCRTriggerConfigDialog(None)
    assert "region" not in dlg.get_config()  # whole screen by default

    dlg.area_combo.setCurrentIndex(1)
    dlg._region = [100, 200, 300, 40]
    assert dlg.get_config()["region"] == [100, 200, 300, 40]

    dlg2 = OCRTriggerConfigDialog(None, config={"target_text": "ok", "region": [10, 20, 30, 40]})
    assert dlg2.area_combo.currentIndex() == 1
    assert dlg2.get_config()["region"] == [10, 20, 30, 40]
    assert "10" in dlg2.region_label.text()


def test_region_round_trips_from_node_json_into_the_runtime():
    # The GUI stores a JSON string on the node property (conditional.py apply).
    node_value = json.dumps([10, 20, 30, 40])
    assert _parse_region(node_value) == [10, 20, 30, 40]

    # The runtime coerces the stored form into the crop tuple.
    coerce = OCRTrigger._coerce_region
    assert coerce(node_value) == (10, 20, 30, 40)
    assert coerce([10, 20, 30, 40]) == (10, 20, 30, 40)
    assert coerce((10, 20, 30, 40)) == (10, 20, 30, 40)
    assert coerce("") is None
    assert coerce(None) is None
    assert coerce("junk") is None
    assert coerce([1, 2, 3]) is None


def test_picked_rect_becomes_an_xywh_region():
    dlg = OCRTriggerConfigDialog(None)
    dlg._on_element_picked((100, 200, 340, 240))
    assert dlg._region == [100, 200, 240, 40]
    # A cancel (None) leaves the previous/whole-screen state untouched.
    dlg._on_element_picked(None)
    assert dlg._region == [100, 200, 240, 40]


def test_element_pick_overlay_emits_rect_on_click_and_none_on_cancel():
    from PyQt5.QtCore import QEvent, QPointF, Qt
    from PyQt5.QtGui import QKeyEvent, QMouseEvent

    from NGUI.dialogs.trigger_dialogs import ElementPickOverlay

    got = []
    ov = ElementPickOverlay()
    # The click captures the worker's latest hover hit (UIA never runs on the
    # GUI thread), so publish one the way the worker would.
    ov._hit = {"rect": (10, 20, 110, 80), "label": "b"}
    ov.picked.connect(got.append)
    press = QMouseEvent(
        QEvent.MouseButtonPress, QPointF(5, 5), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier
    )
    ov.mousePressEvent(press)
    assert got == [(10, 20, 110, 80)]

    got2 = []
    ov2 = ElementPickOverlay()
    ov2.picked.connect(got2.append)
    ov2.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    assert got2 == [None]


def test_pick_click_never_runs_uia_on_the_gui_thread():
    """The pick click must not call UI Automation inline: a slow or hung UIA
    provider on the GUI thread freezes the whole picker, so the click uses the
    worker's latest hover hit instead."""
    from PyQt5.QtCore import QEvent, QPointF, Qt
    from PyQt5.QtGui import QMouseEvent

    from NGUI.dialogs.trigger_dialogs import ElementPickOverlay

    ov = ElementPickOverlay()

    def _boom(x, y):
        raise AssertionError("UI Automation must not run on the GUI thread")

    ov._element_under_cursor = _boom
    ov._hit = {"rect": (1, 2, 31, 42), "label": ""}
    got = []
    ov.picked.connect(got.append)
    ov.mousePressEvent(
        QMouseEvent(QEvent.MouseButtonPress, QPointF(5, 5),
                    Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
    )
    assert got == [(1, 2, 31, 42)]


def test_overlay_paints_a_hit_testable_base_over_the_spotlight():
    """The spotlighted element area must not be fully transparent: a layered
    (translucent) window is click-through on alpha-0 pixels, so a transparent
    spotlight would swallow the very click that should pick the element."""
    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QPixmap

    from NGUI.dialogs.trigger_dialogs import ElementPickOverlay

    ov = ElementPickOverlay()
    ov.resize(200, 200)
    ov._hit = {"rect": (20, 20, 120, 100), "label": ""}
    pm = QPixmap(200, 200)
    pm.fill(Qt.transparent)
    ov.render(pm)
    img = pm.toImage()
    assert img.pixelColor(70, 60).alpha() > 0  # inside the element box
    assert img.pixelColor(5, 5).alpha() > 0    # dimmed background
