"""Desktop recording overlay: marks the real element under the cursor.

The desktop twin of the web recorder's in-page overlay
(``player/web/inject/recorder.js``) and the web picker's highlight
(``player/web/inject/picker.js``): while a desktop sequence records, a
frameless, click-through, always-on-top window outlines the element under the
cursor and pins a "RECORDING" cheat sheet of the modifier gestures to a corner.

Element marking is Windows UI Automation: ``IUIAutomation::ElementFromPoint``
returns the element under a point and ``CurrentBoundingRectangle`` its boundary
- the desktop equivalent of the browser's ``elementFromPoint`` +
``getBoundingClientRect``.  It covers Win32 / WinForms / WPF / WinUI / Electron
/ Chrome (its accessibility tree) / UWP; custom-drawn canvases expose nothing
and fall back to a cursor reticle.  Driven through ``comtypes`` (already
installed) - no new dependency.

Two hard constraints, both about the recorder's 35x35 click crop that becomes
the REPLAY template (``player/action_handlers.py`` handle_click_action):

* while ANY mouse button is down the overlay hides every mark, so the crop is
  never tinted by the outline/label/cheat sheet (a window X button sits exactly
  under the top-right cheat sheet);
* elements are outlined with a border and NO fill, so nothing is painted inside
  the element even in the tick before the hide lands.

Coordinates: UIA/pyautogui report PHYSICAL pixels, but the app enables Qt
high-DPI scaling (``NGUI/app.py`` ``AA_EnableHighDpiScaling``), so widgets are
laid out in LOGICAL pixels.  Marks are scaled by ``1 / devicePixelRatio`` before
drawing.

The overlay is torn down by a ``stop_event`` the recorder sets, NOT by
``_restore_window``: that is scheduled via ``QTimer.singleShot(0, lambda)`` from
the recorder's worker thread, and PyQt creates that timer in the (event-loop
less) worker thread, so it never fires.

ponytail: the hit-test runs on a daemon thread so a slow/hung UIA provider on
one app can never freeze the GUI; swap to an in-timer call if that ever shows
up as overhead.
"""
from __future__ import annotations

import logging
import threading
import time

from PyQt5.QtCore import QRect, Qt, QTimer
from PyQt5.QtWidgets import QApplication, QFrame, QLabel, QWidget

logger = logging.getLogger(__name__)

# Fallback reticle around the cursor when UIA finds no element.  Border-only, so
# it is also safe to draw right up to the click crop.
_RETICLE = 48
_MARGIN = 12
_DRAW_MS = 16          # GUI redraw + button-watch cadence
_HIT_MIN_MOVE = 3      # px the cursor must move before a new UIA hit-test
_HIT_IDLE_S = 0.03     # worker sleep between polls
_ANCESTOR_LIMIT = 12   # control-view ancestors captured per element

_BOX_QSS = "background: transparent; border: 2px solid #00e0b8;"
_LABEL_QSS = (
    "background: #00e0b8; color: #00201a;"
    "padding: 1px 6px; border-radius: 3px; font: 11px monospace;"
)
# Template-crop preview: the exact region the click's replay template will be
# cropped from.  Dashed + amber so it never reads as the element outline.
_CROP_QSS = "background: transparent; border: 1px dashed #ffb020;"
_CROP_LABEL_QSS = (
    "background: #ffb020; color: #201400;"
    "padding: 1px 6px; border-radius: 3px; font: 10px monospace;"
)
_CHEAT_QSS = (
    "background: rgba(0,32,26,0.82); color: #00e0b8;"
    "border: 1px solid rgba(0,224,184,0.55); border-radius: 6px;"
    "padding: 8px 12px; font: 11px monospace;"
)
# The desktop recorder's real gestures (recorder/element_recorder.py):
_CHEAT_TEXT = (
    "RECORDING\n"
    "Drag > 10px         drag & drop\n"
    "Right Ctrl + move   move (hover)\n"
    "Right Ctrl + click  visual match\n"
    "Alt + click         relative click\n"
    "Alt + drag          relative drag\n"
    "Shift(R) + click    absolute click\n"
    "Shift(R) + drag     absolute drag\n"
    "Ctrl + click        ctrl_click\n"
    "Shift(L) + click    shift_click\n"
    "Double-click        double click\n"
    "Scroll              scroll (grouped)\n"
    "Insert + 2 marks    repeat each sibling\n"
    "ESC                 stop & save"
)

