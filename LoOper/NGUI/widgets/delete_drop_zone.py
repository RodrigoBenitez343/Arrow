"""Floating delete drop zone for the graph canvas.

A rounded square that slides up from the bottom edge of the canvas when a drag
(from the left library bar, or a node being dragged in the graph) comes close
to it. While a draggable hovers it, the square lights up with a red fog.

What a drop does:
* a library card (a sequence / chain / web-session file, or a shared LLM/Code/
  Context library node) is deleted from DISK;
* a node dragged from the graph is deleted from the GRAPH only.

Graph node dragging is NOT a Qt drag-and-drop (NodeGraphQt moves the item with
plain mouse events), so the zone is summoned / dropped-on through a scene mouse
watcher in addition to being a normal drop target for library QDrags.
"""

import json
import logging
import os

from PyQt5.QtCore import (QAbstractAnimation, QEasingCurve, QEvent, QObject,
                          QPoint, QPropertyAnimation, QRect, Qt)
from PyQt5.QtGui import QColor, QCursor
from PyQt5.QtWidgets import (QDialog, QGraphicsDropShadowEffect, QLabel,
                             QVBoxLayout, QWidget)

from ..constants import ACCENT_COLOR, DANGER_COLOR, FLOAT_SHADOW_BLUR, RADIUS_LG
from ..i18n import _
from ..icons import tabler_qicon

logger = logging.getLogger(__name__)

ZONE_SIZE = 76            # the rounded square's side
ZONE_MARGIN = 14          # gap from the canvas bottom edge when shown
ZONE_HIDDEN_EXTRA = 48    # how far below the edge it rests while hidden
SUMMON_MARGIN = 140       # how close to the bottom a drag must be to summon it
HYSTERESIS = 40           # extra distance before hiding (stops boundary flicker)
ICON_PX = 30


