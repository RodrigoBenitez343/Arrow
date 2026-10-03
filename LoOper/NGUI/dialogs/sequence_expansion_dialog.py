"""Overlay that expands a Sequence node into its recorded steps.

Mirrors the chain-import expansion: a nested graph view is hosted in a dialog
and the node's content is laid out there. For a sequence the content is its
recorded action list, shown as one ActionNode per step wired in order.

Per the product ask, this overlay shows ONLY the top toolbar - the left
(library) and right (node) bars are removed, since the steps are the content.

It is EDITABLE: each step node shows a preview of what it acted on (the click
screenshot), dragging/reordering the nodes changes the order, and removing a
node drops that step. "Save Changes" rewrites the sequence file accordingly.
"""

import base64
import json
import logging
import os

from PyQt5.QtWidgets import (QDialogButtonBox, QHBoxLayout, QLabel, QPushButton)
from PyQt5.QtCore import QSize
from PyQt5.QtGui import QIcon, QPainter, QPixmap, QColor

from .base_dialog import ModernDialog
from ..i18n import _
from ..constants import TEXT_COLOR, DIALOG_LARGE_W, DIALOG_LARGE_H

logger = logging.getLogger(__name__)

# Horizontal spacing between the laid-out action nodes.
_ACTION_GAP_X = 250.0


