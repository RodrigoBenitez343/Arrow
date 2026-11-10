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

Where the text goes: each section's title and note sit in the TOP-LEFT corner
INSIDE its frame, and every member node carries a small note just under it
saying what that node does — the same sentence the node dialog shows.  The
frame is the section's footprint; nothing is stacked on top of it, so the
annotation costs the chain no height at all.
"""

import logging

from NodeGraphQt.constants import Z_VAL_NODE
from PyQt5.QtCore import QObject, QRect, QRectF, QTimer, Qt
from PyQt5.QtGui import QBrush, QColor, QFont, QFontMetrics, QPainter, QPen
from PyQt5.QtWidgets import QGraphicsItem

logger = logging.getLogger(__name__)

#: Boxes sit under the nodes (Z_VAL_NODE) but above the pipes (-1); the text
#: sits with them so it never covers a node.
_BOX_Z = Z_VAL_NODE - 0.5
_TEXT_Z = Z_VAL_NODE - 0.45
_RING_Z = Z_VAL_NODE + 2.0

#: Padding a box leaves around its nodes.
_PAD = 18.0

_RING_COLOR = '#FACC15'
_REFRESH_MS = 300

#: Character limits for the two texts a box carries.
_NOTE_CHARS = 4000
_DOES_CHARS = 4000

#: Inset the section's title/note keeps from its frame's top-left corner.
_TITLE_INSET = 10.0
#: A section's header (title + note) never grows past this: a description that
#: long is elided rather than pushing the frame down the whole canvas.
_MAX_HEADER = 260.0
#: Column the per-node notes wrap at, and their gap under the node.
_NODE_NOTE_WIDTH = 190.0
_NODE_NOTE_GAP = 6.0
#: Extra height the frame reserves under a group for its nodes' notes.
_NODE_NOTE_ROOM = 54.0

#: Clearance kept between text and any node it is not describing.
_CLEARANCE = 14.0
#: A gap narrower than this is not a place to put words.
_MIN_GAP = 48.0
#: Text is never searched for further than this from its own section.
_MAX_LAUNCH = 260.0
#: Below this share of the note visible, a gap is a teaser, not a label.
_MIN_SHOWN = 0.5
#: Bounds on the column the note wraps at.
_MIN_WIDTH = 150.0
_MAX_WIDTH = 460.0
#: Horizontal breathing room the text keeps inside its box.
_TEXT_INSET = 12.0


def _distance(a, b):
    """Cheap separation between two rects: 0 when they touch, else the gap."""
    dx = max(0.0, max(a.left() - b.right(), b.left() - a.right()))
    dy = max(0.0, max(a.top() - b.bottom(), b.top() - a.bottom()))
    return (dx * dx + dy * dy) ** 0.5


class _BoxItem(QGraphicsItem):
    """A dashed, click-through FRAME around one section's nodes.

    One section can be drawn as several frames: a frame is a rectangle, so
    members that are not side by side are framed separately instead of in one
    rectangle that rounds whatever sits between them.
    """

    def __init__(self, hue='#22D3EE', ring=False):
        super().__init__()
        self._rects = []
        self._color = QColor(hue)
        self._ring = bool(ring)
        self.setFlag(QGraphicsItem.ItemIsSelectable, False)
        self.setFlag(QGraphicsItem.ItemIsFocusable, False)
        self.setAcceptedMouseButtons(Qt.NoButton)
        self.setZValue(_RING_Z if ring else _BOX_Z)
        self.setVisible(False)

    # -- geometry ------------------------------------------------------

    def set_rect(self, rect):
        self.set_rects([rect])

    def set_rects(self, rects):
        rects = [QRectF(rect) for rect in (rects or ())]
        rects = [rect for rect in rects if not rect.isEmpty()]
        if rects == self._rects:
            return
        self.prepareGeometryChange()
        self._rects = rects
        self.update()

    def rects(self):
        """The frames this item draws (a section can own more than one)."""
        return list(self._rects)

    def boundingRect(self):
        rect = QRectF()
        for part in self._rects:
            rect = part if rect.isEmpty() else rect.united(part)
        return rect.adjusted(-2.0, -2.0, 2.0, 2.0)

    # -- painting ------------------------------------------------------

    def paint(self, painter, option, widget=None):
        if not self._rects:
            return
        painter.setRenderHint(QPainter.Antialiasing, True)
        color = QColor(self._color)
        fill = QColor(color)
        if self._ring:
            fill.setAlpha(38)
            painter.setBrush(QBrush(fill))
            painter.setPen(QPen(color, 2.6))
            for rect in self._rects:
                painter.drawRoundedRect(rect, 8.0, 8.0)
            return
        fill.setAlpha(12)
        border = QColor(color)
        border.setAlpha(150)
        painter.setBrush(QBrush(fill))
        painter.setPen(QPen(border, 1.4, Qt.DashLine))
        for rect in self._rects:
            painter.drawRoundedRect(rect, 10.0, 10.0)


class _TextItem(QGraphicsItem):
    """Shared painting for a wrapped, clipped block of canvas prose.

    Every reader of a description gets the same treatment: the text wraps at
    the item's own width, is clipped to its rect, and — when the rect cannot
    hold it — ends with an ellipsis instead of bleeding over the nodes.
    """

    _TITLE_FONT_PX = 9.5
    _NOTE_FONT_PX = 8.5

    def __init__(self, hue='#22D3EE', z=_TEXT_Z):
        super().__init__()
        self._rect = QRectF()
        self._color = QColor(hue)
        self.setFlag(QGraphicsItem.ItemIsSelectable, False)
        self.setFlag(QGraphicsItem.ItemIsFocusable, False)
        self.setAcceptedMouseButtons(Qt.NoButton)
        self.setZValue(z)
        self.setVisible(False)

    def set_rect(self, rect):
        rect = QRectF(rect)
        if rect == self._rect:
            return
        self.prepareGeometryChange()
        self._rect = rect
        self.update()

    def boundingRect(self):
        return self._rect.adjusted(-2.0, -2.0, 2.0, 2.0)

    @classmethod
    def _title_font(cls):
        font = QFont()
        font.setPointSizeF(cls._TITLE_FONT_PX)
        font.setBold(True)
        return font

    @classmethod
    def _note_font(cls):
        font = QFont()
        font.setPointSizeF(cls._NOTE_FONT_PX)
        return font

    @classmethod
    def _wrapped_height(cls, text, width, title=False):
        if not text:
            return 0.0
        font = cls._title_font() if title else cls._note_font()
        return QFontMetrics(font).boundingRect(
            QRect(0, 0, int(max(1.0, width)), 10000),
            Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignTop, text).height()

    @classmethod
    def _elide(cls, text, width, rows, title=False):
        """*text* cut to *rows* lines, ending in '…' when it was cut."""
        if not text or rows <= 0:
            return ''
        font = cls._title_font() if title else cls._note_font()
        lead = QFontMetrics(font).lineSpacing()
        budget = rows * lead + 1
        if cls._wrapped_height(text, width, title) <= budget:
            return text
        words = text.split()
        lo, hi = 0, len(words)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if cls._wrapped_height(' '.join(words[:mid]) + ' …', width,
                                   title) <= budget:
                lo = mid
            else:
                hi = mid - 1
        return ' '.join(words[:lo]) + (' …' if lo else '…')

    @classmethod
    def _rows(cls, text, width, height, title=False):
        """Lines of *text* that fit a width x height box."""
        font = cls._title_font() if title else cls._note_font()
        lead = max(1, QFontMetrics(font).lineSpacing())
        return int(max(0.0, height) // lead)

    def _draw_block(self, painter, text, rect, title=False):
        """Draw *text* inside *rect*, elided if it cannot all fit."""
        if not text or rect.width() < 1 or rect.height() < 1:
            return 0.0
        font = self._title_font() if title else self._note_font()
        rows = self._rows(text, rect.width(), rect.height(), title)
        shown = self._elide(text, rect.width(), rows, title)
        if not shown:
            return 0.0
        color = QColor(self._color)
        if not title:
            color.setAlpha(215)
        painter.setFont(font)
        painter.setPen(QPen(color))
        painter.drawText(rect, Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignTop,
                         shown)
        return self._wrapped_height(shown, rect.width(), title)


class _LabelItem(_TextItem):
    """A section's title + note, pinned to its frame's top-left corner."""

    def __init__(self, title='', note='', hue='#22D3EE', anchor=None):
        super().__init__(hue)
        self._title = str(title or '')
        self._note = str(note or '')[:_NOTE_CHARS]
        #: The section this label describes (scene rect) and its node ids.
        self._anchor = QRectF(anchor) if anchor is not None else QRectF()
        self._own = frozenset()

    def set_anchor(self, rect, own=()):
        self._anchor = QRectF(rect) if rect is not None else QRectF()
        self._own = frozenset(str(n) for n in own or ())

    def head_height(self, width):
        """Height the title alone needs at *width* (0 when there is none)."""
        if not self._title:
            return 0.0
        return self._wrapped_height(self._title, width, title=True) + 4.0

    def note_height(self, width, text=None):
        """Height *text* (default: the note) needs wrapped at *width*."""
        return self._wrapped_height(
            self._note if text is None else text, width)

    def preferred_height(self, width):
        """The height that shows the whole note plus the title."""
        need = self.note_height(width) + self.head_height(width)
        return need if need > 0 else QFontMetrics(self._note_font()).height()

    def paint(self, painter, option, widget=None):
        if self._rect.isEmpty():
            return
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setClipRect(self._rect)
        left = self._rect.left()
        width = max(1.0, self._rect.width())
        top = self._rect.top()
        bottom = self._rect.bottom()
        if self._title:
            stamp = self._wrapped_height(self._title, width, title=True)
            used = self._draw_block(
                painter, self._title,
                QRectF(left, top, width, min(stamp, bottom - top)), title=True)
            top += used + 4.0
        if self._note and top < bottom:
            self._draw_block(painter, self._note,
                             QRectF(left, top, width, bottom - top))
        painter.setClipping(False)


class _NodeNoteItem(_TextItem):
    """One node's own sentence, drawn just under that node.

    The words are the SAME ones the node dialog shows (``node.description``,
    written by the Builder), so reading the box and opening the node never
    disagree.
    """

    def __init__(self, text='', hue='#22D3EE'):
        super().__init__(hue)
        self._text = ' '.join(str(text or '').split())[:_DOES_CHARS]

    def set_text(self, text):
        text = ' '.join(str(text or '').split())[:_DOES_CHARS]
        if text == self._text:
            return
        self._text = text
        self.update()

    def height_for(self, width=_NODE_NOTE_WIDTH):
        """The height this note needs at the strip's width."""
        return self._wrapped_height(self._text, width) or 0.0

    def paint(self, painter, option, widget=None):
        if self._rect.isEmpty() or not self._text:
            return
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setClipRect(self._rect)
        self._draw_block(painter, self._text, self._rect)
        painter.setClipping(False)


