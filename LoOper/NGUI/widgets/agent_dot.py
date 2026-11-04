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
import logging
import time

from PyQt5.QtCore import QPointF, Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import (QBrush, QColor, QLinearGradient, QPainter, QPen,
                         QPolygonF, QCursor)
from PyQt5.QtWidgets import QWidget

from ..constants import ACCENT_COLOR
from .mitosis_loader import MitosisLoader

logger = logging.getLogger(__name__)

ARROW_W = 28
ARROW_H = 28
ARROW_TIP = (3.0, 3.0)   # hotspot: the arrow point, aligned to the cursor
POLL_INTERVAL = 16       # ms (~60 fps tracking)
_CAPTURE_HIDE_S = 0.3    # stay blank this long after a screen read
THINK_BOX = 72           # the node animation that stands in for the cursor

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


# ── OS pointer blanking (platform gate: Win32 cursors, X11 XFixes) ───
# The hardware cursor is always composited above every window, so the arrow can
# only read as "on top" by hiding the real pointer while it is up.
_cursors_hidden = False
_startup_reset_done = False

# Calibration knob: set False to keep the real cursor visible (the arrow then
# reads as a colored highlight around it instead of replacing it).
_HIDE_OS_CURSOR = True


def _set_pointer_visible(visible: bool) -> None:
    """Hide/show the OS pointer through the platform gate."""
    try:  # LoOper/ on sys.path: the app and the test suite both run that way
        from platform_gate import set_pointer_visible
    except ImportError:  # imported as LoOper.*
        from LoOper.platform_gate import set_pointer_visible
    try:
        set_pointer_visible(visible)
    except Exception:
        logger.exception("Could not %s the OS pointer",
                         "show" if visible else "hide")


def _blank_system_cursors():
    """Replace every system cursor with a transparent one (hide the pointer)."""
    global _cursors_hidden
    if _cursors_hidden:
        return
    _cursors_hidden = True
    _set_pointer_visible(False)


def _restore_system_cursors():
    """Reload the user's real cursors from the active scheme."""
    global _cursors_hidden
    if not _cursors_hidden:
        return
    _cursors_hidden = False
    _set_pointer_visible(True)


def _force_restore_system_cursors():
    """Unconditionally show the pointer (also clears a stale blank)."""
    _set_pointer_visible(True)


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
        # Thinking: the cursor IS the node animation.  Acting: the arrow.
        self._thinking = False
        self._loader = None
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
        if self._thinking and self._loader is not None:
            self._loader.start()

    def hideEvent(self, event):
        self._timer.stop()
        if self._loader is not None:
            self._loader.stop()
        _restore_system_cursors()
        super().hideEvent(event)

    # ── Thinking / acting appearance ────────────────────────────────

    def set_thinking(self, thinking: bool):
        """Thinking: the node animation replaces the cursor.

        Acting (a sequence is driving the screen): the plain arrow, so the
        user can see what the runtime is doing with the pointer.
        """
        if self._thinking == thinking:
            return
        self._thinking = bool(thinking)
        if self._thinking:
            if self._loader is None:
                self._loader = MitosisLoader(self, size=THINK_BOX)
            self._loader.setGeometry(0, 0, THINK_BOX, THINK_BOX)
            self._loader.show()
            if self.isVisible():
                self._loader.start()
            self.setFixedSize(THINK_BOX, THINK_BOX)
        else:
            if self._loader is not None:
                self._loader.stop()
                self._loader.hide()
            self.setFixedSize(ARROW_W, ARROW_H)
        self._follow_mouse()
        self.update()

    # ── Mouse following ──────────────────────────────────────────────

    def _follow_mouse(self):
        """Track the cursor: the arrow tip rides the hotspot, the animation centres on it."""
        try:
            cursor = QCursor.pos()
            if self._thinking:
                half = THINK_BOX // 2
                self.move(cursor.x() - half, cursor.y() - half)
            else:
                self.move(cursor.x() - int(ARROW_TIP[0]),
                          cursor.y() - int(ARROW_TIP[1]))
            if self._loader is not None:
                blank = self._is_blank()
                if self._loader.isVisible() == blank:
                    self._loader.setVisible(not blank)
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
        if self._thinking or self._is_blank():
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
