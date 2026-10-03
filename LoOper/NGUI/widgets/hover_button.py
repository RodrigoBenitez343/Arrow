"""Reusable icon button: a soft cyan "fog" that grows and an icon that enlarges
on hover, animated smoothly both ways.

Qt stylesheets cannot draw a soft glow, and a drop-shadow effect is built from
the widget's rendered pixels — on a transparent-background button that is only
the icon glyph, so the "glow" is a faint halo (and the button is invisible at
rest). So the fog is painted here as a radial cyan gradient whose intensity is
driven by an animated ``glow`` property, and the button keeps a visible resting
chip so it reads as a button before it is hovered.
"""

import logging

from PyQt5.QtCore import (QEasingCurve, QPointF, QPropertyAnimation, QSize, Qt,
                          pyqtProperty)
from PyQt5.QtGui import QColor, QPainter, QRadialGradient
from PyQt5.QtWidgets import QPushButton

from ..constants import ACCENT_COLOR, TEXT_COLOR
from ..icons import tabler_qicon

logger = logging.getLogger(__name__)

GROW_PX = 6            # how much the icon grows on hover
FOG_ALPHA = 150        # peak fog intensity (0-255)
_DURATION = 170        # ms, both ways


class HoverGlowButton(QPushButton):
    """Icon button: resting chip, cyan fog + icon grow on hover."""

    def __init__(self, icon_name=None, size=32, icon=18,
                 color=TEXT_COLOR, parent=None):
        super().__init__(parent)
        self._icon_px = int(icon)
        self._icon_color = color
        self._glow = 0.0
        self.setFixedSize(int(size), int(size))
        self.setCursor(Qt.PointingHandCursor)
        self.setIconSize(QSize(self._icon_px, self._icon_px))
        if icon_name:
            self.setIcon(tabler_qicon(icon_name, self._icon_px, color))
        # A visible chip at rest, so the button is not "invisible until hovered".
        self.setStyleSheet(
            f"QPushButton {{ background: rgba(255,255,255,0.10); border: none;"
            f" border-radius: {int(size) // 2}px; }}"
            f"QPushButton:hover {{ background: rgba(255,255,255,0.18); }}"
            f"QPushButton:checked {{ background: rgba(34,211,238,0.20); }}"
        )

        self._glow_anim = QPropertyAnimation(self, b"glow", self)
        self._glow_anim.setDuration(_DURATION)
        self._glow_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._icon_anim = QPropertyAnimation(self, b"iconSize", self)
        self._icon_anim.setDuration(_DURATION)
        self._icon_anim.setEasingCurve(QEasingCurve.OutCubic)

    # ── animated glow property (painted behind the chip) ──────────────

    def get_glow(self):
        return self._glow

    def set_glow(self, value):
        self._glow = float(value)
        self.update()

    glow = pyqtProperty(float, get_glow, set_glow)

    def set_icon_name(self, icon_name, color=None):
        """Swap the icon (e.g. SEND <-> MICROPHONE when the mode changes)."""
        self.setIcon(tabler_qicon(icon_name, self._icon_px,
                                  color or self._icon_color))

    # ── hover ─────────────────────────────────────────────────────────

    def enterEvent(self, event):
        super().enterEvent(event)
        self._animate(True)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._animate(False)

    def _animate(self, on):
        try:
            self._glow_anim.stop()
            self._glow_anim.setStartValue(self._glow)
            self._glow_anim.setEndValue(1.0 if on else 0.0)
            self._glow_anim.start()
            px = self._icon_px + GROW_PX if on else self._icon_px
            self._icon_anim.stop()
            self._icon_anim.setStartValue(self.iconSize())
            self._icon_anim.setEndValue(QSize(px, px))
            self._icon_anim.start()
        except Exception:
            logger.exception("HoverGlowButton._animate failed")

    def paintEvent(self, event):
        if self._glow > 0.01:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            radius = max(self.width(), self.height()) * (0.42 + 0.30 * self._glow)
            grad = QRadialGradient(QPointF(self.rect().center()), radius)
            center = QColor(ACCENT_COLOR)
            center.setAlpha(int(FOG_ALPHA * self._glow))
            edge = QColor(ACCENT_COLOR)
            edge.setAlpha(0)
            grad.setColorAt(0.0, center)
            grad.setColorAt(1.0, edge)
            painter.setPen(Qt.NoPen)
            painter.setBrush(grad)
            r = self.width() // 2
            painter.drawRoundedRect(self.rect(), r, r)
            painter.end()
        super().paintEvent(event)


if __name__ == "__main__":  # ponytail: smallest runnable check for the hover logic
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtWidgets import QApplication

    _app = QApplication([])
    b = HoverGlowButton("SEND", size=32, icon=18)
    b.show()
    assert b.get_glow() == 0.0, "fog must start hidden"
    b._animate(True)
    b._glow_anim.setCurrentTime(b._glow_anim.duration())
    b._icon_anim.setCurrentTime(b._icon_anim.duration())
    assert b.get_glow() == 1.0, "fog must light up on hover"
    assert b.iconSize().width() == 18 + GROW_PX, "icon must grow on hover"
    b._animate(False)
    b._glow_anim.setCurrentTime(b._glow_anim.duration())
    b._icon_anim.setCurrentTime(b._icon_anim.duration())
    assert b.get_glow() == 0.0, "fog must clear on leave"
    assert b.iconSize().width() == 18, "icon must shrink back"
    print("HoverGlowButton self-check OK")