# UIA ControlType ids -> readable names.
_CONTROL_TYPES = {
    50000: "Button", 50001: "Calendar", 50002: "CheckBox", 50003: "ComboBox",
    50004: "Edit", 50005: "Hyperlink", 50006: "Image", 50007: "ListItem",
    50008: "List", 50009: "Menu", 50010: "MenuBar", 50011: "MenuItem",
    50012: "ProgressBar", 50013: "RadioButton", 50014: "ScrollBar",
    50015: "Slider", 50016: "Spinner", 50017: "StatusBar", 50018: "Tab",
    50019: "TabItem", 50020: "Text", 50021: "ToolBar", 50022: "ToolTip",
    50023: "Tree", 50024: "TreeItem", 50025: "Custom", 50026: "Group",
    50027: "Thumb", 50028: "DataGrid", 50029: "DataItem", 50030: "Document",
    50031: "SplitButton", 50032: "Window", 50033: "Pane",
}

# COM objects are apartment-bound, so the IUIAutomation instance is per-thread.
_uia_local = threading.local()


def _get_uia():
    """Per-thread ``IUIAutomation`` instance, or None when unavailable."""
    if getattr(_uia_local, "tried", False):
        return getattr(_uia_local, "uia", None)
    _uia_local.tried = True
    _uia_local.uia = None
    try:
        import comtypes
        import comtypes.client

        comtypes.CoInitialize()
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient

        _uia_local.uia = comtypes.client.CreateObject(
            UIAutomationClient.CUIAutomation
        )
    except Exception as exc:
        logger.debug("UIA unavailable (%s) - using a cursor reticle", exc)
    return _uia_local.uia


def _safe(el, name, default=""):
    """Read a UIA ``Current*`` property, tolerating a provider that throws."""
    try:
        value = getattr(el, name)
    except Exception:
        return default
    return default if value is None else value


def _runtime_id(el):
    """UIA RuntimeId as a list of ints, or [].

    Session-unique and comparable, used only at RECORD time to find the shared
    container of two marked siblings (never persisted usefully).  Read via
    ``GetRuntimeId()``: the comtypes wrapper exposes the runtime id as a METHOD,
    so the ``CurrentRuntimeId`` property form raises AttributeError and yields
    nothing - which silently disabled sibling pairing.
    """
    try:
        value = el.GetRuntimeId()
    except Exception:
        try:
            value = el.CurrentRuntimeId
        except Exception:
            return []
    try:
        return [int(v) for v in value]
    except Exception:
        return []


def _rect_of(el):
    """Element bbox (l, t, r, b) in screen pixels, or None."""
    try:
        r = el.CurrentBoundingRectangle
        return (int(r.left), int(r.top), int(r.right), int(r.bottom))
    except Exception:
        return None


