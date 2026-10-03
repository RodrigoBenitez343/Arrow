"""Fixed-width stack of log-entry "bubbles" for the execution overlay.

The terminal renders every record as its own boxed panel; this mirrors that
look on screen: one rounded, level-coloured bubble per log entry, in a column
of FIXED width so the panel only ever grows vertically as entries come and go.
Widgets are pooled and reused, so the 30 fps overlay repaint never re-creates
them.
"""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from ..constants import ACCENT_COLOR, DANGER_COLOR, TEXT_MUTED, TEXT_SECONDARY

# Level accent colours (mirror logging_setup's console styles).
LEVEL_COLORS = {
    "DEBUG": TEXT_MUTED,
    "INFO": ACCENT_COLOR,
    "WARNING": "#f0b429",
    "ERROR": DANGER_COLOR,
    "CRITICAL": DANGER_COLOR,
}

_BUBBLE_BG = "rgba(8,12,16,0.84)"
_BUBBLE_TEXT = TEXT_SECONDARY


class LogBubble(QFrame):
    """One log entry: a header line (time / level / component) + the message."""

    def __init__(self, width, parent=None):
        super().__init__(parent)
        self.setObjectName("logBubble")
        self.setFixedWidth(width)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 5)
        lay.setSpacing(1)

        self._head = QLabel(self)
        hf = self._head.font()
        hf.setPointSize(7)
        hf.setBold(True)
        self._head.setFont(hf)
        self._head.setStyleSheet("background: transparent;")

        self._body = QLabel(self)
        bf = self._body.font()
        bf.setPointSize(8)
        self._body.setFont(bf)
        self._body.setWordWrap(True)
        self._body.setStyleSheet(
            f"color: {_BUBBLE_TEXT}; background: transparent;")

        lay.addWidget(self._head)
        lay.addWidget(self._body)

    def set_entry(self, when, level, component, message):
        color = LEVEL_COLORS.get(level, TEXT_MUTED)
        self.setStyleSheet(
            f"QFrame#logBubble {{ background: {_BUBBLE_BG};"
            f" border-left: 3px solid {color}; border-radius: 6px; }}"
        )
        self._head.setText(f"{when}  [{level}]  {component}")
        self._head.setStyleSheet(f"color: {color}; background: transparent;")
        self._body.setText(message)


class LogBubbleList(QWidget):
    """A fixed-width column of bubbles that only stretches vertically."""

    def __init__(self, width, max_bubbles=8, parent=None):
        super().__init__(parent)
        self._width = width
        self._max = max_bubbles
        self.setFixedWidth(width)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.setAlignment(Qt.AlignTop)
        self._lay = lay
        self._bubbles = []
        self._key = None

    def _add(self):
        b = LogBubble(self._width, self)
        self._lay.addWidget(b)
        self._bubbles.append(b)
        return b

    def set_entries(self, entries):
        """entries: newest-last list of (when_str, level, component, message)."""
        entries = list(entries)[-self._max:]
        key = tuple((lvl, comp, msg) for (_t, lvl, comp, msg) in entries)
        if key == self._key:
            return
        self._key = key
        for i, entry in enumerate(entries):
            b = self._bubbles[i] if i < len(self._bubbles) else self._add()
            b.set_entry(*entry)
            b.show()
        for b in self._bubbles[len(entries):]:
            b.hide()
        self.updateGeometry()
        self.adjustSize()

    def clear(self):
        self._key = None
        for b in self._bubbles:
            b.hide()