class DeleteDropZone(QWidget):
    """Rounded square drop target: deletes library files and graph nodes."""

    # Drag mime types accepted by the zone; these mirror the formats set by the
    # sequence/chain/web-sequence library cards when a drag starts.
    _DROP_FORMATS = (
        'application/x-looper-sequence-file',
        'application/x-looper-chain-file',
        'application/x-looper-web-session-file',
    )
    # LLM/Code/Context library cards drag a JSON config referencing a node
    # entry inside a chain file; those drops are delegated to the registered
    # node drop handler instead of a plain file removal.
    _NODE_CONFIG_FORMATS = (
        'application/x-looper-llm-config',
        'application/x-looper-context-config',
        'application/x-looper-code-config',
    )
    _NODE_CONFIG_KIND = {
        'application/x-looper-llm-config': 'llm',
        'application/x-looper-context-config': 'context',
        'application/x-looper-code-config': 'code',
    }
    _NODE_CONFIG_KEY = {
        'application/x-looper-llm-config': 'llm_node_data',
        'application/x-looper-context-config': 'context_node_data',
        'application/x-looper-code-config': 'code_node_data',
    }

    def __init__(self, graph_view, parent=None):
        super().__init__(parent if parent is not None else graph_view)
        self.graph_view = graph_view
        self._node_drop_handler = None
        self._hot = False
        self._shown = False

        self.setObjectName("deleteDropZone")
        self.setFixedSize(ZONE_SIZE, ZONE_SIZE)
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(_("Drop here to delete"))
        self.setStyleSheet(
            f"QWidget#deleteDropZone {{"
            f" background-color: rgba(20, 22, 26, 235);"
            f" border: 1px solid rgba(255, 255, 255, 0.10);"
            f" border-radius: {RADIUS_LG}px; }}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._icon = QLabel(self)
        self._icon.setAlignment(Qt.AlignCenter)
        self._icon.setStyleSheet("background: transparent; border: none;")
        layout.addWidget(self._icon)
        self._paint_icon(False)

        # Soft float shadow (cyan, like the other floating overlays); it turns
        # into a red fog while a draggable hovers the zone.
        self._shadow = QGraphicsDropShadowEffect(self)
        self._shadow.setOffset(0, 0)
        self.setGraphicsEffect(self._shadow)
        self._apply_shadow(False)

        # Slide animation on the widget position.
        self._anim = QPropertyAnimation(self, b"pos", self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._anim.finished.connect(self._on_anim_done)

        self.hide()

    # --------------------------------------------------------------- visuals
    def _paint_icon(self, hot):
        color = DANGER_COLOR if hot else ACCENT_COLOR
        self._icon.setPixmap(tabler_qicon("TRASH", ICON_PX, color).pixmap(ICON_PX, ICON_PX))

    def _apply_shadow(self, hot):
        color = QColor(DANGER_COLOR if hot else ACCENT_COLOR)
        color.setAlpha(220 if hot else 60)
        self._shadow.setColor(color)
        self._shadow.setBlurRadius(36 if hot else FLOAT_SHADOW_BLUR)

    def set_hot(self, hot):
        """Red fog on while a draggable hovers the zone, off otherwise."""
        hot = bool(hot)
        if hot == self._hot:
            return
        self._hot = hot
        # Freeze mid-slide the instant a drag lands on us: a drop target that
        # keeps MOVING under the cursor makes Qt thrash between DragEnter and
        # DragLeave, which reads as flicker.
        if hot:
            try:
                if self._anim.state() == QAbstractAnimation.Running:
                    self._anim.setCurrentTime(self._anim.duration())
            except Exception:
                pass
        self._apply_shadow(hot)
        self._paint_icon(hot)

    # -------------------------------------------------------------- geometry
    def _shown_pos(self, parent_h):
        return QPoint((self.parentWidget().width() - self.width()) // 2,
                      parent_h - self.height() - ZONE_MARGIN)

    def _hidden_pos(self, parent_h):
        return QPoint((self.parentWidget().width() - self.width()) // 2,
                      parent_h + ZONE_HIDDEN_EXTRA)

    def slide_in(self):
        if self._shown:
            return
        parent = self.parentWidget()
        if parent is None:
            return
        self._shown = True
        self.move(self._hidden_pos(parent.height()))
        self.show()
        self.raise_()
        self._animate_to(self._shown_pos(parent.height()))

    def slide_out(self):
        self.set_hot(False)
        if not self._shown:
            return
        self._shown = False
        parent = self.parentWidget()
        if parent is None:
            return
        self._animate_to(self._hidden_pos(parent.height()))

    def reposition(self):
        """Snap to the correct resting spot (used on canvas resize)."""
        parent = self.parentWidget()
        if parent is None:
            return
        target = self._shown_pos(parent.height()) if self._shown else self._hidden_pos(parent.height())
        self._anim.stop()
        self.move(target)

    def _animate_to(self, pos):
        self._anim.stop()
        self._anim.setStartValue(self.pos())
        self._anim.setEndValue(pos)
        self._anim.start()

    def _on_anim_done(self):
        if not self._shown:
            self.hide()

    # ----------------------------------------------------------- drop target
    def set_node_drop_handler(self, handler):
        """Register the handler for deleting shared library nodes."""
        self._node_drop_handler = handler

    def _accepts(self, mime):
        if mime is None:
            return False
        return any(mime.hasFormat(f) for f in self._DROP_FORMATS + self._NODE_CONFIG_FORMATS)

    def dragEnterEvent(self, event):
        if self._accepts(event.mimeData()):
            event.acceptProposedAction()
            self.set_hot(True)
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if self._accepts(event.mimeData()):
            event.acceptProposedAction()
            self.set_hot(True)
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.set_hot(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self.set_hot(False)
        handled = self._handle_drop(event)
        if handled:
            event.acceptProposedAction()
        else:
            event.ignore()
        # The drag is finished — retract (the watcher only sees drops that land
        # on the canvas, not the ones that land on us).
        self.slide_out()

    def _handle_drop(self, event):
        mime = event.mimeData()
        cfg_format = next(
            (f for f in self._NODE_CONFIG_FORMATS if mime.hasFormat(f)), None
        )
        if cfg_format:
            if not self._node_drop_handler:
                return False
            try:
                payload = json.loads(str(mime.data(cfg_format), 'utf-8'))
                return bool(self._node_drop_handler(
                    self._NODE_CONFIG_KIND[cfg_format],
                    payload.get('chain_file', ''),
                    payload.get(self._NODE_CONFIG_KEY[cfg_format], {}) or {},
                ))
            except Exception as e:
                logger.error(f"Error processing library node drop: {e}")
            return False

        file_format = next((f for f in self._DROP_FORMATS if mime.hasFormat(f)), None)
        if not file_format:
            return False
        try:
            file_path = str(mime.data(file_format), 'utf-8')
            if not os.path.exists(file_path):
                return False
            from .collapsible_toolbar import ModernDeleteDialog
            dialog = ModernDeleteDialog(self, os.path.basename(file_path))
            if dialog.exec_() == QDialog.Accepted:
                os.remove(file_path)
                return True
        except Exception as e:
            logger.error(f"Error processing file drop in delete zone: {e}")
        return False


class _DeleteDragWatcher(QObject):
    """Summons / drops-on the delete zone for both drag kinds.

    * Library QDrags arrive as DragEnter/DragMove/Drop events on the
      viewer/viewport.
    * Graph node drags are plain mouse events; the press is only counted when it
      landed on a node (not on a port), so dragging a connection never arms the
      zone.
    """

    def __init__(self, graph_view, zone):
        super().__init__(graph_view)
        self.gv = graph_view
        self.zone = zone
        self._node_drag = False

    # ---------------------------------------------------------------- helpers
    def _viewer(self):
        try:
            return self.gv.node_graph.viewer()
        except Exception:
            return None

    def _view_global_rect(self):
        viewer = self._viewer()
        if viewer is None:
            return None
        try:
            return QRect(viewer.mapToGlobal(QPoint(0, 0)), viewer.size())
        except Exception:
            return None

    def _zone_global_rect(self):
        return QRect(self.zone.mapToGlobal(QPoint(0, 0)), self.zone.size())

    def _over_zone(self, global_pos):
        return self.zone._shown and self._zone_global_rect().contains(global_pos)

    def _update_visibility(self, global_pos):
        """Show the zone while a drag is near the bottom (or already on it).

        Visibility only (the zone's own dragEnter/Move/Leave drive the red fog
        while it is the drop target, so the two never fight). A hysteresis band
        stops the zone thrashing in/out when the cursor sits on the boundary.
        """
        if self._over_zone(global_pos):
            self.zone.slide_in()
            return
        rect = self._view_global_rect()
        if rect is None:
            return
        if not (rect.left() <= global_pos.x() <= rect.right()):
            self.zone.slide_out()
            return
        dist = rect.bottom() - global_pos.y()
        if dist <= SUMMON_MARGIN:
            self.zone.slide_in()
        elif dist > SUMMON_MARGIN + HYSTERESIS:
            self.zone.slide_out()
        # else: inside the hysteresis band — keep the current state.

    def _press_on_node(self):
        viewer = self._viewer()
        if viewer is None:
            return False
        try:
            vp = viewer.viewport()
            scene_pos = viewer.mapToScene(vp.mapFromGlobal(QCursor.pos()))
        except Exception:
            return False
        try:
            items = viewer.scene().items(scene_pos)
        except Exception:
            return False
        for it in items:
            name = type(it).__name__
            if name in ('PortItem', 'CustomPortItem'):
                return False      # a port press starts a connection, not a move
            if name == 'NodeItem':
                return True
        return False

    def _delete_graph_nodes(self):
        try:
            if hasattr(self.gv, 'request_delete_selected'):
                self.gv.request_delete_selected()
            elif hasattr(self.gv, 'delete_selected_nodes'):
                self.gv.delete_selected_nodes()
        except Exception as e:
            logger.debug(f"Delete-zone node removal failed: {e}")

    # ------------------------------------------------------------ event filter
    def eventFilter(self, obj, event):
        try:
            et = event.type()
            if et in (QEvent.DragEnter, QEvent.DragMove, QEvent.DragLeave):
                # Library QDrag passing over the viewer/viewport. Position only:
                # a DragLeave here means the drag moved onto the zone (a sibling
                # widget), so hide only if the cursor really left the bottom.
                self._update_visibility(QCursor.pos())
            elif et == QEvent.Drop:
                self.zone.slide_out()
            elif et == QEvent.MouseButtonPress:
                self._node_drag = self._press_on_node()
            elif et == QEvent.MouseMove:
                if self._node_drag and (event.buttons() & Qt.LeftButton):
                    pos = QCursor.pos()
                    self._update_visibility(pos)
                    self.zone.set_hot(self._over_zone(pos))
            elif et == QEvent.MouseButtonRelease:
                if self._node_drag:
                    over = self._over_zone(QCursor.pos())
                    self._node_drag = False
                    self.zone.slide_out()
                    if over:
                        self._delete_graph_nodes()
        except Exception as e:
            logger.debug(f"Delete-zone watcher error: {e}")
        return False


def install_delete_drop_zone(graph_view):
    """Create the delete zone over the canvas and wire its drag detection.

    Returns the zone, or None if the graph engine is not ready yet.
    """
    try:
        zone = DeleteDropZone(graph_view, parent=graph_view)
        zone.set_node_drop_handler(
            getattr(graph_view.chains_library, 'delete_shared_node', None)
            if getattr(graph_view, 'chains_library', None) is not None else None
        )
        watcher = _DeleteDragWatcher(graph_view, zone)
        targets = []
        try:
            viewer = graph_view.node_graph.viewer()
            targets.append(viewer)
            targets.append(viewer.viewport())
            targets.append(viewer.scene())
        except Exception:
            pass
        targets.append(graph_view)
        for t in targets:
            if t is not None:
                try:
                    t.installEventFilter(watcher)
                except Exception:
                    pass
        graph_view._delete_zone = zone
        graph_view._delete_zone_watcher = watcher
        logger.info("Delete drop zone installed")
        return zone
    except Exception as e:
        logger.error(f"Could not install delete drop zone: {e}")
        return None