def _ancestor_chain(uia, el, limit=_ANCESTOR_LIMIT):
    """Control-view ancestors of ``el``, nearest first, stopping at the window.

    This is the desktop stand-in for a CSS selector's structural path: it lets
    replay re-scope a find to the element's containing window even when the
    element itself carries no stable AutomationId.
    """
    out = []
    try:
        walker = uia.ControlViewWalker
    except Exception:
        try:
            walker = uia.RawViewWalker
        except Exception:
            return out
    node = el
    for _ in range(limit):
        try:
            node = walker.GetParentElement(node)
        except Exception:
            break
        if node is None:
            break
        try:
            ctype = int(node.CurrentControlType)
        except Exception:
            break
        if not ctype:
            break
        out.append({
            "control_type": ctype,
            "name": str(_safe(node, "CurrentName"))[:60],
            "automation_id": str(_safe(node, "CurrentAutomationId"))[:60],
            "class_name": str(_safe(node, "CurrentClassName"))[:120],
            "framework_id": str(_safe(node, "CurrentFrameworkId"))[:40],
            "runtime_id": _runtime_id(node),
            "rect": _rect_of(node),
        })
        if ctype == 50032:  # Window - the top-level anchor, stop here
            break
    return out


def element_descriptor_at(x, y):
    """Full UIA identity of the element at a point, or None.

    Returns ``{'rect', 'label', 'name', 'automation_id', 'control_type',
    'class_name', 'framework_id', 'process_id', 'ancestors'}`` - everything
    replay needs to re-find the element WITHOUT the image crop.  None when UIA
    is unavailable or nothing usable is under the point (custom-drawn canvas).
    """
    uia = _get_uia()
    if uia is None:
        return None
    try:
        from ctypes import wintypes

        el = uia.ElementFromPoint(wintypes.POINT(int(x), int(y)))
        r = el.CurrentBoundingRectangle
        left, top, right, bottom = int(r.left), int(r.top), int(r.right), int(r.bottom)
        if right - left < 2 or bottom - top < 2:
            return None
        name = str(_safe(el, "CurrentName")).strip()
        try:
            ctype = int(el.CurrentControlType)
        except Exception:
            ctype = 0
        try:
            pid = int(el.CurrentProcessId)
        except Exception:
            pid = 0
        label = _CONTROL_TYPES.get(ctype, f"control_{ctype}")
        if name:
            label += f'  "{name[:40]}"'
        return {
            "rect": (left, top, right, bottom),
            "label": label,
            "name": name[:120],
            "automation_id": str(_safe(el, "CurrentAutomationId"))[:120],
            "control_type": ctype,
            "class_name": str(_safe(el, "CurrentClassName"))[:120],
            "framework_id": str(_safe(el, "CurrentFrameworkId"))[:40],
            "process_id": pid,
            "runtime_id": _runtime_id(el),
            "ancestors": _ancestor_chain(uia, el),
        }
    except Exception as exc:
        logger.debug("UIA descriptor failed at (%s, %s): %s", x, y, exc)
        return None


def element_at(x, y):
    """Element under a screen point: ``{'rect': (l, t, r, b), 'label': str}``.

    Returns None when UIA is unavailable or nothing usable is under the point.
    """
    desc = element_descriptor_at(x, y)
    if desc is None:
        return None
    return {"rect": tuple(desc["rect"]), "label": desc.get("label", "")}


