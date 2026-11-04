"""flow_overlay.py — what the Builder DRAWS on the canvas.

Two visual aids, nothing else:

* **boxes** — a titled, coloured frame around the nodes of one section of the
  chain (the sections come from the chain's own ``flow`` metadata, or from the
  phases of the walk when the chain was never described);
* **the walk highlight** — a ring over the node the Builder is explaining,
  moved step by step during a smoke test.

Both are plain ``QGraphicsItem``s added to the node graph's scene: they carry
NO ports, are not saved into the chain and are dropped when the graph is
rebuilt.  They are annotations, never chain content.

An important shape note: the box geometry follows the live nodes.  A 300 ms
timer re-fits every box while one is on screen, so dragging a node does not
leave a frame around empty space.
"""

import logging

from NodeGraphQt.constants import Z_VAL_NODE
from PyQt5.QtCore import QObject, QRect, QRectF, QTimer, Qt
from PyQt5.QtGui import QBrush, QColor, QFont, QFontMetrics, QPainter, QPen
from PyQt5.QtWidgets import QGraphicsItem

logger = logging.getLogger(__name__)

#: Boxes sit under the nodes (Z_VAL_NODE) but above the pipes (-1).
_BOX_Z = Z_VAL_NODE - 0.5
_RING_Z = Z_VAL_NODE + 2.0

#: Padding a box leaves around its nodes.  The top band carries the title and
#: the wrapped NOTE, so its height is measured per box (``_BoxItem.head_height``)
#: instead of being a constant.
_PAD_X = 18.0
_MIN_BAND = 30.0
_PAD_BOTTOM = 18.0

_RING_COLOR = '#FACC15'
_REFRESH_MS = 300


#: Characters of a section note a box draws.  The note is the description, not
#: the transcript: past this the band would cover the canvas it annotates.
_NOTE_CHARS = 800


class _BoxItem(QGraphicsItem):
    """A rounded frame with a title band; geometry set by the overlay.

    The band carries the section's title AND its full ``note``, wrapped: the
    note is the description of what that part of the chain does, so eliding it
    to one line left every box a headline with no body.
    """

    def __init__(self, title='', note='', hue='#22D3EE', ring=False):
        super().__init__()
        self._rect = QRectF()
        self._title = str(title or '')
        self._note = str(note or '')[:_NOTE_CHARS]
        self._color = QColor(hue)
        self._ring = bool(ring)
        self.setFlag(QGraphicsItem.ItemIsSelectable, False)
        self.setFlag(QGraphicsItem.ItemIsFocusable, False)
        self.setAcceptedMouseButtons(Qt.NoButton)
        self.setZValue(_RING_Z if ring else _BOX_Z)
        self.setVisible(False)

    # -- geometry ------------------------------------------------------

    def set_rect(self, rect):
        rect = QRectF(rect)
        if rect == self._rect:
            return
        self.prepareGeometryChange()
        self._rect = rect
        self.update()

    def boundingRect(self):
        return self._rect.adjusted(-2.0, -2.0, 2.0, 2.0)

    # -- text ----------------------------------------------------------

    @staticmethod
    def _title_font():
        font = QFont()
        font.setPointSizeF(9.5)
        font.setBold(True)
        return font

    @staticmethod
    def _note_font():
        font = QFont()
        font.setPointSizeF(8.5)
        return font

    def head_height(self, width):
        """The top band this box needs at *width* (title + wrapped note)."""
        inner = max(60, int(width - 20))
        height = QFontMetrics(self._title_font()).height() + 4.0
        if self._note:
            wrapped = QFontMetrics(self._note_font()).boundingRect(
                QRect(0, 0, inner, 10000),
                Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignTop, self._note)
            height += wrapped.height() + 4.0
        return max(_MIN_BAND, height + 8.0)

    # -- painting ------------------------------------------------------

    def paint(self, painter, option, widget=None):
        if self._rect.isEmpty():
            return
        painter.setRenderHint(QPainter.Antialiasing, True)
        color = QColor(self._color)
        if self._ring:
            fill = QColor(color)
            fill.setAlpha(38)
            painter.setBrush(QBrush(fill))
            painter.setPen(QPen(color, 2.6))
            painter.drawRoundedRect(self._rect, 8.0, 8.0)
            return

        fill = QColor(color)
        fill.setAlpha(16)
        border = QColor(color)
        border.setAlpha(170)
        painter.setBrush(QBrush(fill))
        painter.setPen(QPen(border, 1.4, Qt.DashLine))
        painter.drawRoundedRect(self._rect, 10.0, 10.0)
        if not (self._title or self._note):
            return
        left = self._rect.left() + 10.0
        width = max(0.0, self._rect.width() - 20.0)
        top = self._rect.top() + 5.0
        title_font = self._title_font()
        title_height = QFontMetrics(title_font).height()
        if self._title:
            painter.setFont(title_font)
            painter.setPen(QPen(QColor(color)))
            text = QFontMetrics(title_font).elidedText(
                self._title, Qt.ElideRight, int(width))
            painter.drawText(QRectF(left, top, width, title_height),
                             Qt.AlignLeft | Qt.AlignVCenter, text)
            top += title_height + 4.0
        if self._note:
            note_color = QColor(color)
            note_color.setAlpha(215)
            painter.setFont(self._note_font())
            painter.setPen(QPen(note_color))
            painter.drawText(
                QRectF(left, top, width,
                       max(0.0, self._rect.bottom() - top)),
                Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignTop, self._note)


