"""Soft 'degrade' glow for interactive elements: white at rest, cyan on hover.

Qt stylesheets cannot draw a soft glow, so a small drop-shadow effect is
attached per widget, and its colour is swapped as the pointer enters/leaves.
Hover is detected by polling the pointer against each widget's rectangle (a
child only receives hover while the pointer is over that child, which flickers
on containers), so one small timer drives every registered widget.
"""
import logging

from PyQt5.QtCore import QEvent, QObject, QTimer
from PyQt5.QtGui import QColor, QCursor
from PyQt5.QtWidgets import (QApplication, QAbstractButton, QAbstractSpinBox,
                             QComboBox, QGraphicsDropShadowEffect, QGroupBox,
                             QLineEdit, QPlainTextEdit, QTextEdit)

from .constants import ACCENT_COLOR

logger = logging.getLogger(__name__)

_GLOW_BLUR = 5           # tight, crisp halo (smaller = less sketchy)
_GLOW_IDLE_ALPHA = 70    # white fog at rest
_GLOW_HOVER_ALPHA = 150  # cyan on hover
_POLL_MS = 120

# Interactive element types that get the glow.
_GLOW_TYPES = (QAbstractButton, QLineEdit, QAbstractSpinBox, QComboBox,
               QTextEdit, QPlainTextEdit, QGroupBox)


class _GlowManager(QObject):
    """Polls the pointer and swaps each registered widget's glow colour."""

    def __init__(self, app):
        super().__init__(app)
        self._items = []  # [widget, effect, idle, hover, is_hover]
        self._timer = QTimer(self)
        self._timer.setInterval(_POLL_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def register(self, widget, effect, idle, hover):
        self._items.append([widget, effect, idle, hover, None])

    def _tick(self):
        try:
            pos = QCursor.pos()
            alive = []
            for item in self._items:
                widget, effect, idle, hover, state = item
                try:
                    if widget.isVisible():
                        inside = widget.rect().contains(widget.mapFromGlobal(pos))
                        if inside != state:
                            effect.setColor(hover if inside else idle)
                            item[4] = inside
                    alive.append(item)
                except RuntimeError:
                    pass  # C++ widget deleted
                except Exception:
                    alive.append(item)
            self._items = alive
        except Exception:
            pass


_manager = None


def _get_manager():
    global _manager
    if _manager is None:
        _manager = _GlowManager(QApplication.instance())
    return _manager


def attach_glow(widget, blur=_GLOW_BLUR):
    """Give *widget* a soft glow: white at rest, cyan while hovered."""
    if widget is None:
        return
    try:
        # Never steal an existing effect (e.g. the dialog's own window shadow).
        if widget.graphicsEffect() is not None:
            return
        idle = QColor(255, 255, 255, _GLOW_IDLE_ALPHA)
        hover = QColor(ACCENT_COLOR)
        hover.setAlpha(_GLOW_HOVER_ALPHA)
        effect = QGraphicsDropShadowEffect(widget)
        effect.setBlurRadius(blur)
        effect.setOffset(0, 0)
        effect.setColor(idle)
        widget.setGraphicsEffect(effect)
        widget._glow_effect = effect  # keep a reference
        _get_manager().register(widget, effect, idle, hover)
    except Exception:
        logger.debug("attach_glow failed", exc_info=True)


def attach_glow_tree(root, include_frames=()):
    """Attach the glow to every interactive element under *root*."""
    if root is None:
        return
    try:
        widgets = [root] if isinstance(root, _GLOW_TYPES) else []
        for t in _GLOW_TYPES + tuple(include_frames):
            widgets.extend(root.findChildren(t))
        for w in widgets:
            attach_glow(w)
    except Exception:
        logger.debug("attach_glow_tree failed", exc_info=True)
