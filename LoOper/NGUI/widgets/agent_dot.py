"""Cursor arrow indicator drawn on top of the mouse pointer.

A frameless, always-on-top, CLICK-THROUGH overlay that paints the Arrow brand
pointer on the cursor.  It is shown while the app is recording, replaying a
chain, or showing an agent surface (overlay / notch), so the user can always
see where the app is working.

The hardware cursor always composites above every window, so nothing can be
drawn "on top" of it while it is shown: the system cursors are replaced with a
transparent one while the arrow is up, and restored whenever it hides, at
exit, and once at start-up (so a previous crash cannot leave a cursorless
user).

The overlay is transparent for input: it sits exactly under the OS cursor and
must never swallow a click (recording and replay drive real mouse input).
Because it lives at the click point it also blanks itself for every screen read
(``bus.hide_overlay_for_capture``) and while a mouse button is held during
recording, so it can never bake into a recorded click template or a replay
match.

ponytail: a single small polygon repainted at ~60 fps; stop repainting
(self._busy) if this ever shows as CPU.
"""

import atexit
import ctypes
import logging
import sys
import time

from PyQt5.QtCore import QPointF, Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import (QBrush, QColor, QLinearGradient, QPainter, QPen,
                         QPolygonF, QCursor)
from PyQt5.QtWidgets import QWidget

from ..constants import ACCENT_COLOR

logger = logging.getLogger(__name__)

ARROW_W = 28
ARROW_H = 28
ARROW_TIP = (3.0, 3.0)   # hotspot: the arrow point, aligned to the cursor
POLL_INTERVAL = 16       # ms (~60 fps tracking)
_CAPTURE_HIDE_S = 0.3    # stay blank this long after a screen read

# Execution-state colours
DOT_EXECUTE_COLOR = "#ef4444"   # red while a chain is running
DOT_IDLE_COLOR = ACCENT_COLOR    # teal otherwise

# Pointer HEAD only, tip at the origin, symmetric about the 45-degree axis so
# it points up-left like a real cursor.  The 3rd point pulls the back edge in
# for a swept, modern silhouette.
_ARROW = [(0.0, 0.0), (21.0, 10.0), (12.0, 12.0), (10.0, 21.0)]
_ARROW_SCALE = 1.0


def _arrow_points():
    """Arrowhead polygon with its tip at ARROW_TIP."""
    tipx, tipy = ARROW_TIP
    return [QPointF(x * _ARROW_SCALE + tipx, y * _ARROW_SCALE + tipy)
            for (x, y) in _ARROW]


# ── OS cursor blanking (Windows) ─────────────────────────────────────
# The hardware cursor is always composited above every window, so the arrow can
# only read as "on top" by hiding the real pointer while it is up.
_SPI_SETCURSORS = 0x0057
# Every OCR_* shape the pointer can take; blanking OCR_NORMAL alone leaves an
# I-beam / hand / resize cursor stuck on top of the arrow.
_OCR_IDS = (32512, 32513, 32514, 32515, 32516, 32642, 32643, 32644,
            32645, 32646, 32648, 32649, 32650, 32651)
_cursors_hidden = False
_startup_reset_done = False

# Calibration knob: set False to keep the real cursor visible (the arrow then
# reads as a colored highlight around it instead of replacing it).
_HIDE_OS_CURSOR = True


def _win_user32():
    """user32 on Windows, else None (this feature is Windows-only)."""
    if sys.platform != "win32":
        return None
    return ctypes.windll.user32


def _blank_system_cursors():
    """Replace every system cursor with a transparent one (hide the pointer)."""
    global _cursors_hidden
    if _cursors_hidden:
        return
    u = _win_user32()
    if u is None:
        return
    try:
        import ctypes.wintypes as wt
        u.CreateCursor.argtypes = [wt.HINSTANCE, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int,
                                   ctypes.c_void_p, ctypes.c_void_p]
        u.CreateCursor.restype = wt.HANDLE
        u.CopyIcon.argtypes = [wt.HANDLE]
        u.CopyIcon.restype = wt.HANDLE
        u.SetSystemCursor.argtypes = [wt.HANDLE, wt.DWORD]
        u.SetSystemCursor.restype = wt.BOOL
        u.DestroyCursor.argtypes = [wt.HANDLE]
        n = 32 * 32 // 8
        and_mask = (ctypes.c_ubyte * n)(*([0xFF] * n))   # keep the screen
        xor_mask = (ctypes.c_ubyte * n)(*([0x00] * n))   # no invert -> invisible
        base = u.CreateCursor(None, 0, 0, 32, 32, and_mask, xor_mask)
        if not base:
            return
        try:
            for ocr in _OCR_IDS:
                copy = u.CopyIcon(base)   # SetSystemCursor takes ownership
                if copy:
                    u.SetSystemCursor(copy, ocr)
        finally:
            u.DestroyCursor(base)
        _cursors_hidden = True
    except Exception:
        logger.exception("Could not blank the system cursors")


def _restore_system_cursors():
    """Reload the user's real cursors from the active scheme."""
    global _cursors_hidden
    if not _cursors_hidden:
        return
    _cursors_hidden = False
    _force_restore_system_cursors()


def _force_restore_system_cursors():
    """Unconditionally reload the system cursors (also clears a stale blank)."""
    u = _win_user32()
    if u is None:
        return
    try:
        u.SystemParametersInfoW.argtypes = [ctypes.c_uint, ctypes.c_uint,
                                            ctypes.c_void_p, ctypes.c_uint]
        u.SystemParametersInfoW(0x0057, 0, None, 0)
    except Exception:
        logger.exception("Could not restore the system cursors")