class SequenceExpansionDialog(ModernDialog):
    """Overlay showing a Sequence node's recorded actions as a graph."""

    def __init__(self, sequence_node, parent_graph_view, parent=None):
        super().__init__(parent, title=_("Sequence"), help_topic="sequence-dialog")
        self.sequence_node = sequence_node
        self.parent_graph_view = parent_graph_view

        self.setModal(True)
        # A nested graph needs room, so do not content-fit this one.
        self._auto_fit = False
        self.resize(DIALOG_LARGE_W, DIALOG_LARGE_H)

        self.setup_ui()
        self.load_sequence()

    # ------------------------------------------------------------------ UI
    def setup_ui(self):
        layout = self.content_layout

        header = QHBoxLayout()
        name = self.sequence_node.name() if self.sequence_node is not None else ""
        header_label = QLabel(_("Sequence: {name}").format(name=name))
        header_label.setStyleSheet(
            f"color: {TEXT_COLOR}; font-size: 14px; font-weight: bold;")
        header.addWidget(header_label)
        header.addStretch()

        self.maximize_button = QPushButton(self)
        self.maximize_button.setFixedSize(30, 30)
        self.maximize_button.setToolTip(_("Maximize/Restore"))
        self.maximize_button.setIcon(self._max_icon(16))
        self.maximize_button.setIconSize(QSize(16, 16))
        self.maximize_button.clicked.connect(self.toggle_maximize)
        header.addWidget(self.maximize_button)
        layout.addLayout(header)

        # A nested graph hosts the sequence's steps.
        from ..graph_view import GraphViewWidget
        self.graph_view = GraphViewWidget(self)
        self.graph_view.setMinimumSize(900, 600)
        self._strip_side_bars(self.graph_view)
        layout.addWidget(self.graph_view, 1)

        button_box = QDialogButtonBox(QDialogButtonBox.Close)
        button_box.rejected.connect(self.reject)
        self.save_button = QPushButton(_("Save Changes"))
        self.save_button.clicked.connect(self.save_changes)
        button_box.addButton(self.save_button, QDialogButtonBox.ActionRole)
        layout.addWidget(button_box)

    def _strip_side_bars(self, graph_view):
        """Hide the left/right bars (and their toggles), keeping the top bar."""
        try:
            for attr in ('chains_library', '_toolbar_widget'):
                holder = getattr(graph_view, attr, None)
                if holder is None:
                    continue
                holder.hide()
                for toggle_attr in ('_toggle_btn', '_toggle_btn_nodes'):
                    toggle = getattr(holder, toggle_attr, None)
                    if toggle is not None:
                        toggle.hide()
            # Re-run the layout so the top bar spans the freed space.
            graph_view.ui_components.layout_floating_bars()
        except Exception as e:
            logger.debug(f"Could not strip side bars: {e}")

    # -------------------------------------------------------------- loading
    def load_sequence(self):
        actions = self._read_actions()
        self._build_action_nodes(actions)

    def _sequence_path(self):
        """Absolute path of the sequence file this node points at, or ''."""
        try:
            config = self.sequence_node.get_sequence_config()
        except Exception:
            config = {}
        seq_file = (config or {}).get('sequence_file', '') or ''
        if not seq_file:
            return ''
        folder = getattr(self.parent_graph_view, 'sequences_folder', '') or ''
        return seq_file if os.path.isabs(seq_file) else os.path.join(
            folder, os.path.basename(seq_file))

    def _read_actions(self):
        """Read the sequence file's action list (empty on any failure)."""
        path = self._sequence_path()
        if not path or not os.path.exists(path):
            logger.warning(f"Sequence file not found: {path}")
            return []
        try:
            with open(path, 'r', encoding='utf-8') as handle:
                data = json.load(handle)
        except Exception as e:
            logger.error(f"Failed to read sequence {path}: {e}")
            return []
        if isinstance(data, dict):
            return data.get('actions', []) or []
        if isinstance(data, list):
            return data
        return []

    def _build_action_nodes(self, actions):
        """Create one ActionNode per step and wire them in order."""
        try:
            manager = self.graph_view.graph_manager
        except Exception:
            return
        previous = None
        for index, action in enumerate(actions):
            node = None
            try:
                node = manager.create_node(
                    'action.ActionNode',
                    name=f"Step {index + 1}",
                    pos=[index * _ACTION_GAP_X, 0.0],
                )
            except Exception as e:
                logger.debug(f"Action node creation failed at {index}: {e}")
            if node is None:
                continue
            try:
                node.set_action_data(0, index, action)
            except Exception:
                pass
            # Remember which step this node is so Save can rebuild the file.
            node._looper_action_data = action
            self._apply_action_preview(node, action)
            if previous is not None:
                try:
                    manager.connect_nodes(previous, node, 'output', 'input')
                except Exception:
                    pass
            previous = node
        # Show the ACTIONS as the content. The nested graph defaults to the
        # sequence view, which DISABLES every ActionNode (the red-X "disabled"
        # look, i.e. nothing usable). Switch to the action view so the recorded
        # steps are the enabled, visible content.
        try:
            self.graph_view.show_action_view()
        except Exception:
            try:
                for node in self.graph_view.graph_manager.get_all_nodes():
                    node.set_disabled(False)
            except Exception:
                pass

    def _apply_action_preview(self, node, action):
        """Use the step's clicked-element screenshot as its node glyph."""
        try:
            data = action.get('screenshot_data')
            if not data:
                return
            encoded = data.split(',', 1)[1] if ',' in data else data
            raw = base64.b64decode(encoded)
            pm = QPixmap()
            if not pm.loadFromData(raw):
                return
            node._looper_preview_pm = pm
            # Re-run the icon pass now so the thumbnail shows immediately.
            try:
                self.graph_view.node_button_manager._apply_node_icon(node)
            except Exception:
                pass
        except Exception:
            pass

    def save_changes(self):
        """Write the steps back to the sequence file.

        The visible order is left-to-right, so reordering = dragging nodes and
        the file is rewritten to match; steps whose node was removed are gone.
        """
        path = self._sequence_path()
        if not path:
            return
        try:
            nodes = [n for n in self.graph_view.graph_manager.get_all_nodes()
                     if hasattr(n, '_looper_action_data')]
            nodes.sort(key=lambda n: float(n.view.pos().x()))
            actions = [n._looper_action_data for n in nodes]
            metadata = {}
            try:
                with open(path, 'r', encoding='utf-8') as handle:
                    existing = json.load(handle)
                if isinstance(existing, dict):
                    metadata = existing.get('metadata', {}) or {}
            except Exception:
                pass
            metadata['total_actions'] = len(actions)
            with open(path, 'w', encoding='utf-8') as handle:
                json.dump({'metadata': metadata, 'actions': actions}, handle,
                          indent=2)
            logger.info(f"Saved sequence {path} with {len(actions)} actions")
        except Exception as e:
            logger.error(f"Failed to save sequence: {e}", exc_info=True)

    # ------------------------------------------------------------ chrome
    def toggle_maximize(self):
        if self.isMaximized():
            self.showNormal()
            if hasattr(self, 'maximize_button'):
                self.maximize_button.setIcon(self._max_icon(16))
        else:
            self.showMaximized()
            if hasattr(self, 'maximize_button'):
                self.maximize_button.setIcon(self._restore_icon(16))

    def _max_icon(self, size: int) -> QIcon:
        pix = QPixmap(size, size)
        pix.fill(QColor(0, 0, 0, 0))
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.Antialiasing)
        pen = painter.pen()
        pen.setColor(QColor(TEXT_COLOR))
        pen.setWidthF(2.0)
        painter.setPen(pen)
        margin = int(size * 0.18)
        painter.drawRoundedRect(margin, margin, size - 2 * margin,
                                size - 2 * margin, 3, 3)
        painter.end()
        return QIcon(pix)

    def _restore_icon(self, size: int) -> QIcon:
        pix = QPixmap(size, size)
        pix.fill(QColor(0, 0, 0, 0))
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.Antialiasing)
        pen = painter.pen()
        pen.setColor(QColor(TEXT_COLOR))
        pen.setWidthF(2.0)
        painter.setPen(pen)
        margin = int(size * 0.22)
        painter.drawRoundedRect(margin - 2, margin, size - 2 * margin,
                                size - 2 * margin, 3, 3)
        painter.drawRoundedRect(margin, margin - 2, size - 2 * margin,
                                size - 2 * margin, 3, 3)
        painter.end()
        return QIcon(pix)