class FlowOverlay(QObject):
    """The Builder's annotations on one node graph (boxes + step ring)."""

    def __init__(self, node_graph, parent=None):
        super().__init__(parent)
        #: The live ``NodeGraph`` (or a callable returning it: the canvas is
        #: built after this object and must never be captured as None).
        self._graph = node_graph
        #: ``[(section, frame, label, {node_id: note})]`` — one entry per
        #: section, each carrying the box, its corner label and a note item for
        #: every member node.
        self._boxes = []
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

    def _node_rects(self):
        """``[(id, rect)]`` of every node on the canvas (empty when unknown)."""
        graph = self._graph_ref()
        list_nodes = getattr(graph, 'all_nodes', None)
        if not callable(list_nodes):
            return []
        try:
            rows = []
            for node in list_nodes() or ():
                view = getattr(node, 'view', None)
                if view is None:
                    continue
                rows.append((str(getattr(node, 'id', '') or ''),
                             view.sceneBoundingRect()))
            return rows
        except Exception:
            logger.debug('node rects unavailable', exc_info=True)
            return []

    # -- boxes ---------------------------------------------------------

    def show_sections(self, sections, node_texts=None):
        """Draw the section boxes; replaces the previous ones.

        ``node_texts`` maps a node id to the sentence that node does.  Every
        member node of every section then gets its own small note inside the
        frame, so the canvas explains each step as well as each part.  A node
        with no sentence gets no note (an empty box would only add noise).
        """
        self.clear_boxes()
        scene = self._live_scene()
        texts = {str(k): str(v or '') for k, v in (node_texts or {}).items()}
        for section in (sections or []):
            hue = section.get('hue') or '#22D3EE'
            frame = _BoxItem(hue=hue)
            label = _LabelItem(title=section.get('title'),
                               note=section.get('note'), hue=hue)
            scene.addItem(frame)
            scene.addItem(label)
            notes = {}
            for node_id in section.get('nodes') or ():
                text = texts.get(str(node_id)) or ''
                if not text.strip():
                    continue
                note = _NodeNoteItem(text=text, hue=hue)
                scene.addItem(note)
                notes[str(node_id)] = note
            self._boxes.append((section, frame, label, notes))
        self._refit_boxes()
        if self._boxes:
            self._timer.start()

    def _section_islands(self, section, canvas_rects=None):
        """The section's footprint, cut into islands of member nodes.

        Member rects only share an island while NO other node of the canvas
        lies inside it.  A single union of scattered members is a frame around
        nodes that do not belong to the section at all (an old walk order, a
        pool placed three screens away), which reads as one box holding
        unrelated steps.
        """
        members = []
        for node_id in section.get('nodes') or ():
            node_rect = self._view_rect(node_id)
            if node_rect is not None:
                members.append((str(node_id), node_rect))
        if not members:
            return []
        own = {node_id for node_id, _rect in members}
        rows = (self._node_rects() if canvas_rects is None
                else canvas_rects)
        strangers = [rect.center() for node_id, rect in rows
                     if node_id not in own]
        islands = []
        for _node_id, node_rect in sorted(
                members, key=lambda row: (row[1].top(), row[1].left())):
            for index, island in enumerate(islands):
                grown = island.united(node_rect)
                if not any(grown.contains(point) for point in strangers):
                    islands[index] = grown
                    break
            else:
                islands.append(QRectF(node_rect))
        return islands

    def _refit_boxes(self):
        """Place the frame, its corner label and every member node's note.

        Geometry follows the live nodes (the timer calls this while a box is
        on screen), so dragging a node re-fits all three together.
        """
        if not self._boxes:
            return
        canvas_rects = self._node_rects()
        for section, frame, label, notes in self._boxes:
            islands = self._section_islands(section, canvas_rects)
            if not islands:
                frame.setVisible(False)
                label.setVisible(False)
                for note in notes.values():
                    note.setVisible(False)
                continue
            rect = islands[0]
            # The frame reserves a HEADER strip above the group (the section's
            # own title + note) and a strip under it (each member's sentence):
            # both live inside the frame without ever covering a node.
            head = self._header_height(label, rect)
            parts = []
            for index, island in enumerate(islands):
                top = _PAD + (head if index == 0 else 0.0)
                parts.append(island.adjusted(-_PAD, -top, _PAD,
                                             _PAD + _NODE_NOTE_ROOM))
            frame.set_rects(parts)
            frame.setVisible(True)
            label.set_anchor(rect, section.get('nodes') or ())
            label.set_rect(self._corner_rect(label, rect, head))
            label.setVisible(True)
            for node_id, note in notes.items():
                node_rect = self._view_rect(node_id)
                if node_rect is None:
                    note.setVisible(False)
                    continue
                note.set_rect(self._node_note_rect(node_rect))
                note.setVisible(bool(note.height_for()))

    # -- label geometry ------------------------------------------------

    def _label_width(self, rect):
        """The column the section's title + note wrap at."""
        return max(_MIN_WIDTH, rect.width() + 2 * (_PAD - _TITLE_INSET))

    def _header_height(self, label, rect):
        """Height the frame reserves ABOVE the group for its title + note.

        Bounded by ``_MAX_HEADER``: a long note is elided inside the header
        (ending in "…") instead of stretching the box down the canvas.
        """
        want = label.preferred_height(self._label_width(rect)) + 2 * _TITLE_INSET
        return min(want, _MAX_HEADER)

    def _corner_rect(self, label, rect, head):
        """The label's rect: the frame's TOP-LEFT corner, inside it.

        ``rect`` is the group's footprint; the frame reaches ``_PAD`` outside
        it plus *head* above it, which is the room the label gets.  Anchoring
        to that corner keeps the section's title where a reader looks first.
        """
        left = rect.left() - _PAD + _TITLE_INSET
        top = rect.top() - _PAD - head + _TITLE_INSET
        width = self._label_width(rect)
        return QRectF(left, top, width, max(14.0, head - 2 * _TITLE_INSET))

    def _node_note_rect(self, node_rect):
        """The strip under one node where its own sentence is drawn."""
        return QRectF(node_rect.left(),
                      node_rect.bottom() + _NODE_NOTE_GAP,
                      max(_NODE_NOTE_WIDTH, node_rect.width()),
                      max(1.0, self._note_room()))

    @staticmethod
    def _note_room():
        """Height reserved under a node for its sentence."""
        return _NODE_NOTE_ROOM - _NODE_NOTE_GAP

    # -- walk highlight ------------------------------------------------

    def clear_boxes(self):
        scene = self._scene()
        for _section, frame, label, notes in self._boxes:
            for item in [frame, label] + list(notes.values()):
                if scene is not None and item.scene() is scene:
                    scene.removeItem(item)
        self._boxes = []
        if not self._rings:
            self._timer.stop()

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
