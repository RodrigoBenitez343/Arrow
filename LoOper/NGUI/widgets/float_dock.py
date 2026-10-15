"""float_dock.py — the compact dock row that opens the canvas panels.

The overlay bars are no longer edge-docked: a single floating row of three
toggle buttons sits where the top bar used to be, and each button slides its
panel down from under the row (see ``UIComponents.toggle_panel``). One panel
open at a time, so the canvas stays readable and the builder chat keeps its
column.
"""

import logging

from PyQt5.QtCore import Qt, QSize, pyqtSignal
from PyQt5.QtWidgets import QFrame, QHBoxLayout, QToolButton, QWidget

from ..constants import ACCENT_COLOR, SIDE_PANEL_BG
from ..i18n import _
from ..icons import tabler_qicon

logger = logging.getLogger(__name__)

#: (key, icon, label) for the panels the dock opens.
DOCK_ITEMS = (
    ('actions', 'ADJUSTMENTS', _("Actions")),
    ('library', 'LIBRARY', _("Library")),
    ('nodes', 'PUZZLE', _("Nodes")),
)

#: Sub-dock for the Library panel: one button per node collection, so the
#: library itself stays a plain list (no tab row inside it).  The middle field
#: is the NODE TYPE whose icon the button wears, so a switcher and the nodes
#: it lists read as the same family (FloatDockBar node_icons=True).
COLLECTION_ITEMS = (
    ('sequence', 'sequence', _("Sequences")),
    ('chain', 'chain_import', _("Chains")),
    ('llm', 'llm', _("LLM")),
    ('context', 'context', _("Context")),
    ('code', 'code', _("Code")),
    ('web_seq', 'web_sequence', _("Web Sequences")),
)

BUTTON_SIZE = 40
#: Icon box inside a dock button; sized so the node glyphs (get_node_icon)
#: render full instead of cramped.
BUTTON_ICON = 22


class FloatDockBar(QWidget):
    """A row of toggle buttons; each one asks to open its panel."""

    panel_requested = pyqtSignal(str)

    def __init__(self, parent=None, items=None, node_icons=False):
        super().__init__(parent)
        items = items if items is not None else DOCK_ITEMS
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet("background: transparent;")

        self._content_frame = QFrame(self)
        self._content_frame.setObjectName("dockContent")
        # Transparent: the buttons float individually on the canvas, each its
        # own card, so no dark frame boxes the whole row together.
        self._content_frame.setStyleSheet(
            "QFrame#dockContent { background: transparent; }"
        )
        lay = QHBoxLayout(self._content_frame)
        # Room around each button for its float shadow, plus a gap so the
        # buttons read as separate floats rather than one stuck-together block.
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(8)

        self._buttons = {}
        for key, icon_name, label in items:
            btn = QToolButton(self._content_frame)
            btn.setCheckable(True)
            btn.setFixedSize(BUTTON_SIZE, BUTTON_SIZE)
            ico = None
            if node_icons:
                # Wear the exact icon the node type uses (get_node_icon), so a
                # list's switcher and the nodes inside it are the same family.
                try:
                    from .collapsible_toolbar import get_node_icon
                    ico = get_node_icon(icon_name, BUTTON_ICON)
                except Exception:
                    ico = None
            if ico is None or ico.isNull():
                ico = tabler_qicon(icon_name, BUTTON_ICON)
            if ico is not None and not ico.isNull():
                btn.setIcon(ico)
                btn.setIconSize(QSize(BUTTON_ICON, BUTTON_ICON))
            btn.setToolTip(label)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(
                f"""
                QToolButton {{
                    background-color: {SIDE_PANEL_BG};
                    border: 1px solid rgba(255, 255, 255, 0.08);
                    border-radius: 10px;
                }}
                QToolButton:checked {{
                    background-color: {ACCENT_COLOR};
                    border-color: {ACCENT_COLOR};
                }}
                QToolButton:hover {{
                    border-color: {ACCENT_COLOR};
                }}
                """
            )
            btn.clicked.connect(lambda _c=False, k=key: self.panel_requested.emit(k))
            lay.addWidget(btn)
            self._buttons[key] = btn

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self._content_frame)

    def set_active(self, key):
        """Check only the button of the open panel (None clears all)."""
        for k, btn in self._buttons.items():
            btn.setChecked(k == key)


class CanvasSideButton(QToolButton):
    """A checkable icon button pinned to the canvas edge.

    Used for the Builder chat toggle: same surface family as the dock row,
    floating on the canvas' right side instead of the top bar.
    """

    def __init__(self, icon_name, tooltip, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        # Same size as the dock row's buttons so the row and this float match.
        self.setFixedSize(BUTTON_SIZE, BUTTON_SIZE)
        ico = tabler_qicon(icon_name, BUTTON_ICON)
        if ico is not None and not ico.isNull():
            self.setIcon(ico)
            self.setIconSize(QSize(BUTTON_ICON, BUTTON_ICON))
        self.setToolTip(tooltip)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(
            f"""
            QToolButton {{
                background-color: {SIDE_PANEL_BG};
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 10px;
            }}
            QToolButton:checked {{
                background-color: {ACCENT_COLOR};
                border-color: {ACCENT_COLOR};
            }}
            QToolButton:hover {{
                border-color: {ACCENT_COLOR};
            }}
            """
        )
