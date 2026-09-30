"""Minimal floating dot that follows the mouse when agent mode is contracted.

Click the dot to expand into the full AgentOverlay chat interface.
"""

import logging

from PyQt5.QtCore import QPoint, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QMouseEvent, QPainter, QCursor
from PyQt5.QtWidgets import QWidget

from ..constants import ACCENT_COLOR

logger = logging.getLogger(__name__)

DOT_SIZE = 16
DOT_OFFSET_X = 20   # pixels right of cursor
DOT_OFFSET_Y = 20   # pixels below cursor
POLL_INTERVAL = 30  # ms (~33 fps tracking)

# Execution-state colours
DOT_EXECUTE_COLOR = "#ef4444"   # red glow/inner when chain is running
DOT_IDLE_COLOR = ACCENT_COLOR    # teal glow/inner when idle


class AgentDot(QWidget):
    """Floating frameless dot that follows the cursor near the mouse pointer.

    Emits *clicked* when the user clicks (not drags) the dot.
    """

    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("agentDot")

        # Frameless, always-on-top, no taskbar entry
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)

        self.setFixedSize(DOT_SIZE, DOT_SIZE)

        # Mouse-tracking timer — polls cursor position periodically
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_INTERVAL)
        self._timer.timeout.connect(self._follow_mouse)

        # ponytail: simple bool flag for execute vs idle appearance
        self._execute_mode = False

        # Click-vs-drag detection state
        self._press_pos = QPoint()
        self._was_drag = False

    # ── Lifecycle ────────────────────────────────────────────────────

    def showEvent(self, event):
        super().showEvent(event)
        self._follow_mouse()   # snap to cursor immediately
        self._timer.start()

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    # ── Mouse following ──────────────────────────────────────────────

    def _follow_mouse(self):
        """Reposition the dot slightly to the right and below the cursor."""
        try:
            cursor = QCursor.pos()
            self.move(
                cursor.x() + DOT_OFFSET_X,
                cursor.y() + DOT_OFFSET_Y,
            )
        except Exception:
            pass

    # ── Painting ─────────────────────────────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        color = DOT_EXECUTE_COLOR if self._execute_mode else DOT_IDLE_COLOR

        # Outer glow ring (fainter)
        glow_color = QColor(color)
        glow_color.setAlpha(50)
        painter.setBrush(glow_color)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(self.rect().adjusted(1, 1, -1, -1))

        # Inner solid dot
        painter.setBrush(QColor(color))
        painter.drawEllipse(self.rect().adjusted(4, 4, -4, -4))

        painter.end()

    def set_execute_mode(self, executing: bool):
        """Switch between idle (teal) and executing (red) appearance."""
        if self._execute_mode != executing:
            self._execute_mode = executing
            self.update()

    # ── Mouse interaction ────────────────────────────────────────────

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self._press_pos = event.globalPos()
            self._was_drag = False
            self._timer.stop()

    def mouseMoveEvent(self, event: QMouseEvent):
        if event.buttons() & Qt.LeftButton and not self._was_drag:
            dist = (event.globalPos() - self._press_pos).manhattanLength()
            if dist >= 5:
                self._was_drag = True

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._timer.start()
        if event.button() == Qt.LeftButton and not self._was_drag:
            self.clicked.emit()