def _reset_system_cursors_once():
    """Clear a blank left behind by a previous crash (start-up safety net)."""
    global _startup_reset_done
    if _startup_reset_done:
        return
    _startup_reset_done = True
    _force_restore_system_cursors()


atexit.register(_force_restore_system_cursors)


class AgentDot(QWidget):
    """Click-through cursor arrow, shown while the app is busy or in agent mode."""

    clicked = pyqtSignal()          # kept for API compat (window is click-through)

    _hideRequested = pyqtSignal()   # capture-hide request from the replay thread

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("agentDot")

        # Frameless, always-on-top, no taskbar entry, and CLICK-THROUGH: the
        # arrow rides the OS cursor, so it must never steal a click.
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowTransparentForInput
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)

        self.setFixedSize(ARROW_W, ARROW_H)

        # Cursor-tracking timer — polls the pointer position.
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_INTERVAL)
        self._timer.timeout.connect(self._follow_mouse)

        # ponytail: simple bool flag for execute vs idle appearance
        self._execute_mode = False
        # While set, the arrow stays blank (a recording button is held).
        self._suppress_event = None
        # Blank until this timestamp (a screen read is in flight).
        self._blank_until = 0.0

        # The player calls hide_for_capture() from the replay thread; route it
        # to the GUI thread and block until the blank has landed, so a screen
        # read can never contain the arrow.
        self._hideRequested.connect(self._hide_now, Qt.BlockingQueuedConnection)

        # Clear a blank cursor a previous crash may have left behind.
        _reset_system_cursors_once()

    # ── Lifecycle ────────────────────────────────────────────────────

    def showEvent(self, event):
        super().showEvent(event)
        self._follow_mouse()
        self._timer.start()

    def hideEvent(self, event):
        self._timer.stop()
        _restore_system_cursors()
        super().hideEvent(event)

    # ── Mouse following ──────────────────────────────────────────────

    def _follow_mouse(self):
        """Track the cursor: the arrow tip rides the cursor hotspot."""
        try:
            cursor = QCursor.pos()
            self.move(cursor.x() - int(ARROW_TIP[0]),
                      cursor.y() - int(ARROW_TIP[1]))
            self.update()
        except Exception:
            pass
        self._apply_os_cursor()

    def _apply_os_cursor(self):
        """Hide the real pointer while the arrow is up, so the arrow IS it.

        Restored while a recording click is held: the arrow blanks for the
        template crop there, and the user still needs to see where they click.
        """
        evt = self._suppress_event
        if not _HIDE_OS_CURSOR:
            return
        if self.isVisible() and not (evt is not None and evt.is_set()):
            _blank_system_cursors()
        else:
            _restore_system_cursors()

    # ── Capture / recording suppression ──────────────────────────────

    def set_suppress_event(self, event):
        """Stay blank while *event* is set (a recording click is captured)."""
        self._suppress_event = event

    def hide_for_capture(self):
        """Replay thread: blank the arrow for a screen read, then return."""
        try:
            if QThread.currentThread() == self.thread():
                self._hide_now()
            else:
                self._hideRequested.emit()
        except Exception:
            logger.exception("AgentDot.hide_for_capture failed")

    def _hide_now(self):
        """GUI thread: blank the arrow and keep it blank briefly."""
        self._blank_until = time.time() + _CAPTURE_HIDE_S
        self.repaint()

    def _is_blank(self):
        """True while the arrow must not paint (held button / screen read)."""
        if time.time() < self._blank_until:
            return True
        evt = self._suppress_event
        return evt is not None and evt.is_set()

    # ── Painting ─────────────────────────────────────────────────────

    def paintEvent(self, event):
        if self._is_blank():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        base = QColor(DOT_EXECUTE_COLOR if self._execute_mode else DOT_IDLE_COLOR)
        poly = QPolygonF(_arrow_points())

        # Soft halo in the same hue (no hard border) so it reads on any screen.
        halo = QColor(base)
        halo.setAlpha(70)
        pen = QPen(halo)
        pen.setWidthF(6.0)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawPolygon(poly)

        # Modern fill: hue-shifted tint at the tip falling off to the base hue.
        hue, sat, val, _ = base.getHsv()
        tip = QColor.fromHsv((hue + 25) % 360, min(255, sat + 15),
                             min(255, val + 35))
        grad = QLinearGradient(poly.at(0), poly.at(2))
        grad.setColorAt(0.0, tip)
        grad.setColorAt(1.0, base)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(grad))
        painter.drawPolygon(poly)

        painter.end()

    def set_execute_mode(self, executing: bool):
        """Switch between idle (teal) and executing (red) appearance."""
        if self._execute_mode != executing:
            self._execute_mode = executing
            self.update()


if __name__ == "__main__":
    # Self-check: the tip must sit on the cursor hotspot, and the arrow must be
    # blank exactly while armed (held button / in-flight screen read).
    import os
    import threading

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtWidgets import QApplication

    _app = QApplication([])
    _dot = AgentDot()

    _pts = _arrow_points()
    assert _pts[0].x() == ARROW_TIP[0] and _pts[0].y() == ARROW_TIP[1], \
        "arrow tip must sit at the hotspot"

    _dot._blank_until = time.time() + 1
    assert _dot._is_blank(), "an in-flight screen read must blank the arrow"
    _dot._blank_until = 0.0

    _ev = threading.Event()
    _dot.set_suppress_event(_ev)
    assert not _dot._is_blank()
    _ev.set()
    assert _dot._is_blank(), "a held recording button must blank the arrow"
    _ev.clear()
    assert not _dot._is_blank()
    print("AgentDot self-check OK")
