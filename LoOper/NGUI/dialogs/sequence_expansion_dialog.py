"""Overlay that expands a Sequence (or Web Sequence) node into its steps.

Mirrors the chain-import expansion: a nested graph view is hosted in a dialog
and the node's content is laid out there. For a sequence the content is its
recorded action list, shown as one ActionNode per step wired in order. The SAME
overlay serves a WEB sequence (`web_sequences/<session_file>`): its actions are
browser steps carrying DOM locators, labelled from the element they touch, so a
recorded web session is inspected, edited and saved exactly like a desktop one.

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


def _write_actions_file(path, actions):
    """Rewrite an actions file in place: new 'actions' + metadata.total_actions.

    Every OTHER top-level key is preserved: a web session also carries
    schema_version / mode / start_url (and a desktop sequence may carry its own
    extras), which a metadata+actions-only write would silently destroy.
    Returns the action count written.
    """
    base = {}
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            loaded = json.load(handle)
        if isinstance(loaded, dict):
            base = loaded
    except Exception:
        base = {}
    metadata = base.get('metadata')
    if not isinstance(metadata, dict):
        metadata = {}
    metadata['total_actions'] = len(actions)
    base['metadata'] = metadata
    base['actions'] = actions
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(base, handle, indent=2)
    return len(actions)


class SequenceExpansionDialog(ModernDialog):
    """Overlay showing a Sequence node's recorded actions as a graph."""

    def __init__(self, sequence_node, parent_graph_view, parent=None, kind=None):
        # ONE overlay serves both node types: a DESKTOP sequence (steps with
        # click screenshots) and a WEB sequence (browser actions carrying DOM
        # locators).  They differ only in the file they read and how an action
        # is labelled, so a web session becomes as editable as a desktop one
        # instead of needing a parallel dialog.
        self.kind = kind or self._node_kind(sequence_node)
        super().__init__(parent, help_topic="sequence-dialog",
                         title=_("Web Sequence") if self.kind == 'web'
                         else _("Sequence"))
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
        _kind_label = _("Web Sequence") if self.kind == 'web' else _("Sequence")
        header_label = QLabel(
            _("{kind}: {name}").format(kind=_kind_label, name=name))
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

    @staticmethod
    def _node_kind(node):
        """'web' for a Web Sequence node, 'desktop' for a desktop sequence."""
        ntype = getattr(node, '__identifier__', '') or ''
        return 'web' if ('web_sequence' in ntype or 'WebSequence' in ntype) \
            else 'desktop'

    def _source_path(self):
        """Absolute path of the actions file this node points at, or ''.

        A desktop sequence binds its file on 'sequence_file' under
        ``sequences_folder``; a web sequence binds 'session_file' under
        ``web_sequences_folder``.  Both files are the SAME shape (a metadata
        dict plus an 'actions' list), which is what lets one overlay edit both.
        """
        if self.kind == 'web':
            try:
                config = self.sequence_node.get_web_sequence_config()
            except Exception:
                config = {}
            file_field = (config or {}).get('session_file', '') or ''
            folder = getattr(
                self.parent_graph_view, 'web_sequences_folder', '') or ''
        else:
            try:
                config = self.sequence_node.get_sequence_config()
            except Exception:
                config = {}
            file_field = (config or {}).get('sequence_file', '') or ''
            folder = getattr(self.parent_graph_view, 'sequences_folder', '') or ''
        if not file_field:
            return ''
        return file_field if os.path.isabs(file_field) else os.path.join(
            folder, os.path.basename(file_field))

    def _read_actions(self):
        """Read the source file's action list (empty on any failure)."""
        path = self._source_path()
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
        created = []
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
            # A web action's OWN target is what identifies it (e.g. the stray
            # click to remove), so label it from the DOM locator instead of the
            # generic 'Action N: TYPE' the desktop shape produces.
            if self.kind == 'web':
                _label = self._web_action_label(index, action)
                try:
                    node.set_name(_label)
                except Exception:
                    pass
                try:
                    node.set_property('details', _label)
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
            created.append(node)
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
        # A web action carries NO recorded screenshot (unlike a desktop step),
        # so its preview is taken live from the workbench browser.
        if self.kind == 'web':
            self._apply_web_previews(created)

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

    @staticmethod
    def _web_action_label(index, action):
        """A human label for a web action: its type + the target it touches.

        For a click this is the element text/placeholder the step resolves to —
        exactly what lets the user spot an incidental recorded action (e.g. a
        click baked onto a page's typeahead history) and drop it.
        """
        def _target(loc):
            if not isinstance(loc, dict):
                return ''
            for key in ('text', 'placeholder', 'aria_label', 'name', 'id',
                        'link_text', 'alt_text', 'title', 'label_text',
                        'value'):
                v = ' '.join(str(loc.get(key) or '').split())
                if v:
                    return v[:48]
            return str(loc.get('tag') or '').strip()

        a = action or {}
        atype = str(a.get('type') or 'action')
        loc = a.get('locator')
        if atype == 'type':
            body = ' '.join(str(a.get('value') or '').split())[:40] \
                or _target(loc)
        elif atype == 'key':
            body = str(a.get('key') or '')
            state = str(a.get('state') or '').strip()
            body = f"{body} ({state})" if state else body
        elif atype == 'navigate':
            body = str(a.get('url') or '')[:48]
        elif atype == 'scroll':
            body = str(a.get('scroll') or '')[:24]
        else:
            body = _target(loc)
        return f"{index + 1}. {atype} {body}".strip()

    # Cap on live thumbnails per expansion: each one needs a viewport
    # screenshot, so a runaway session must never stall the dialog.
    _MAX_WEB_PREVIEWS = 40

    def _apply_web_previews(self, nodes):
        """Live element thumbnails for a web session's actions.

        A web action carries no recorded screenshot (unlike a desktop step), so
        the preview is taken NOW from the workbench browser — but only when one
        is already open and on the page, never by launching one.  For each
        action the element is re-resolved (shadow-aware, scrolled into view by
        the engine's own helper) and cropped out of a fresh viewport
        screenshot, so the crop always matches the pixels it came from.  No
        browser open -> the actions keep the generic type glyph; the label
        already says what each one does.
        """
        try:
            from player.web.session import (
                SHARED_WEB_SCOPE, existing_workbench_driver)
            from player.web.actions import deep_element_rect
            from player.web.events import Event
        except Exception as e:
            logger.debug(f"web preview unavailable: {e}")
            return
        try:
            driver = existing_workbench_driver(SHARED_WEB_SCOPE)
        except Exception:
            driver = None
        if driver is None:
            return
        attempts = 0
        for node in nodes:
            if attempts >= self._MAX_WEB_PREVIEWS:
                break
            action = getattr(node, '_looper_action_data', None)
            if not isinstance(action, dict) or not action.get('locator'):
                continue
            attempts += 1
            try:
                rect = deep_element_rect(driver, Event.from_dict(action))
                if not rect:
                    continue
                shot = QPixmap()
                if not shot.loadFromData(driver.get_screenshot_as_png()):
                    continue
                pm = self._crop_preview(shot, rect)
                if pm is None or pm.isNull():
                    continue
                node._looper_preview_pm = pm
                self.graph_view.node_button_manager._apply_node_icon(node)
            except Exception as e:
                logger.debug(f"web preview failed for a step: {e}")
                continue

    @staticmethod
    def _crop_preview(shot, rect, pad=6):
        """A padded crop of *shot* around a device-pixel [x, y, w, h] box.

        Clamped to the image so an element at the viewport edge still yields a
        usable thumbnail; a degenerate box yields None.
        """
        try:
            x, y, w, h = (int(v) for v in rect)
            if w <= 0 or h <= 0:
                return None
            x0, y0 = max(0, x - pad), max(0, y - pad)
            x1 = min(shot.width(), x + w + pad)
            y1 = min(shot.height(), y + h + pad)
            if x1 - x0 < 2 or y1 - y0 < 2:
                return None
            return shot.copy(x0, y0, x1 - x0, y1 - y0)
        except Exception:
            return None

    def save_changes(self):
        """Write the steps back to the source file.

        The visible order is left-to-right, so reordering = dragging nodes and
        the file is rewritten to match; steps whose node was removed are gone.
        """
        path = self._source_path()
        if not path:
            return
        try:
            nodes = [n for n in self.graph_view.graph_manager.get_all_nodes()
                     if hasattr(n, '_looper_action_data')]
            nodes.sort(key=lambda n: float(n.view.pos().x()))
            actions = [n._looper_action_data for n in nodes]
            count = _write_actions_file(path, actions)
            logger.info(
                f"Saved {'web ' if self.kind == 'web' else ''}sequence "
                f"{path} with {count} actions")
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
