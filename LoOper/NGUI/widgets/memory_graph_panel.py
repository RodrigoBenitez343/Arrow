"""memory_graph_panel.py — knowledge-graph view of the recorded activity memory.

Hosted inside the agent overlay (header "G" button), mirroring the Scheduler
panel swap.  The graph is a pure VIEW over ``player/agentic_ops/run_memory``:
DAY nodes group RUN episodes and CHAT turns; RUN->CHAIN edges mark every chain
that actually executed inside a run — recurring chains read as knowledge hubs.
Clicking a node shows its recorded events (the audit trail); nothing that was
not recorded gets a node.

ponytail: two deterministic layouts, no physics library — left-to-right
columns (default: day -> its runs+chats -> chains, the way time reads) and
a radial variant (hub chain centred, each date an angular segment).
"""

import logging
import math
from datetime import datetime

from PyQt5.QtCore import QLineF, QPointF, QRectF, Qt, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen, QPolygonF
from PyQt5.QtWidgets import (
    QComboBox,
    QFrame,
    QGraphicsEllipseItem,
    QGraphicsLineItem,
    QGraphicsPathItem,
    QGraphicsPolygonItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from ..constants import ACCENT_COLOR, GRAPH_PLANE, RED_PRIMARY, TEXT_COLOR
from ..i18n import _

logger = logging.getLogger(__name__)

# ── palette ──
_COL_DAY = QColor(255, 255, 255, 90)
_COL_RUN_OK = QColor(ACCENT_COLOR)
_COL_RUN_FAILED = QColor(RED_PRIMARY)
_COL_RUN_STOPPED = QColor("#FFA726")
_RUN_COLORS = {'ok': _COL_RUN_OK, 'failed': _COL_RUN_FAILED,
               'stopped': _COL_RUN_STOPPED}
_COL_CHAIN = QColor("#4DB6FF")
_COL_CHAT_USER = QColor(255, 255, 255, 120)
_COL_CHAT_AGENT = QColor(0, 224, 184, 150)
_COL_LABEL = QColor(TEXT_COLOR)
_COL_LABEL_DIM = QColor(255, 255, 255, 130)
_EDGE_DAY = QColor(255, 255, 255, 60)      # date -> its runs (dashed)
_EDGE_DAY_CHAT = QColor(255, 255, 255, 32)  # date -> its chats (dotted)
_EDGE_CHAT_RUN = QColor(0, 224, 184, 110)   # chat turn -> the run it produced

# ── layout constants ──
# Left->right columns (default): time reads down-scrolling per day block,
# structure to the right.  DAY | the day's runs+chats | CHAINS.
_COL_DAY_X = 60.0
_COL_EVENT_X = 250.0
_COL_CHAIN_X = 640.0
_ROW_H = 34.0
_MIN_COL_H = 70.0
# Radial alternative: hub at the centre, each date an angular segment
# (see _layout_radial).
_R_RUN = 190.0
_R_CHAIN = 360.0
_R_CHAT = 490.0
_R_DAY = 580.0

_PANEL_QSS = (
    "QFrame#memoryGraphPanel { background-color: transparent; border: 0px; }"
    f"QLabel {{ color: {TEXT_COLOR}; }}"
)


def _radius(kind, node):
    if kind == 'day':
        return 22.0
    if kind == 'run':
        return 14.0
    if kind == 'chat':
        return 7.5
    return 10.0 + min(14.0, 2.0 * math.sqrt(max(1, int(node.get('events') or 0))))


def _hhmm(ts):
    try:
        return datetime.fromtimestamp(float(ts or 0)).strftime('%H:%M')
    except Exception:
        return ''


def _day_wedges(day_nodes):
    """[(day_id, a0, a1, mid)] — one angular segment per date.

    The busiest day gets the widest segment; chronological order runs
    around the circle from 12 o'clock.  Shared by the layout (placing that
    day's runs and chats) and the drawing (the outer date arc), so the two
    can never disagree.
    """
    ordered = sorted(day_nodes, key=lambda d: str(d.get('key') or d.get('id')))
    if not ordered:
        return []
    if len(ordered) == 1:
        # A single day wraps the whole circle: no boundary exists, so the
        # marker anchors at 12 o'clock and no arc is drawn (a full circle
        # would read as a stray ring through the nodes, not as a date band).
        return [(ordered[0]['id'], -math.pi / 2.0, 3.0 * math.pi / 2.0,
                 -math.pi / 2.0)]
    weights = [max(1, int(d.get('runs') or 0) + int(d.get('chats') or 0))
               for d in ordered]
    total = float(sum(weights)) or 1.0
    out = []
    start = -math.pi / 2.0
    for d, w in zip(ordered, weights):
        span = 2.0 * math.pi * (w / total)
        out.append((d['id'], start, start + span, start + span / 2.0))
        start += span
    return out


def _plural(n, word):
    return f"{n} {word}" if int(n) == 1 else f"{n} {word}s"


def _node_tooltip(node):
    kind = node.get('kind')
    if kind == 'day':
        return _("{} — {} run(s), {} chat turn(s)").format(
            node.get('label'), node.get('runs', 0), node.get('chats', 0))
    if kind == 'run':
        lines = [
            "{} · {} · {}".format(
                node.get('label'), node.get('date', ''),
                _("ok") if node.get('status') == 'ok' else node.get('status')),
            "{} · {}".format(
                _plural(node.get('events', 0), 'node event'),
                ", ".join(node.get('chains') or []) or _("no chains recorded")),
        ]
        if node.get('request'):
            lines.append(_("request: {}").format(node.get('request')))
        return "\n".join(lines)
    if kind == 'chain':
        return _("chain '{}' — {} event(s) across {} run(s)").format(
            node.get('label'), node.get('events', 0), node.get('runs', 0))
    return node.get('label', '')


class _Canvas(QGraphicsView):
    node_clicked = pyqtSignal(str)

    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self.setRenderHints(
            QPainter.Antialiasing | QPainter.TextAntialiasing)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setMinimumHeight(160)

    def wheelEvent(self, event):
        try:
            factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
            self.scale(factor, factor)
        except Exception:
            pass

    def mousePressEvent(self, event):
        try:
            if event.button() == Qt.LeftButton:
                item = self.itemAt(event.pos())
                while item is not None and not item.data(0):
                    item = item.parentItem()
                if item is not None and item.data(0):
                    self.node_clicked.emit(str(item.data(0)))
        except Exception:
            pass
        super().mousePressEvent(event)

    def resizeEvent(self, event):
        # Late layout (expand/fullscreen, first show) must re-fit, else the
        # graph overflows the viewport edges.
        #
        # Two hard rules learned the hard way here:
        #  1. PyQt5 calls qFatal() when a Python exception escapes a Qt
        #     virtual override — a single AttributeError in this method
        #     killed the whole app (0xc0000409 fail-fast in Qt5Core.dll,
        #     no Python traceback).  Everything must be guarded.
        #  2. Use self.scene() (the QGraphicsView API), never an attribute:
        #     the scene is owned by MemoryGraphPanel, not by this view.
        super().resizeEvent(event)
        try:
            scene = self.scene()
            if scene is not None and scene.items():
                self.fit_all()
        except Exception:
            pass

    def fit_all(self):
        try:
            scene = self.scene()
            if scene is None:
                return
            rect = scene.itemsBoundingRect().adjusted(-40, -40, 40, 40)
            self.fitInView(rect, Qt.KeepAspectRatio)
        except Exception:
            pass


class MemoryGraphPanel(QFrame):
    """Graph view of the recorded memory — days, runs, chains, chats."""

    close_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("memoryGraphPanel")
        self.setStyleSheet(_PANEL_QSS)
        self._scene = QGraphicsScene(self)
        self._canvas = _Canvas(self._scene, self)
        self._canvas.setBackgroundBrush(QBrush(QColor(GRAPH_PLANE)))
        self._canvas.node_clicked.connect(self._on_node_clicked)
        self._items_by_node = {}
        self._selected_id = ""
        self._build_ui()

    # ── Public API ───────────────────────────────────────────────────
    def refresh(self):
        """Rebuild the graph from the recorded activity (honest window)."""
        try:
            from player.agentic_ops import run_memory
            data = run_memory.graph_data(
                day=self._window_value() or None,
                scope=self._scope_value() or None,
            )
        except Exception:
            logger.exception("MemoryGraphPanel.refresh: graph_data failed")
            data = {'nodes': [], 'edges': [], 'counts': {}}
        self._draw(data)

    def select(self, kind, key):
        """Show one node's recorded events in the details pane and mark it."""
        try:
            try:
                from player.agentic_ops import run_memory
                text = run_memory.graph_detail(
                    kind, key, scope=self._scope_value() or None)
            except Exception as e:
                logger.exception("MemoryGraphPanel.select failed")
                text = _("(details unavailable: {})").format(e)
            self._details.setPlainText(text)
            self._highlight(f"{kind}:{key}")
        except Exception:
            logger.exception("MemoryGraphPanel.select: error")

    # ── UI construction ──────────────────────────────────────────────
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 10, 14, 10)
        root.setSpacing(8)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        title = QLabel(_("Knowledge graph"), self)
        title.setStyleSheet(
            f"color: {TEXT_COLOR}; font-size: 13px; font-weight: 700;")
        head.addWidget(title)
        self._count_label = QLabel("", self)
        self._count_label.setStyleSheet(
            "color: rgba(255,255,255,0.35); font-size: 10px;")
        head.addWidget(self._count_label)
        head.addStretch(1)

        self._layout_combo = QComboBox(self)
        self._layout_combo.addItem(_("Left \u2192 right"), "lr")
        self._layout_combo.addItem(_("Radial"), "radial")
        self._layout_combo.setToolTip(
            _("How the graph is arranged (left-right reads like time)"))
        head.addWidget(self._layout_combo)

        self._scope_combo = QComboBox(self)
        self._scope_combo.addItem(_("All scopes"), "")
        for label, value in ((_("Agent"), "agent"), (_("Manual"), "manual"),
                             (_("Scheduled"), "schedule")):
            self._scope_combo.addItem(label, value)
        self._scope_combo.setToolTip(_("Separate agent-mode from manual runs"))
        head.addWidget(self._scope_combo)

        self._window_combo = QComboBox(self)
        for label, value in ((_("All time"), ""), (_("Today"), "today"),
                             (_("Yesterday"), "yesterday"),
                             (_("Last 7 days"), "last 7 days"),
                             (_("Last 30 days"), "last 30 days")):
            self._window_combo.addItem(label, value)
        head.addWidget(self._window_combo)

        refresh_btn = QPushButton(_("Refresh"), self)
        refresh_btn.setCursor(Qt.PointingHandCursor)
        refresh_btn.setStyleSheet(
            f"QPushButton {{ background-color: transparent; color: {TEXT_COLOR};"
            f"border: 1px solid rgba(255,255,255,0.14); border-radius: 10px;"
            f"padding: 3px 10px; font-size: 11px; }}"
            f"QPushButton:hover {{ color: {ACCENT_COLOR};"
            f"border-color: {ACCENT_COLOR}; }}")
        refresh_btn.clicked.connect(self.refresh)
        head.addWidget(refresh_btn)

        close_btn = QPushButton("\u00d7", self)
        close_btn.setToolTip(_("Close"))
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setFixedSize(22, 22)
        close_btn.setStyleSheet(
            f"QPushButton {{ background-color: transparent;"
            f"color: rgba(255,255,255,0.5); border: 0px; border-radius: 11px;"
            f"font-size: 14px; }}"
            f"QPushButton:hover {{ color: {ACCENT_COLOR};"
            f"background-color: rgba(255,255,255,0.06); }}")
        close_btn.clicked.connect(self.close_requested.emit)
        head.addWidget(close_btn)
        root.addLayout(head)

        # Filter/layout changes rebuild the view.
        self._layout_combo.currentIndexChanged.connect(lambda _i: self.refresh())
        self._scope_combo.currentIndexChanged.connect(lambda _i: self.refresh())
        self._window_combo.currentIndexChanged.connect(lambda _i: self.refresh())

        root.addWidget(self._canvas, 1)

        # Legend: every line and color means exactly one thing.
        self._legend = QLabel(self)
        self._legend.setTextFormat(Qt.RichText)
        self._legend.setWordWrap(True)
        self._legend.setText(
            f"<span style='color:{ACCENT_COLOR}'>\u25cf</span> run ok&nbsp;&nbsp;"
            f"<span style='color:{RED_PRIMARY}'>\u25cf</span> run failed&nbsp;&nbsp;"
            f"<span style='color:#FFA726'>\u25cf</span> run stopped&nbsp;&nbsp;"
            f"<span style='color:#4DB6FF'>\u25cf</span> chain&nbsp;&nbsp;"
            f"<span style='color:rgba(255,255,255,0.55)'>\u25cb</span> chat&nbsp;&nbsp;"
            f"<span style='color:rgba(0,224,184,0.6)'>\u2014 \u2192</span> chat turn "
            f"\u2192 the run it produced&nbsp;&nbsp;"
            f"<span style='color:{ACCENT_COLOR}'>\u2014 \u2192</span> run \u2192 chain "
            f"used (label = how much of that chain ran)&nbsp;&nbsp;"
            f"dates own their runs and chats"
        )
        self._legend.setStyleSheet(
            "color: rgba(255,255,255,0.42); font-size: 10px;")
        root.addWidget(self._legend)

        self._empty = QLabel(
            _("No recorded activity in this window yet.\n"
              "Run a chain, schedule one, or ask the agent something — "
              "every executed node lands here."), self)
        self._empty.setAlignment(Qt.AlignCenter)
        self._empty.setWordWrap(True)
        self._empty.setStyleSheet(
            "color: rgba(255,255,255,0.35); font-size: 11px;")
        self._empty.hide()
        root.addWidget(self._empty, 1)

        self._details = QTextEdit(self)
        self._details.setReadOnly(True)
        self._details.setFixedHeight(140)
        self._details.setPlaceholderText(
            _("Click a node to audit what was recorded."))
        self._details.setStyleSheet(
            "QTextEdit { background-color: rgba(0,0,0,0.25);"
            " color: rgba(255,255,255,0.78);"
            " border: 1px solid rgba(255,255,255,0.08); border-radius: 8px;"
            " font-family: Consolas, 'Courier New', monospace;"
            " font-size: 10px; padding: 4px; }")
        root.addWidget(self._details)

    def _layout_mode(self):
        try:
            return str(self._layout_combo.currentData() or "lr")
        except Exception:
            return "lr"

    def _scope_value(self):
        try:
            return str(self._scope_combo.currentData() or "")
        except Exception:
            return ""

    def _window_value(self):
        try:
            return str(self._window_combo.currentData() or "")
        except Exception:
            return ""

    # ── graph drawing ────────────────────────────────────────────────
    def _draw(self, data):
        try:
            self._scene.clear()
            self._items_by_node = {}
            self._selected_id = ""
            nodes = list(data.get('nodes') or [])
            edges = list(data.get('edges') or [])
            counts = data.get('counts') or {}
            if not nodes:
                self._canvas.hide()
                self._empty.show()
                self._count_label.setText("")
                return
            self._empty.hide()
            self._canvas.show()

            mode = self._layout_mode()
            if mode == 'radial':
                pos = self._layout_radial(nodes, edges)
            else:
                pos = self._layout_columns(nodes, edges)
            nodes_by_id = {n['id']: n for n in nodes}
            # Radial only: date bands as boundaries (gaps each side, none at
            # all for a single day) — the column layout labels its day nodes
            # directly instead.
            if mode == 'radial':
                wedges = _day_wedges([n for n in nodes if n['kind'] == 'day'])
                draw_arcs = len(wedges) > 1
                for did, a0, a1, mid in wedges:
                    d = nodes_by_id.get(did) or {}
                    tip = _("{} — {} run(s), {} chat turn(s)").format(
                        d.get('label'), d.get('runs', 0), d.get('chats', 0))
                    if draw_arcs:
                        # Generous gaps at the boundaries: the bands must
                        # read as separate territories, never one ring.
                        inset = min(0.18, (a1 - a0) * 0.09)
                        self._add_day_arc(a0 + inset, a1 - inset, tip)
                    self._add_arc_text(
                        "{} · {} · {}".format(
                            d.get('label'),
                            _plural(d.get('runs', 0), 'run'),
                            _plural(d.get('chats', 0), 'chat')),
                        mid, _R_DAY)

            rc_i = 0  # run->chain index — staggers line labels so parallel
                      # links do not stack their text on the same spot
            d_c_i = 0  # same for the date->chat links
            for e in edges:
                p1 = pos.get(e.get('source'))
                p2 = pos.get(e.get('target'))
                if not p1 or not p2:
                    continue
                kind = e.get('kind')
                if kind == 'day-run':
                    # The date's link to its run.  The run's own summary stays
                    # beside the run node (one label class per row band keeps
                    # the tight day blocks collision-free).
                    d = nodes_by_id.get(e.get('source')) or {}
                    rn = nodes_by_id.get(e.get('target')) or {}
                    pen = QPen(_EDGE_DAY)
                    pen.setWidthF(1.0)
                    pen.setStyle(Qt.DashLine)
                    tip = (f"{d.get('label', '')}: run {_hhmm(rn.get('ts'))} "
                           f"— {rn.get('label')} "
                           f"({rn.get('events', 0)} node event(s), "
                           f"{rn.get('status')})")
                    self._add_edge(p1, p2, pen, False, 0.0, tip)
                    continue
                if kind == 'day-chat':
                    # The date's line to its chat carries the turn's text —
                    # the dot keeps only who + when.
                    d = nodes_by_id.get(e.get('source')) or {}
                    cn = nodes_by_id.get(e.get('target')) or {}
                    pen = QPen(_EDGE_DAY_CHAT)
                    pen.setWidthF(0.8)
                    pen.setStyle(Qt.DotLine)
                    tip = (f"{d.get('label', '')} {_hhmm(cn.get('ts'))} "
                           f"{cn.get('role')}: {cn.get('label')}")
                    self._add_edge(p1, p2, pen, False, 0.0, tip)
                    excerpt = str(cn.get('label') or '')
                    if len(excerpt) > 40:
                        excerpt = excerpt[:40] + '…'
                    if excerpt:
                        # Staircase: each successive turn labels further along
                        # its own link with a deeper offset, so consecutive
                        # rows can never stack their text.
                        self._add_edge_label(
                            excerpt, p1, p2,
                            frac=0.5 + 0.16 * (d_c_i % 4),
                            offset=-6.0 - 6.0 * (d_c_i % 4))
                        d_c_i += 1
                    continue
                if kind == 'chat-run':
                    # This chat turn PRODUCED that run (link recorded and
                    # query-verified at write time) — the tie between what
                    # was asked and the chains that then executed.
                    cn = nodes_by_id.get(e.get('source')) or {}
                    rn = nodes_by_id.get(e.get('target')) or {}
                    pen = QPen(_EDGE_CHAT_RUN)
                    pen.setWidthF(1.0)
                    tip = (f"turn '{str(cn.get('label') or '')[:60]}' "
                           f"produced run {_hhmm(rn.get('ts'))} "
                           f"({rn.get('label')})")
                    self._add_edge(p1, p2, pen, True, 14.0, tip)
                    continue
                if kind == 'run-chain':
                    # The run USED this chain.  Color = how the run ended,
                    # thickness = how many node events that chain contributed,
                    # and the label states that count right on the line.
                    run_node = nodes_by_id.get(e.get('source')) or {}
                    base = _RUN_COLORS.get(run_node.get('status'), _COL_RUN_OK)
                    color = QColor(base.red(), base.green(), base.blue(), 175)
                    count = int(e.get('count') or 1)
                    pen = QPen(color)
                    pen.setWidthF(1.0 + min(2.5, 0.5 * count))
                    chain_node = nodes_by_id.get(e.get('target')) or {}
                    tip = (f"run {_hhmm(run_node.get('ts'))} "
                           f"({run_node.get('label')}) used chain "
                           f"'{chain_node.get('label')}' — {count} node event(s)")
                    self._add_edge(p1, p2, pen, True,
                                   _radius('chain', chain_node), tip)
                    self._add_edge_label(
                        f"used · {_plural(count, 'event')}", p1, p2,
                        frac=0.35 + (rc_i % 3) * 0.2,
                        offset=8.0 if rc_i % 2 == 0 else -14.0)
                    rc_i += 1

            for n in nodes:
                self._add_node(n, pos, mode)
            self._canvas.fit_all()
            total_runs = int(counts.get('runs') or 0)
            shown = int(counts.get('runs_shown') or 0)
            runs_txt = f"{shown}/{total_runs}" if total_runs > shown else str(shown)
            self._count_label.setText(_("{} run(s) · {} chain(s) · {} chat(s)").format(
                runs_txt, counts.get('chains', 0), counts.get('chats_shown', 0)))
        except Exception:
            logger.exception("MemoryGraphPanel._draw failed")

    def _layout_columns(self, nodes, edges):
        """Left -> right, the way time reads: DAY | its runs+chats | CHAINS.

        Each day is a block whose members stack in chronological order (a
        chat sits right above the run it produced), and chains sit at the
        barycenter of the runs that used them, spread so labels never
        overlap.  Deterministic.
        """
        pos = {}
        days = sorted((n for n in nodes if n['kind'] == 'day'),
                      key=lambda n: str(n.get('key') or n.get('id')))
        cursor = 40.0
        for day in days:
            members = [n for n in nodes
                       if n['kind'] in ('run', 'chat')
                       and f"day:{n.get('date')}" == day['id']]
            members.sort(key=lambda n: (n.get('ts') or 0, n['id']))
            col_h = max(_MIN_COL_H, len(members) * _ROW_H + 28)
            pos[day['id']] = (_COL_DAY_X, cursor + col_h / 2.0)
            for i, n in enumerate(members):
                y = cursor + 14 + i * _ROW_H + _ROW_H / 2.0
                pos[n['id']] = (_COL_EVENT_X, y)
            cursor += col_h + 34

        # Members without a matching date node still get their rows.
        strays = [n for n in nodes if n['id'] not in pos
                  and n['kind'] in ('run', 'chat')]
        for i, n in enumerate(sorted(strays, key=lambda n: (n.get('ts') or 0,
                                                           n['id']))):
            pos[n['id']] = (_COL_EVENT_X,
                            cursor + 14 + i * _ROW_H + _ROW_H / 2.0)

        chain_nodes = [n for n in nodes if n['kind'] == 'chain']
        if chain_nodes:
            links = {}
            for e in edges:
                if e.get('kind') != 'run-chain':
                    continue
                p = pos.get(e.get('source'))
                if p:
                    links.setdefault(e.get('target'), []).append(p[1])
            order = sorted(chain_nodes,
                           key=lambda n: -int(n.get('events') or 0))
            for n in order:
                ys = links.get(n['id'])
                pos[n['id']] = (_COL_CHAIN_X,
                                sum(ys) / len(ys) if ys else cursor / 2.0)
            prev_y = None
            prev_node = None
            for n in sorted(chain_nodes, key=lambda c: pos[c['id']][1]):
                x, y = pos[n['id']]
                if prev_node is not None:
                    # Radii-aware spacing: the old fixed 46px gap made the
                    # biggest chain hubs (r up to 24) touch each other.
                    need = (_radius('chain', prev_node)
                            + _radius('chain', n) + 14.0)
                    if y - prev_y < need:
                        y = prev_y + need
                pos[n['id']] = (x, y)
                prev_y = y
                prev_node = n
        return pos

    def _layout_radial(self, nodes, edges):
        """Temporal radial layout: the hub chain (the orchestrator when
        present) sits at the centre; the circle is TIME.  Each day owns an
        angular segment holding its runs (inner ring) and chats (outer
        ring); chains sit on a middle ring near the runs that used them.

        Deterministic, and nothing piles up: angles inside a segment are
        evenly spread, so two dots from the same day can never overlap in
        angle — that was the clutter when dates were ordinary nodes.
        """
        pos = {}
        if not nodes:
            return pos
        if len(nodes) == 1:
            pos[nodes[0]['id']] = (0.0, 0.0)
            return pos

        hub = self._pick_hub_id(nodes, edges)
        pos[hub] = (0.0, 0.0)
        angles = {hub: 0.0}

        day_nodes = [n for n in nodes if n['kind'] == 'day']
        wedges = _day_wedges(day_nodes)
        wedge_ids = {w[0] for w in wedges}
        members, strays = {}, []
        for n in nodes:
            # Chains and the day markers themselves are placed below; only
            # runs/chats belong to a day's member bucket (a day node has no
            # 'date' field — classifying it here would strand it as a stray
            # and later overwrite its arc position).
            if n['id'] == hub or n['kind'] in ('chain', 'day'):
                continue
            did = f"day:{n.get('date')}"
            if did in wedge_ids:
                members.setdefault(did, []).append(n)
            else:
                strays.append(n)

        for did, a0, a1, mid in wedges:
            span = a1 - a0
            group = sorted(members.get(did, []),
                           key=lambda n: (n.get('ts') or 0, n['id']))
            runs = [n for n in group if n['kind'] == 'run']
            chats = [n for n in group if n['kind'] == 'chat']
            # Members cluster around the date's anchor (≤30° per member,
            # capped to the wedge) instead of spraying across the whole
            # sector — a lone run must NOT land on the antipode of its date
            # (its fan line would drag straight through the hub), and a
            # tight fan per date is far easier to read.
            for kind_list, radius in ((runs, _R_RUN), (chats, _R_CHAT)):
                n = len(kind_list)
                if not n:
                    continue
                used = min(span, n * (math.pi / 3.0))
                base = mid - used / 2.0
                for i, item in enumerate(kind_list):
                    ang = base + used * (i + 0.5) / n
                    pos[item['id']] = (math.cos(ang) * radius,
                                       math.sin(ang) * radius)
                    angles[item['id']] = ang
            day = next((d for d in day_nodes if d['id'] == did), None)
            if day is not None:
                pos[day['id']] = (math.cos(mid) * _R_DAY, math.sin(mid) * _R_DAY)
                angles[day['id']] = mid

        # Runs/chats without a matching date node (should not happen in
        # practice) share the chat ring evenly instead of vanishing.
        for i, n in enumerate(sorted(strays, key=lambda n: (n.get('ts') or 0,
                                                           n['id']))):
            ang = 2.0 * math.pi * (i + 0.5) / max(1, len(strays))
            pos[n['id']] = (math.cos(ang) * _R_CHAT, math.sin(ang) * _R_CHAT)
            angles[n['id']] = ang

        # Chains: on the middle ring at the circular mean of the runs that
        # used them (they point back at their days); a small angular gap is
        # enforced so neighbouring chains never stack.
        adj = {}
        for e in edges:
            a, b = e.get('source'), e.get('target')
            if a and b and a != b:
                adj.setdefault(a, set()).add(b)
                adj.setdefault(b, set()).add(a)
        chains = [n for n in nodes if n['kind'] == 'chain' and n['id'] != hub]
        placed, unanchored = [], []
        for c in chains:
            sx = sy = 0.0
            for nb in adj.get(c['id'], ()):
                a = angles.get(nb)
                if a is None:
                    continue
                sx += math.cos(a)
                sy += math.sin(a)
            if sx or sy:
                placed.append((math.atan2(sy, sx), c))
            else:
                unanchored.append(c)
        placed.sort(key=lambda t: t[0])
        n = len(placed)
        gap = 2.0 * math.pi / max(len(chains), 8)
        center = 0.0
        if n:
            # Spread the fan SYMMETRICALLY around the parents' mean
            # direction, offset by half a gap — chains then straddle their
            # runs instead of marching clockwise from them, so no chain
            # lands collinear with a date (its fan line would pass through
            # it) and the ring stays balanced.
            sx = sum(math.cos(a) for a, _ in placed)
            sy = sum(math.sin(a) for a, _ in placed)
            center = math.atan2(sy, sx) if (sx or sy) else 0.0
            base = center - (n - 1) * gap / 2.0 + gap / 2.0
            for i, (_ang, c) in enumerate(placed):
                ang = base + i * gap
                pos[c['id']] = (math.cos(ang) * _R_CHAIN,
                                math.sin(ang) * _R_CHAIN)
        # Chains with no recorded run go on the opposite side, evenly.
        for j, c in enumerate(unanchored):
            ang = (center + math.pi
                   + (j - (len(unanchored) - 1) / 2.0) * gap)
            pos[c['id']] = (math.cos(ang) * _R_CHAIN,
                            math.sin(ang) * _R_CHAIN)
        return pos

    @staticmethod
    def _pick_hub_id(nodes, edges):
        """The centre: the orchestrator chain when present (the agent's
        root), else the most-connected chain, else the most-connected node."""
        deg = {}
        for e in edges:
            for key in ('source', 'target'):
                nid = e.get(key)
                if nid:
                    deg[nid] = deg.get(nid, 0) + 1
        chains = [n for n in nodes if n.get('kind') == 'chain']
        if chains:
            orch = [c for c in chains
                    if 'orch' in str(c.get('label') or c.get('key') or '').lower()]
            if orch:
                return max(orch, key=lambda c: deg.get(c['id'], 0))['id']
            return max(chains,
                       key=lambda c: (deg.get(c['id'], 0),
                                      int(c.get('events') or 0)))['id']
        return max(nodes, key=lambda n: deg.get(n['id'], 0))['id']

    def _add_node(self, node, pos, mode='lr'):
        p = pos.get(node['id'])
        if not p:
            return
        x, y = p
        r = _radius(node['kind'], node)
        item = QGraphicsEllipseItem(x - r, y - r, r * 2, r * 2)
        kind = node['kind']
        if kind == 'run':
            color = _RUN_COLORS.get(node.get('status'), _COL_RUN_STOPPED)
            brush = QBrush(QColor(color.red(), color.green(), color.blue(), 200))
            pen = QPen(QColor(255, 255, 255, 140), 1.0)
        elif kind == 'chain':
            brush = QBrush(QColor(_COL_CHAIN.red(), _COL_CHAIN.green(),
                                  _COL_CHAIN.blue(), 210))
            pen = QPen(QColor(255, 255, 255, 120), 1.0)
        elif kind == 'chat':
            base = _COL_CHAT_AGENT if node.get('role') == 'agent' else _COL_CHAT_USER
            brush = QBrush(base)
            pen = QPen(QColor(0, 0, 0, 0), 0.0)
        else:  # day
            brush = QBrush(_COL_DAY)
            pen = QPen(QColor(255, 255, 255, 110), 1.2)
        item.setBrush(brush)
        item.setPen(pen)
        item.setData(0, node['id'])
        item.setData(2, pen)
        item.setToolTip(_node_tooltip(node))
        item.setZValue(1)
        self._scene.addItem(item)
        self._items_by_node[node['id']] = item

        # ── labels: primary (what) + secondary (how much / how it went) ──
        # Radial layout: text points AWAY from the centre, so the spokes
        # stay readable on both hemispheres.
        _right = x >= 0.0
        lx = (x + r + 7) if _right else (x - r - 7)
        _align = 'left' if _right else 'right'
        if kind == 'day':
            if mode == 'radial':
                # The date lives on the arc band (see _add_arc_text); the
                # marker itself stays clean — its tooltip carries the counts.
                pass
            else:
                self._add_label(node.get('label'), x, y - r - 32, 8.5,
                                _COL_LABEL, align='center')
                self._add_label(
                    f"{_plural(node.get('runs', 0), 'run')} · "
                    f"{_plural(node.get('chats', 0), 'chat')}",
                    x, y - r - 20, 7.0, _COL_LABEL_DIM, align='center')
        elif kind == 'run':
            # Identity + summary beside the node (the one label class per
            # row band; the chain links carry their own 'used · N' labels).
            self._add_label(
                f"{_hhmm(node.get('ts'))} {str(node.get('label') or '')[:24]}",
                lx, y - 13, 7.5, _COL_LABEL, align=_align)
            _dur = node.get('duration_s')
            _dur_txt = f" · {_dur}s" if isinstance(_dur, (int, float)) else ""
            self._add_label(
                f"{_plural(node.get('events', 0), 'node')}{_dur_txt} · "
                f"{node.get('status')}",
                lx, y + 2, 7.0, _COL_LABEL_DIM, align=_align)
        elif kind == 'chat':
            # Identity only: the turn's text rides its date->chat line.
            role_color = (_COL_CHAT_AGENT if node.get('role') == 'agent'
                          else _COL_LABEL_DIM)
            self._add_label(f"{_hhmm(node.get('ts'))} {node.get('role')}",
                            lx, y - 6, 7.0, role_color, align=_align)
        else:  # chain
            self._add_label(str(node.get('label') or ''), lx, y - 13, 8.0,
                            _COL_LABEL, align=_align)
            self._add_label(
                f"{_plural(node.get('events', 0), 'event')} · "
                f"{_plural(node.get('runs', 0), 'run')}",
                lx, y + 2, 7.0, _COL_LABEL_DIM, align=_align)

    def _add_label(self, text, x, y, size, color, align='left'):
        """One text item; align centers/rights it on x (node columns)."""
        text = str(text or '')
        if not text:
            return
        item = QGraphicsSimpleTextItem(text)
        font = item.font()
        font.setPointSizeF(size)
        item.setFont(font)
        item.setBrush(QBrush(QColor(color)))
        rect = item.boundingRect()
        if align == 'center':
            item.setPos(x - rect.width() / 2.0, y)
        elif align == 'right':
            item.setPos(x - rect.width(), y)
        else:
            item.setPos(x, y)
        item.setZValue(2)
        self._scene.addItem(item)

    def _add_edge_label(self, text, p1, p2, frac=0.5, offset=6.0):
        """Relationship text riding ON the line, rotated to follow it.

        The label sits at ``frac`` along the link, nudged ``offset`` px to the
        perpendicular side, and its rotation is flipped so it never reads
        upside-down.  Callers stagger frac/offset across parallel links.
        Short links get no label at all — text longer than its line just
        collides with everything around it (the tooltip still carries it).
        """
        try:
            text = str(text or '')
            if not text:
                return
            dx, dy = p2[0] - p1[0], p2[1] - p1[1]
            length = math.hypot(dx, dy) or 0.01
            if length < 90.0:
                return
            item = QGraphicsSimpleTextItem(text)
            font = item.font()
            font.setPointSizeF(6.8)
            item.setFont(font)
            item.setBrush(QBrush(QColor(_COL_LABEL_DIM)))
            rect = item.boundingRect()
            angle = math.degrees(math.atan2(dy, dx))
            if angle > 90.0:
                angle -= 180.0
            elif angle < -90.0:
                angle += 180.0
            cx = p1[0] + dx * frac + (-dy / length) * offset
            cy = p1[1] + dy * frac + (dx / length) * offset
            item.setTransformOriginPoint(rect.width() / 2.0,
                                         rect.height() / 2.0)
            item.setPos(cx - rect.width() / 2.0, cy - rect.height() / 2.0)
            item.setRotation(angle)
            item.setZValue(0.5)
            item.setToolTip(item.toolTip() or text)
            self._scene.addItem(item)
        except Exception:
            pass

    def _add_day_arc(self, a0, a1, tooltip=""):
        """Outer arc spanning one day's angular segment (its territory)."""
        try:
            rect = QRectF(-_R_DAY, -_R_DAY, _R_DAY * 2.0, _R_DAY * 2.0)
            path = QPainterPath()
            start_deg = math.degrees(a0)
            span_deg = math.degrees(a1 - a0)
            path.arcMoveTo(rect, start_deg)
            path.arcTo(rect, start_deg, span_deg)
            arc = QGraphicsPathItem(path)
            pen = QPen(QColor(255, 255, 255, 70))
            pen.setWidthF(1.4)
            arc.setPen(pen)
            arc.setZValue(-1)
            if tooltip:
                arc.setToolTip(tooltip)
            self._scene.addItem(arc)
        except Exception:
            pass

    def _add_arc_text(self, text, ang, radius):
        """Date band label riding just outside its arc, flipped upright."""
        try:
            text = str(text or '')
            if not text:
                return
            item = QGraphicsSimpleTextItem(text)
            font = item.font()
            font.setPointSizeF(8.0)
            item.setFont(font)
            item.setBrush(QBrush(QColor(_COL_LABEL)))
            rect = item.boundingRect()
            r = radius + 20.0
            cx, cy = math.cos(ang) * r, math.sin(ang) * r
            deg = math.degrees(ang) + 90.0
            if deg > 90.0:
                deg -= 180.0
            elif deg < -90.0:
                deg += 180.0
            item.setTransformOriginPoint(rect.width() / 2.0,
                                         rect.height() / 2.0)
            item.setPos(cx - rect.width() / 2.0, cy - rect.height() / 2.0)
            item.setRotation(deg)
            item.setZValue(2)
            self._scene.addItem(item)
        except Exception:
            pass

    def _add_edge(self, p1, p2, pen, arrow, tip_r, tooltip=""):
        """One meaning-carrying edge: line + arrowhead at the target node."""
        try:
            line = QGraphicsLineItem(QLineF(
                QPointF(p1[0], p1[1]), QPointF(p2[0], p2[1])))
            line.setPen(pen)
            line.setZValue(-1)
            if tooltip:
                line.setToolTip(tooltip)
            self._scene.addItem(line)
            if not arrow:
                return
            dx, dy = p2[0] - p1[0], p2[1] - p1[1]
            dist = math.hypot(dx, dy) or 1.0
            ux, uy = dx / dist, dy / dist
            tip = (p2[0] - ux * tip_r, p2[1] - uy * tip_r)
            base = (tip[0] - ux * 9.0, tip[1] - uy * 9.0)
            px, py = -uy, ux
            head = QGraphicsPolygonItem(QPolygonF([
                QPointF(tip[0], tip[1]),
                QPointF(base[0] + px * 3.4, base[1] + py * 3.4),
                QPointF(base[0] - px * 3.4, base[1] - py * 3.4),
            ]))
            head.setBrush(QBrush(pen.color()))
            head.setPen(QPen(QColor(0, 0, 0, 0), 0))
            head.setZValue(0)
            if tooltip:
                head.setToolTip(tooltip)
            self._scene.addItem(head)
        except Exception:
            pass

    def _on_node_clicked(self, node_id):
        try:
            kind, _, key = str(node_id).partition(':')
            if kind and key:
                self.select(kind, key)
        except Exception:
            logger.exception("MemoryGraphPanel._on_node_clicked: error")

    def _highlight(self, node_id):
        try:
            prev = self._items_by_node.get(self._selected_id)
            if prev is not None and prev.data(2) is not None:
                prev.setPen(prev.data(2))
            item = self._items_by_node.get(node_id)
            if item is not None:
                item.setPen(QPen(QColor(255, 255, 255, 230), 2.0))
            self._selected_id = node_id
        except Exception:
            pass