class RecordingOverlay(QWidget):
    """Click-through element marker + modifier cheat sheet for recording."""

    def __init__(self, stop_event: threading.Event,
                 mouse_down_event: threading.Event, parent=None):
        super().__init__(parent)
        self._stop = stop_event
        self._mouse_down = mouse_down_event
        self._kill = threading.Event()
        self._hit = None
        self._hit_lock = threading.Lock()
        self._dpr = 1.0

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowTransparentForInput
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)

        self._box = QFrame(self)
        self._box.setStyleSheet(_BOX_QSS)
        self._box.hide()

        self._label = QLabel(self)
        self._label.setStyleSheet(_LABEL_QSS)
        self._label.hide()

        # Template-crop preview (see _draw_crop): the region that becomes the
        # click's replay template, so the user sees what replay will search
        # for.  While the right-Ctrl visual-match marker is held it shrinks to
        # the legacy patch and gets a caption.
        self._crop = QFrame(self)
        self._crop.setStyleSheet(_CROP_QSS)
        self._crop.hide()

        self._crop_label = QLabel(self)
        self._crop_label.setStyleSheet(_CROP_LABEL_QSS)
        self._crop_label.hide()
        # Callable returning True while the visual-match marker is held; set
        # by the app once the recorder exists (overlay is created first).
        self._visual_provider = None

        self._cheat = QLabel(self)
        self._cheat.setStyleSheet(_CHEAT_QSS)
        self._cheat.setText(_CHEAT_TEXT)
        self._cheat.adjustSize()

        self._timer = QTimer(self)
        self._timer.setInterval(_DRAW_MS)
        self._timer.timeout.connect(self._paint)

        self._worker = threading.Thread(target=self._hit_loop, daemon=True)

    # ------------------------------------------------------------------ API

    def start(self) -> None:
        """Cover the virtual desktop and begin marking elements."""
        # UIA and pyautogui report PHYSICAL pixels, but the app enables Qt
        # high-DPI scaling (NGUI/app.py: AA_EnableHighDpiScaling), so widget
        # geometry is in LOGICAL pixels - physical / devicePixelRatio.  The
        # process is PROCESS_SYSTEM_DPI_AWARE, so every monitor shares one ratio.
        try:
            self._dpr = float(QApplication.primaryScreen().devicePixelRatio()) or 1.0
        except Exception:
            self._dpr = 1.0
        geo = self._virtual_geometry()
        self.setGeometry(geo)
        self._cheat.move(self.width() - self._cheat.width() - _MARGIN, _MARGIN)
        self._cheat.show()
        self.show()
        self.raise_()
        self._worker.start()
        self._timer.start()

    def set_visual_provider(self, provider) -> None:
        """Register a callable: True while the visual-match marker is held."""
        self._visual_provider = provider

    def stop(self) -> None:
        """Hide and stop both the draw timer and the hit-test thread."""
        self._kill.set()
        self._timer.stop()
        self.hide()

    # --------------------------------------------------------------- internal

    def _virtual_geometry(self) -> QRect:
        """Union of every screen, so marks follow across monitors."""
        geo = QRect()
        for screen in QApplication.screens():
            geo = geo.united(screen.geometry())
        if geo.isNull():
            primary = QApplication.primaryScreen()
            if primary is not None:
                return primary.geometry()
        return geo

    def _publish(self, res) -> None:
        with self._hit_lock:
            self._hit = res

    def element_rect(self):
        """Last UIA element bbox (l, t, r, b) in PHYSICAL pixels, or None.

        Read by the recorder to size a click's template to the element it was
        on.  None for the reticle fallback (no real element) or before the
        first hit-test lands.
        """
        with self._hit_lock:
            res = self._hit
        if not res or not res.get("element"):
            return None
        return res.get("rect")

    def _hit_loop(self) -> None:
        """Worker: poll the cursor, UIA hit-test, publish the mark to draw.

        Keeps running while a button is held: the recorder reads the element
        rect from here to size its crop, while the GUI simply does not DRAW
        while pressed (so the capture stays clean).
        """
        try:
            import pyautogui
        except Exception:
            return
        last = None
        while not (self._stop.is_set() or self._kill.is_set()):
            try:
                x, y = pyautogui.position()
            except Exception:
                time.sleep(0.1)
                continue
            x, y = int(x), int(y)
            if last is not None and abs(x - last[0]) + abs(y - last[1]) < _HIT_MIN_MOVE:
                time.sleep(_HIT_IDLE_S)
                continue
            last = (x, y)
            self._publish(self._mark_for(x, y))
            time.sleep(_HIT_IDLE_S)

    def _mark_for(self, x, y):
        """UIA element at (x, y), or a cursor reticle when there is none.

        The rect stays in PHYSICAL pixels here; ``_paint`` converts it to the
        logical space the widget is laid out in.
        """
        hit = element_descriptor_at(x, y)
        if hit is not None:
            hit["element"] = True
            hit["x"], hit["y"] = x, y
            return hit
        half = int(_RETICLE * self._dpr / 2)
        return {"rect": (x - half, y - half, x + half, y + half),
                "label": "", "element": False, "x": x, "y": y}

    def _paint(self) -> None:
        """GUI timer: draw the published mark, unless recording ended/pressed."""
        if self._stop.is_set():
            self.stop()
            return

        # While a button is held, hide EVERYTHING: the replay template is
        # captured on release, and any mark over it would ruin the match.
        if self._mouse_down.is_set():
            self._box.hide()
            self._label.hide()
            self._crop.hide()
            self._crop_label.hide()
            self._cheat.hide()
            return

        if self._cheat.isHidden():
            self._cheat.show()

        with self._hit_lock:
            res = self._hit
        if not res:
            self._box.hide()
            self._label.hide()
            self._crop.hide()
            self._crop_label.hide()
            return

        self._draw_crop(res)

        l, t, r, b = self._to_logical(res["rect"])
        # A full-desktop rect is the UIA root - not a meaningful mark.
        if l <= 0 and t <= 0 and r >= self.width() and b >= self.height():
            self._box.hide()
            self._label.hide()
            return
        if r - l < 2 or b - t < 2:
            self._box.hide()
            self._label.hide()
            return

        self._box.setGeometry(l - self.x(), t - self.y(), r - l, b - t)
        self._box.show()

        label = res.get("label") or ""
        if label:
            self._label.setText(label)
            self._label.adjustSize()
            ly = t - self.y() - self._label.height() - 2
            if ly < 0:
                ly = b - self.y() + 2
            self._label.move(max(0, l - self.x()), ly)
            self._label.show()
        else:
            self._label.hide()

    def _to_logical(self, rect):
        """Physical (UIA/pyautogui) rect -> the widget's logical coordinates."""
        dpr = self._dpr or 1.0
        l, t, r, b = rect
        return (int(round(l / dpr)), int(round(t / dpr)),
                int(round(r / dpr)), int(round(b / dpr)))

    def _visual_marker(self) -> bool:
        """True while the recorder's visual-match marker is held."""
        provider = self._visual_provider
        if provider is None:
            return False
        try:
            return bool(provider())
        except Exception:
            return False

    def _draw_crop(self, res) -> None:
        """Dashed preview of the region the click's replay template crops.

        ``recorder.screenshot_manager.click_crop_region`` is the single source
        of truth: element-sized (clamped, centered on the cursor) when an
        element is hovered, the legacy square otherwise.  With the right-Ctrl
        marker held the crop BECOMES that legacy patch - exactly what a visual
        click will search for - so it gets a caption and the box follows live.
        """
        x, y = res.get("x"), res.get("y")
        if x is None or y is None:
            self._crop.hide()
            self._crop_label.hide()
            return
        try:
            from recorder.screenshot_manager import click_crop_region
        except Exception:
            try:
                from LoOper.recorder.screenshot_manager import click_crop_region
            except Exception:
                self._crop.hide()
                self._crop_label.hide()
                return

        visual = self._visual_marker()
        element_rect = res["rect"] if res.get("element") else None
        left, top, width, height = click_crop_region(
            x, y, None if visual else element_rect)
        l, t, r, b = self._to_logical((left, top, left + width, top + height))
        if r - l < 2 or b - t < 2:
            self._crop.hide()
            self._crop_label.hide()
            return
        self._crop.setGeometry(l - self.x(), t - self.y(), r - l, b - t)
        self._crop.show()

        if not visual:
            self._crop_label.hide()
            return
        # Name the patch so the gesture is visible while it is held.
        self._crop_label.setText(f"visual match {width}x{height}")
        self._crop_label.adjustSize()
        label_y = b - self.y() + 2
        if label_y + self._crop_label.height() > self.height():
            label_y = t - self.y() - self._crop_label.height() - 2
        self._crop_label.move(max(0, l - self.x()), max(0, label_y))
        self._crop_label.show()