class FlowOverlay(QObject):
    """The Builder's annotations on one node graph (boxes + step ring)."""

    def __init__(self, node_graph, parent=None):
        super().__init__(parent)
        #: The live ``NodeGraph`` (or a callable returning it: the canvas is
        #: built after this object and must never be captured as None).
        self._graph = node_graph
        self._boxes = []            # [(section, _BoxItem)]
        self._rings = []            # [_BoxItem]
        self._timer = QTimer(self)
        self._timer.setInterval(_REFRESH_MS)
        self._timer.timeout.connect(self._refit_boxes)

    # -- scene plumbing ------------------------------------------------

    def _graph_ref(self):
        graph = self._graph
        return graph() if callable(graph) else graph

    def _scene(self):
        graph = self._graph_ref()
        if graph is None:
            return None
        return graph.scene()

    def _live_scene(self):
        """The scene, or raise: drawing needs a canvas that exists."""
        scene = self._scene()
        if scene is None:
            raise RuntimeError(
                'the node graph has no scene yet — the annotations cannot be '
                'drawn on a canvas that is not built')
        return scene

    def _view_rect(self, node_id):
        """The node's rect in scene coordinates (None when it is gone)."""
        graph = self._graph_ref()
        if graph is None:
            return None
        node = graph.get_node_by_id(str(node_id))
        if node is None:
            return None
        view = getattr(node, 'view', None)
        if view is None:
            return None
        return view.sceneBoundingRect()

    # -- boxes ---------------------------------------------------------

    def show_sections(self, sections):
        """Draw one box per section; replaces the previous boxes."""
        self.clear_boxes()
        scene = self._live_scene()
        for section in (sections or []):
            item = _BoxItem(title=section.get('title'),
                            note=section.get('note'),
                            hue=section.get('hue') or '#22D3EE')
            scene.addItem(item)
            self._boxes.append((section, item))
        self._refit_boxes()
        if self._boxes:
            self._timer.start()

    def _refit_boxes(self):
        for section, item in self._boxes:
            rect = QRectF()
            for node_id in section.get('nodes') or ():
                node_rect = self._view_rect(node_id)
                if node_rect is None:
                    continue
                rect = node_rect if rect.isEmpty() else rect.united(node_rect)
            if rect.isEmpty():
                item.setVisible(False)
                continue
            # The band grows with the note, so the text is never drawn over the
            # nodes it describes.
            band = item.head_height(rect.width() + 2 * _PAD_X)
            item.set_rect(rect.adjusted(-_PAD_X, -band, _PAD_X, _PAD_BOTTOM))
            item.setVisible(True)

    def clear_boxes(self):
        scene = self._scene()
        for _section, item in self._boxes:
            if scene is not None and item.scene() is scene:
                scene.removeItem(item)
        self._boxes = []
        if not self._rings:
            self._timer.stop()

    # -- walk highlight ------------------------------------------------

    def highlight(self, node_ids, hue=_RING_COLOR, centre=True):
        """Ring the given nodes; scrolls the canvas when they are off screen."""
        self.clear_highlights()
        scene = self._live_scene()
        rect = QRectF()
        for node_id in (node_ids or []):
            node_rect = self._view_rect(node_id)
            if node_rect is None:
                raise RuntimeError(
                    'node %r is not on the canvas any more — the walkthrough '
                    'was stopped by an edit' % node_id)
            item = _BoxItem(hue=hue, ring=True)
            item.set_rect(node_rect.adjusted(-6.0, -6.0, 6.0, 6.0))
            item.setVisible(True)
            scene.addItem(item)
            self._rings.append(item)
            rect = node_rect if rect.isEmpty() else rect.united(node_rect)
        if centre and not rect.isEmpty():
            self._ensure_visible(rect)

    def _ensure_visible(self, rect):
        """Pan only when the rect is outside the visible viewport."""
        try:
            viewer = self._graph_ref().viewer()
            viewport = viewer.mapToScene(viewer.viewport().rect()).boundingRect()
            if not viewport.contains(rect):
                viewer.centerOn(rect.center())
        except Exception:
            logger.debug('canvas pan skipped', exc_info=True)

    def clear_highlights(self):
        scene = self._scene()
        for item in self._rings:
            if scene is not None and item.scene() is scene:
                scene.removeItem(item)
        self._rings = []
        if not self._boxes:
            self._timer.stop()

    # -- teardown ------------------------------------------------------

    def clear(self):
        """Drop every annotation (a new chain, or the user asked for it)."""
        self.clear_highlights()
        self.clear_boxes()
