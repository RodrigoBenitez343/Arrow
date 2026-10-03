"""agent_memory_dialog.py — Agent Settings dialog with Memory tab.

Provides a clean graph view of agent memory showing goal-to-goal
relationships (connected by shared keywords and shared chains),
plus tabbed inspection panels for drilling into executions and feedback.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple

import networkx as nx
import numpy as np
import pyqtgraph as pg
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QFont, QLinearGradient, QPainter, QPixmap
from PyQt5.QtWidgets import (
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..constants import (
    ACCENT_COLOR, ACCENT_HOVER, BAR_BG, CENTER_BG, DARK_GREY, LIGHT_GREY, MEDIUM_GREY,
    TEXT_COLOR, BLOCK_HOVER, WELL_BG, HAIRLINE, DANGER_COLOR, BTN_PRIMARY_TEXT,
    RADIUS_SM, RADIUS_MD, DIALOG_LARGE_W, DIALOG_LARGE_H,
)
from ..i18n import _
from .base_dialog import ModernDialog

logger = logging.getLogger(__name__)

# ── Graph color palette ──
EDGE_KEYWORD = QColor(100, 180, 140, 100)   # greenish — query similarity
EDGE_CHAIN = QColor(140, 140, 200, 80)       # bluish — shared chain
COLOR_GOAL_FRESH = QColor(ACCENT_COLOR)          # accent
COLOR_GOAL_OLD = QColor("#4E5D58")            # dimmed
COLOR_GOAL_HIGHLIGHT = QColor("#FFFFFF")

_STOP_WORDS = {
    "the", "a", "an", "is", "it", "to", "of", "in", "for", "on", "and",
    "with", "or", "be", "do", "so", "if", "my", "i", "me", "we", "you",
    "that", "this", "can", "what", "how", "not", "but", "from", "at",
    "by", "its", "all", "was", "are", "has", "had", "have", "been",
    "just", "get", "out", "up", "no", "go", "ok", "hi", "oh", "he",
    "she", "they", "will", "should", "would", "could", "about", "there",
}


def _tokenize(text: str) -> Set[str]:
    """Extract meaningful lowercase word tokens, filtering stop words."""
    words = re.findall(r"[a-zA-Z0-9]{2,}", text.lower())
    return {w for w in words if w not in _STOP_WORDS}


def _color_for_age(created: float, now: float) -> QColor:
    """Interpolate between fresh accent and dimmed grey based on age.

    Goals less than 1 hour old get full accent; older goals
    fade toward COLOR_GOAL_OLD over ~7 days.
    """
    age_sec = max(0, now - created)
    t = min(1.0, age_sec / (7 * 24 * 3600))  # 0 = fresh, 1 = week-old
    r = int(COLOR_GOAL_FRESH.red() * (1 - t) + COLOR_GOAL_OLD.red() * t)
    g = int(COLOR_GOAL_FRESH.green() * (1 - t) + COLOR_GOAL_OLD.green() * t)
    b = int(COLOR_GOAL_FRESH.blue() * (1 - t) + COLOR_GOAL_OLD.blue() * t)
    return QColor(r, g, b)


# ═══════════════════════════════════════════════════════════════════
# MemoryGraphWidget — goals-only graph with meaningful edges
# ═══════════════════════════════════════════════════════════════════

class MemoryGraphWidget(QWidget):
    """Memory graph view — chains, context entries, and relationships in 2D.

    - Primary nodes = chain executions (or goals if goal-scoped).
    - Secondary nodes = context entries (from nano-graphrag).
    - Edges connect nodes that share:
        * keywords in their descriptions (semantic similarity)
        * parent→child chain import relationships
        * goal→chain membership
    - Click a node to select/inspect it.
    """

    node_selected = pg.QtCore.pyqtSignal(str)  # chain_id or goal_id

    def __init__(self, parent=None):
        super().__init__(parent)
        self._goals: Dict[str, Any] = {}
        self._fragments: List[Dict[str, Any]] = []
        self._memory_entries: List[Dict[str, Any]] = []
        self._relationships: List[Dict[str, str]] = []
        self._positions: Dict[str, Tuple[float, float]] = {}
        self._scatter: Optional[pg.ScatterPlotItem] = None
        self._ctx_scatter: Optional[pg.ScatterPlotItem] = None
        self._edge_items: List[pg.PlotDataItem] = []
        self._label_items: List[pg.TextItem] = []
        self._highlighted: Optional[str] = None
        self._node_type: str = "fragment"  # "goal" or "fragment"

        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._glw = pg.GraphicsLayoutWidget()
        self._glw.setBackground(DARK_GREY)
        self._glw.setAntialiasing(True)

        self._plot = self._glw.addPlot(row=0, col=0)
        self._plot.hideAxis("left")
        self._plot.hideAxis("bottom")
        self._plot.setMouseEnabled(x=True, y=True)
        self._plot.setMenuEnabled(False)
        self._plot.disableAutoRange()

        layout.addWidget(self._glw, 1)

        # ── Legend ──
        legend = QFrame(self)
        legend.setStyleSheet("QFrame { background: transparent; }")
        leg_layout = QHBoxLayout(legend)
        leg_layout.setContentsMargins(8, 0, 8, 2)
        leg_layout.setSpacing(4)

        def _add_legend_item(parent_layout, color, width, dashed, text):
            icon = QLabel()
            pix = QPixmap(18, 10)
            pix.fill(Qt.transparent)
            p = QPainter(pix)
            p.setRenderHint(QPainter.Antialiasing)
            pen = p.pen()
            pen.setColor(color)
            pen.setWidthF(width)
            if dashed:
                pen.setStyle(Qt.DashLine)
            p.setPen(pen)
            p.drawLine(2, 5, 16, 5)
            p.end()
            icon.setPixmap(pix)
            parent_layout.addWidget(icon)
            lbl = QLabel(text)
            lbl.setStyleSheet(f"color: rgba(255,255,255,0.55); font-size: 10px;")
            parent_layout.addWidget(lbl)

        _add_legend_item(leg_layout, EDGE_KEYWORD, 1.5, False, "Keyword overlap")
        _add_legend_item(leg_layout, EDGE_CHAIN, 1.0, True, "Chain relationship")

        leg_layout.addStretch()
        layout.addWidget(legend)

    # ── Public API ──

    def load_data(
        self,
        goals: Dict[str, Any],
        fragments: List[Dict[str, Any]],
        memory_entries: List[Dict[str, Any]] | None = None,
        relationships: List[Dict[str, str]] | None = None,
    ):
        self._goals = goals
        self._fragments = fragments
        self._memory_entries = memory_entries or []
        self._relationships = relationships or []
        self._clear()

        if goals:
            self._node_type = "goal"
            G = self._build_goal_graph()
        elif fragments:
            self._node_type = "fragment"
            G = self._build_chain_graph()
        else:
            self._status_text("No memory data — run an agent task first")
            return

        if G.number_of_nodes() == 0:
            self._status_text("No connected nodes to display")
            return

        self._layout(G)
        self._draw_edges(G)
        self._draw_nodes(G)
        self._draw_labels()
        self._plot.autoRange(padding=0.35)

    def _status_text(self, msg: str):
        """Show a status message in the center of the graph."""
        text = pg.TextItem(text=msg, color=(255, 255, 255, 100), anchor=(0.5, 0.5))
        text.setFont(QFont("Segoe UI", 13))
        self._plot.addItem(text)
        self._label_items.append(text)
        self._plot.autoRange()

    # ── Graph construction ──

    def _build_goal_graph(self) -> nx.Graph:
        """Create a graph where nodes are goals and edges are meaningful relationships."""
        G = nx.Graph()

        goal_ids = list(self._goals.keys())
        goal_tokens: Dict[str, Set[str]] = {}
        goal_chains: Dict[str, Set[str]] = defaultdict(set)

        for gid in goal_ids:
            goal = self._goals[gid]
            G.add_node(gid, kind="goal")
            goal_tokens[gid] = _tokenize(goal.get("query", ""))
            for frag in goal.get("fragments", []):
                cid = frag.get("chain_id", "")
                if cid:
                    goal_chains[gid].add(cid)
                    # Add fragment as sub-node connected to goal
                    G.add_node(cid, kind="chain")
                    G.add_edge(gid, cid, etype="membership", weight=1)

        # Edge type 1: keyword overlap
        for i in range(len(goal_ids)):
            for j in range(i + 1, len(goal_ids)):
                a, b = goal_ids[i], goal_ids[j]
                shared = goal_tokens[a] & goal_tokens[b]
                if len(shared) >= 2:
                    G.add_edge(a, b, etype="keyword", weight=len(shared))

        # Edge type 2: shared chains
        for i in range(len(goal_ids)):
            for j in range(i + 1, len(goal_ids)):
                a, b = goal_ids[i], goal_ids[j]
                shared_chains = goal_chains[a] & goal_chains[b]
                if shared_chains:
                    G.add_edge(a, b, etype="chain", weight=len(shared_chains))

        # Edge type 3: parent→child relationships
        self._add_relationship_edges(G)

        return G

    def _build_chain_graph(self) -> nx.Graph:
        """Build graph from chain fragments when no goals exist.

        Each fragment = a node. Edges connect fragments that:
        - share keywords in their descriptions
        - have parent→child import relationships
        Context entries become smaller connected sub-nodes.
        """
        G = nx.Graph()

        # ── Primary nodes: chain fragments ──
        chain_ids: set[str] = set()
        chain_descs: Dict[str, str] = {}
        chain_timestamps: Dict[str, float] = {}

        for frag in self._fragments:
            cid = frag.get("chain_id", "")
            if not cid:
                cid = f"frag_{id(frag)}"
            chain_ids.add(cid)
            chain_descs[cid] = frag.get("description", "") or ""
            chain_timestamps[cid] = frag.get("timestamp", time.time())
            G.add_node(cid, kind="chain", description=chain_descs[cid], timestamp=chain_timestamps[cid])

        if not chain_ids:
            return G

        # ── Build token sets ──
        chain_tokens: Dict[str, Set[str]] = {}
        for cid in chain_ids:
            desc = chain_descs.get(cid, "")
            chain_tokens[cid] = _tokenize(desc)

        # ── Edge type 1: keyword overlap between chain descriptions ──
        chain_list = list(chain_ids)
        for i in range(len(chain_list)):
            for j in range(i + 1, len(chain_list)):
                a, b = chain_list[i], chain_list[j]
                shared = chain_tokens[a] & chain_tokens[b]
                if len(shared) >= 2:
                    G.add_edge(a, b, etype="keyword", weight=len(shared))

        # ── Edge type 2: parent→child relationships from nano ──
        self._add_relationship_edges(G)

        # ── Secondary nodes: context entries connected to their chains ──
        for entry in self._memory_entries:
            entry_chain = entry.get("chain_id", "")
            if entry_chain and entry_chain in chain_ids:
                entry_id = f"ctx_{id(entry)}"
                snap = entry.get("snapshot", "")[:40]
                G.add_node(entry_id, kind="context", snapshot=snap)
                G.add_edge(entry_chain, entry_id, etype="context", weight=0.5)

        return G

    def _add_relationship_edges(self, G: nx.Graph):
        """Add edges for parent→child chain relationships."""
        for rel in self._relationships:
            parent = rel.get("parent", "")
            child = rel.get("child", "")
            if parent and child and (parent in G or child in G):
                # Ensure both nodes exist (add if missing)
                if parent not in G:
                    G.add_node(parent, kind="chain", description=parent)
                if child not in G:
                    G.add_node(child, kind="chain", description=child)
                G.add_edge(parent, child, etype="relationship", weight=2.0)

    def _layout(self, G: nx.Graph):
        if G.number_of_nodes() == 0:
            return
        # Larger k for sparser graphs, more iterations for stability
        node_count = G.number_of_nodes()
        k_val = 3.0 if node_count > 20 else 2.5
        iters = 200 if node_count > 20 else 100
        self._positions = nx.spring_layout(
            G, k=k_val, iterations=iters, seed=42, scale=4.5,
        )

    # ── Drawing ──

    def _clear(self):
        self._plot.clear()
        self._scatter = None
        self._ctx_scatter = None
        self._edge_items.clear()
        self._label_items.clear()
        self._highlighted = None

    def _draw_edges(self, G: nx.Graph):
        """Draw edges, colour-coded by type."""
        kw_lines_x, kw_lines_y = [], []
        ch_lines_x, ch_lines_y = [], []
        ctx_lines_x, ctx_lines_y = [], []

        for u, v, data in G.edges(data=True):
            pu = self._positions.get(u)
            pv = self._positions.get(v)
            if pu is None or pv is None:
                continue
            etype = data.get("etype", "keyword")
            if etype in ("chain", "relationship", "membership"):
                ch_lines_x.extend([pu[0], pv[0], float('nan')])
                ch_lines_y.extend([pu[1], pv[1], float('nan')])
            elif etype == "context":
                ctx_lines_x.extend([pu[0], pv[0], float('nan')])
                ctx_lines_y.extend([pu[1], pv[1], float('nan')])
            else:
                kw_lines_x.extend([pu[0], pv[0], float('nan')])
                kw_lines_y.extend([pu[1], pv[1], float('nan')])

        if kw_lines_x:
            item = pg.PlotDataItem(
                x=kw_lines_x, y=kw_lines_y,
                pen=pg.mkPen(color=EDGE_KEYWORD, width=1.5),
                connect="pairs",
            )
            self._plot.addItem(item)
            self._edge_items.append(item)

        if ch_lines_x:
            item = pg.PlotDataItem(
                x=ch_lines_x, y=ch_lines_y,
                pen=pg.mkPen(color=EDGE_CHAIN, width=1.0, style=Qt.DashLine),
                connect="pairs",
            )
            self._plot.addItem(item)
            self._edge_items.append(item)

        if ctx_lines_x:
            item = pg.PlotDataItem(
                x=ctx_lines_x, y=ctx_lines_y,
                pen=pg.mkPen(color=QColor(180, 180, 140, 60), width=0.5, style=Qt.DotLine),
                connect="pairs",
            )
            self._plot.addItem(item)
            self._edge_items.append(item)

    def _draw_nodes(self, G: nx.Graph):
        """Draw nodes, sized and colored by kind."""
        if G.number_of_nodes() == 0:
            return

        now = time.time()
        chain_spots = []
        ctx_spots = []

        for nid in G.nodes():
            px, py = self._positions[nid]
            ndata = G.nodes[nid]
            kind = ndata.get("kind", "goal")

            if kind == "context":
                # Small, dim context-entry nodes
                ctx_spots.append({
                    "pos": (px, py), "size": 5,
                    "pen": pg.mkPen(color=QColor(180, 180, 140, 80), width=0.5),
                    "brush": pg.mkBrush(QColor(180, 180, 140, 60)),
                    "data": nid,
                })
            elif kind == "goal":
                # Goal nodes — larger, colored by recency
                goal = self._goals.get(nid, {})
                frag_count = max(1, len(goal.get("fragments", [])))
                size = 14 + min(frag_count * 3, 24)
                color = _color_for_age(goal.get("created", now - 86400), now)
                chain_spots.append({
                    "pos": (px, py), "size": size,
                    "pen": pg.mkPen(color=color.lighter(130), width=2.0),
                    "brush": pg.mkBrush(color),
                    "data": nid,
                })
            else:
                # Chain / fragment nodes
                ts = ndata.get("timestamp", now)
                desc = ndata.get("description", "")
                frag_count = len([e for e in self._memory_entries if e.get("chain_id") == nid]) + 1
                size = 10 + min(frag_count * 2, 18)
                color = _color_for_age(ts, now)
                chain_spots.append({
                    "pos": (px, py), "size": size,
                    "pen": pg.mkPen(color=color.lighter(150), width=1.5),
                    "brush": pg.mkBrush(color),
                    "data": nid,
                })

        if chain_spots:
            self._scatter = pg.ScatterPlotItem()
            self._scatter.setData(spots=chain_spots)
            self._scatter.sigClicked.connect(self._on_node_clicked)
            self._plot.addItem(self._scatter)

        if ctx_spots:
            self._ctx_scatter = pg.ScatterPlotItem()
            self._ctx_scatter.setData(spots=ctx_spots)
            self._ctx_scatter.sigClicked.connect(self._on_ctx_clicked)
            self._plot.addItem(self._ctx_scatter)

    def _draw_labels(self):
        """Short label above each primary node (goals and chains, not context entries)."""
        font = QFont("Segoe UI", 7)
        for nid, pos in self._positions.items():
            # Determine node kind from stored goals/fragments
            if nid in self._goals:
                label = (self._goals[nid].get("query") or nid)[:32]
            elif nid.startswith("ctx_"):
                continue  # Skip context entry labels
            else:
                # Chain node — use chain_id or description
                for frag in self._fragments:
                    if frag.get("chain_id") == nid:
                        desc = frag.get("description", "")
                        label = desc[:28] if desc else nid[:14]
                        break
                else:
                    label = nid[:14]
            if not label:
                continue
            text = pg.TextItem(text=label, color=TEXT_COLOR, anchor=(0.5, 1.3))
            text.setFont(font)
            text.setPos(pos[0], pos[1])
            self._plot.addItem(text)
            self._label_items.append(text)

    # ── Interaction ──

    def _on_node_clicked(self, scatter, points):
        if points is None or len(points) == 0:
            return
        nid = points[0].data()
        if nid:
            self._highlight(nid)
            self.node_selected.emit(nid)

    def _on_ctx_clicked(self, scatter, points):
        """Context entry click — highlight its parent chain."""
        if points is None or len(points) == 0:
            return
        nid = points[0].data()
        if nid:
            self.node_selected.emit(nid)

    def _highlight(self, node_id: str):
        self._highlighted = node_id
        if self._scatter is None:
            return
        now = time.time()

        # Rebuild primary spots with highlight
        chain_spots = []
        for nid, pos in self._positions.items():
            if nid.startswith("ctx_"):
                continue  # context spots handled separately
            ndata = {}
            # Try to get node data from goals or fragments
            if nid in self._goals:
                goal = self._goals[nid]
                frag_count = max(1, len(goal.get("fragments", [])))
                size = 14 + min(frag_count * 3, 24)
                color = _color_for_age(goal.get("created", now - 86400), now)
            else:
                for frag in self._fragments:
                    if frag.get("chain_id") == nid:
                        ts = frag.get("timestamp", now)
                        color = _color_for_age(ts, now)
                        frag_count = len([e for e in self._memory_entries if e.get("chain_id") == nid]) + 1
                        size = 10 + min(frag_count * 2, 18)
                        break
                else:
                    color = COLOR_GOAL_OLD
                    size = 10

            if nid == node_id:
                chain_spots.append({
                    "pos": pos, "size": size + 6,
                    "pen": pg.mkPen(color=COLOR_GOAL_HIGHLIGHT, width=3),
                    "brush": pg.mkBrush(COLOR_GOAL_FRESH),
                    "data": nid,
                })
            else:
                faded = QColor(
                    int(color.red() * 0.4),
                    int(color.green() * 0.4),
                    int(color.blue() * 0.4),
                )
                chain_spots.append({
                    "pos": pos, "size": max(6, size - 4),
                    "pen": pg.mkPen(color=faded, width=0.5),
                    "brush": pg.mkBrush(faded),
                    "data": nid,
                })

        self._scatter.setData(spots=chain_spots)

    def minimumSizeHint(self):
        from PyQt5.QtCore import QSize
        return QSize(200, 180)

    def sizeHint(self):
        from PyQt5.QtCore import QSize
        return QSize(600, 350)


# ═══════════════════════════════════════════════════════════════════
# MemoryDetailsPanel — tabbed inspection
# ═══════════════════════════════════════════════════════════════════

class MemoryDetailsPanel(QWidget):
    """Tabbed panel: Goals | Chains | Context.

    Selecting a node (via graph click or table click) populates
    the Chains and Context tabs for that selection.
    """

    node_selected = pg.QtCore.pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._goals: Dict[str, Any] = {}
        self._fragments: List[Dict[str, Any]] = []
        self._memory_entries: List[Dict[str, Any]] = []
        self._selected_id: Optional[str] = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._tabs = QTabWidget()
        self._tabs.setStyleSheet(self._tab_style())

        # ── Tab 1: Goals ──
        goals_tab = QWidget()
        goals_layout = QVBoxLayout(goals_tab)
        goals_layout.setContentsMargins(0, 4, 0, 0)

        self._goals_table = QTableWidget(0, 4)
        self._goals_table.setHorizontalHeaderLabels([
            _("Item"), _("When"), _("Nodes"), _("Status"),
        ])
        self._goals_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._goals_table.setSelectionMode(QTableWidget.SingleSelection)
        self._goals_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._goals_table.setAlternatingRowColors(False)
        hh = self._goals_table.horizontalHeader()
        hh.setStretchLastSection(True)
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self._goals_table.setStyleSheet(self._table_style())
        self._goals_table.cellClicked.connect(self._on_item_clicked)
        goals_layout.addWidget(self._goals_table)
        self._tabs.addTab(goals_tab, _("Items"))

        # ── Tab 2: Executions ──
        exec_tab = QWidget()
        exec_layout = QVBoxLayout(exec_tab)
        exec_layout.setContentsMargins(0, 4, 0, 0)

        self._exec_table = QTableWidget(0, 4)
        self._exec_table.setHorizontalHeaderLabels([
            _("Chain"), _("Description"), _("Context keys"), _("When"),
        ])
        self._exec_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._exec_table.setEditTriggers(QTableWidget.NoEditTriggers)
        eh = self._exec_table.horizontalHeader()
        eh.setStretchLastSection(True)
        eh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        eh.setSectionResizeMode(1, QHeaderView.Stretch)
        eh.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        eh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self._exec_table.setStyleSheet(self._table_style())
        self._exec_label = QLabel(_("Select an item to see its chain executions"))
        self._exec_label.setStyleSheet(
            f"color: rgba(255,255,255,0.3); font-size: 12px; padding: 12px;"
        )
        self._exec_label.setAlignment(Qt.AlignCenter)
        exec_layout.addWidget(self._exec_table)
        exec_layout.addWidget(self._exec_label)
        self._tabs.addTab(exec_tab, _("Executions"))

        # ── Tab 3: Context ──
        ctx_tab = QWidget()
        ctx_layout = QVBoxLayout(ctx_tab)
        ctx_layout.setContentsMargins(0, 4, 0, 0)

        self._ctx_table = QTableWidget(0, 3)
        self._ctx_table.setHorizontalHeaderLabels([
            _("Scope"), _("Content"), _("Node"),
        ])
        self._ctx_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._ctx_table.setEditTriggers(QTableWidget.NoEditTriggers)
        ch = self._ctx_table.horizontalHeader()
        ch.setStretchLastSection(True)
        ch.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        ch.setSectionResizeMode(1, QHeaderView.Stretch)
        ch.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._ctx_table.setStyleSheet(self._table_style())
        self._ctx_label = QLabel(_("Select an item to see its context entries"))
        self._ctx_label.setStyleSheet(
            f"color: rgba(255,255,255,0.3); font-size: 12px; padding: 12px;"
        )
        self._ctx_label.setAlignment(Qt.AlignCenter)
        ctx_layout.addWidget(self._ctx_table)
        ctx_layout.addWidget(self._ctx_label)
        self._tabs.addTab(ctx_tab, _("Context"))

        layout.addWidget(self._tabs)

    # ── Styling ──

    @staticmethod
    def _table_style() -> str:
        return f"""
            QTableWidget {{
                background-color: {WELL_BG};
                color: {TEXT_COLOR};
                border: 1px solid {HAIRLINE};
                border-radius: {RADIUS_MD}px;
                gridline-color: rgba(255,255,255,0.04);
                font-size: 12px;
            }}
            QTableWidget::item {{
                padding: 4px 8px;
            }}
            QTableWidget::item:selected {{
                background-color: {ACCENT_COLOR};
                color: {DARK_GREY};
            }}
            QHeaderView::section {{
                background-color: {BAR_BG};
                color: {TEXT_COLOR};
                padding: 4px 8px;
                border: none;
                border-bottom: 1px solid {HAIRLINE};
                font-weight: 600;
                font-size: 11px;
            }}
        """

    @staticmethod
    def _tab_style() -> str:
        return f"""
            QTabWidget::pane {{
                border: 1px solid {HAIRLINE};
                background-color: {DARK_GREY};
                border-radius: {RADIUS_MD}px;
            }}
            QTabBar::tab {{
                background-color: {MEDIUM_GREY};
                color: {TEXT_COLOR};
                padding: 6px 18px;
                margin-right: 2px;
                border-top-left-radius: {RADIUS_SM}px;
                border-top-right-radius: {RADIUS_SM}px;
                font-size: 12px;
            }}
            QTabBar::tab:selected {{
                background-color: {ACCENT_COLOR};
                color: {DARK_GREY};
            }}
            QTabBar::tab:hover:!selected {{
                background-color: {BLOCK_HOVER};
            }}
        """

    # ── Data ──

    def load_data(
        self,
        goals: Dict[str, Any],
        fragments: List[Dict[str, Any]],
        memory_entries: List[Dict[str, Any]] | None = None,
    ):
        self._goals = goals
        self._fragments = fragments
        self._memory_entries = memory_entries or []
        self._selected_id = None
        self._populate_items()
        self._clear_exec()
        self._clear_ctx()

    def _populate_items(self):
        """Populate the primary table — goals if any, otherwise chain fragments."""
        self._goals_table.setRowCount(0)

        if self._goals:
            # Goal-centric view
            sorted_goals = sorted(
                self._goals.items(),
                key=lambda kv: kv[1].get("created", 0),
                reverse=True,
            )
            for gid, goal in sorted_goals:
                row = self._goals_table.rowCount()
                self._goals_table.insertRow(row)
                query = (goal.get("query", "") or "")[:100]
                ts = goal.get("created", 0)
                when = time.strftime("%m-%d %H:%M", time.localtime(ts)) if ts else "-"
                chain_count = str(len(goal.get("fragments", [])))
                fb_count = str(len(goal.get("feedback", [])))
                self._goals_table.setItem(row, 0, QTableWidgetItem(query))
                self._goals_table.setItem(row, 1, QTableWidgetItem(when))
                self._goals_table.setItem(row, 2, QTableWidgetItem(chain_count))
                self._goals_table.setItem(row, 3, QTableWidgetItem(fb_count))
                self._goals_table.item(row, 0).setData(Qt.UserRole, gid)
        elif self._fragments:
            # Fragment-centric view (no goals)
            sorted_frags = sorted(
                self._fragments,
                key=lambda f: f.get("timestamp", 0),
                reverse=True,
            )
            for frag in sorted_frags:
                row = self._goals_table.rowCount()
                self._goals_table.insertRow(row)
                cid = frag.get("chain_id", "?")
                desc = (frag.get("description", "") or cid)[:100]
                ts = frag.get("timestamp", 0)
                when = time.strftime("%m-%d %H:%M", time.localtime(ts)) if ts else "-"
                ctx_count = str(len(frag.get("context_nodes", [])))
                status = frag.get("status", "?")
                self._goals_table.setItem(row, 0, QTableWidgetItem(desc))
                self._goals_table.setItem(row, 1, QTableWidgetItem(when))
                self._goals_table.setItem(row, 2, QTableWidgetItem(ctx_count))
                self._goals_table.setItem(row, 3, QTableWidgetItem(status))
                self._goals_table.item(row, 0).setData(Qt.UserRole, cid)

    def _on_item_clicked(self, row: int, col: int):
        item = self._goals_table.item(row, 0)
        if item is None:
            return
        sel_id = item.data(Qt.UserRole)
        if not sel_id or sel_id == self._selected_id:
            return
        self._selected_id = sel_id
        self._show_executions(sel_id)
        self._show_context(sel_id)
        self.node_selected.emit(sel_id)

    def select_node(self, node_id: str):
        """Select a node programmatically (from graph click)."""
        for row in range(self._goals_table.rowCount()):
            item = self._goals_table.item(row, 0)
            if item and item.data(Qt.UserRole) == node_id:
                self._goals_table.selectRow(row)
                self._selected_id = node_id
                self._show_executions(node_id)
                self._show_context(node_id)
                return

    def _show_executions(self, sel_id: str):
        """Show fragments/executions for the selected goal or chain."""
        self._exec_table.setRowCount(0)

        # Check if this is a goal
        goal = self._goals.get(sel_id)
        if goal:
            frags = goal.get("fragments", [])
            if not frags:
                self._exec_label.setText(_("No chain executions recorded for this goal"))
                self._exec_label.show()
                return
            self._exec_label.hide()
            for frag in frags:
                row = self._exec_table.rowCount()
                self._exec_table.insertRow(row)
                chain = frag.get("chain_id", "?")
                desc = (frag.get("description", "") or "")[:120]
                keys = ", ".join(
                    k for ctx in frag.get("context_nodes", [])
                    for k in ctx.get("keys", [])
                ) or "-"
                ts = frag.get("timestamp", 0)
                when = time.strftime("%m-%d %H:%M", time.localtime(ts)) if ts else "-"
                self._exec_table.setItem(row, 0, QTableWidgetItem(chain))
                self._exec_table.setItem(row, 1, QTableWidgetItem(desc))
                self._exec_table.setItem(row, 2, QTableWidgetItem(keys[:180]))
                self._exec_table.setItem(row, 3, QTableWidgetItem(when))
            return

        # Check if this is a chain fragment
        for frag in self._fragments:
            if frag.get("chain_id") == sel_id:
                self._exec_label.hide()
                row = self._exec_table.rowCount()
                self._exec_table.insertRow(row)
                chain = frag.get("chain_id", "?")
                desc = (frag.get("description", "") or "")[:120]
                keys = ", ".join(
                    k for ctx in frag.get("context_nodes", [])
                    for k in ctx.get("keys", [])
                ) or "-"
                ts = frag.get("timestamp", 0)
                when = time.strftime("%m-%d %H:%M", time.localtime(ts)) if ts else "-"
                self._exec_table.setItem(row, 0, QTableWidgetItem(chain))
                self._exec_table.setItem(row, 1, QTableWidgetItem(desc))
                self._exec_table.setItem(row, 2, QTableWidgetItem(keys[:180]))
                self._exec_table.setItem(row, 3, QTableWidgetItem(when))
                return

        self._exec_label.setText(_("No execution data for this item"))
        self._exec_label.show()

    def _show_context(self, sel_id: str):
        """Show context entries from nano-graphrag for the selected item."""
        self._ctx_table.setRowCount(0)

        # Find matching context entries
        matching = [
            e for e in self._memory_entries
            if e.get("chain_id") == sel_id or e.get("node_id") == sel_id
        ]

        if not matching:
            # Also try matching by goal membership
            goal = self._goals.get(sel_id)
            if goal:
                for frag in goal.get("fragments", []):
                    cid = frag.get("chain_id", "")
                    matching.extend(
                        e for e in self._memory_entries
                        if e.get("chain_id") == cid
                    )

        if not matching:
            self._ctx_label.setText(_("No context entries for this item"))
            self._ctx_label.show()
            return

        self._ctx_label.hide()
        for entry in matching[:20]:
            row = self._ctx_table.rowCount()
            self._ctx_table.insertRow(row)
            scope = entry.get("scope", "?")
            content = (entry.get("content") or entry.get("snapshot", ""))[:200]
            node = entry.get("node_id", "?")
            self._ctx_table.setItem(row, 0, QTableWidgetItem(scope))
            self._ctx_table.setItem(row, 1, QTableWidgetItem(content))
            self._ctx_table.setItem(row, 2, QTableWidgetItem(node))

    def _clear_exec(self):
        self._exec_table.setRowCount(0)
        self._exec_label.setText(_("Select an item to see its chain executions"))
        self._exec_label.show()

    def _clear_ctx(self):
        self._ctx_table.setRowCount(0)
        self._ctx_label.setText(_("Select an item to see its context entries"))
        self._ctx_label.show()


# ═══════════════════════════════════════════════════════════════════
# AgentMemoryDialog — top-level dialog
# ═══════════════════════════════════════════════════════════════════

class AgentMemoryDialog(ModernDialog):
    """Agent Settings dialog with Memory inspection.

    Top half:  memory graph — chains, context entries, relationships in 2D.
    Bottom:    tabbed details (Items | Executions | Context).
    """

    def __init__(self, parent=None):
        super().__init__(parent, title=_("Agent Settings"), help_topic="general")
        self.setModal(True)
        self._auto_fit = False
        self.resize(DIALOG_LARGE_W, DIALOG_LARGE_H)

        self._goals: Dict[str, Any] = {}
        self._fragments: List[Dict[str, Any]] = []
        self._memory_entries: List[Dict[str, Any]] = []
        self._relationships: List[Dict[str, str]] = []

        self._setup_ui()
        self._refresh_data()

    def _setup_ui(self):
        layout = self.content_layout

        # ── Toolbar ──
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        self._refresh_btn = QPushButton(_("\u21bb Refresh"))
        self._refresh_btn.setCursor(Qt.PointingHandCursor)
        self._refresh_btn.setFixedHeight(28)
        self._refresh_btn.setStyleSheet(
            f"QPushButton {{ background-color: transparent; color: {TEXT_COLOR}; "
            f"border: 1px solid {HAIRLINE}; border-radius: {RADIUS_SM}px; "
            f"padding: 0 14px; font-size: 12px; }}"
            f"QPushButton:hover {{ border-color: {ACCENT_COLOR}; }}"
        )
        self._refresh_btn.clicked.connect(self._refresh_data)
        toolbar.addWidget(self._refresh_btn)

        # ── Semantic search ──
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText(_("Search memory..."))
        self._search_input.setFixedHeight(28)
        self._search_input.setMinimumWidth(160)
        self._search_input.setStyleSheet(
            f"QLineEdit {{ background-color: {CENTER_BG}; color: {TEXT_COLOR}; "
            f"border: 1px solid {HAIRLINE}; border-radius: {RADIUS_SM}px; "
            f"padding: 0 10px; font-size: 12px; }}"
            f"QLineEdit:focus {{ border-color: {ACCENT_COLOR}; }}"
        )
        self._search_input.returnPressed.connect(self._search_memory)
        toolbar.addWidget(self._search_input)

        self._search_btn = QPushButton(_("Query"))
        self._search_btn.setCursor(Qt.PointingHandCursor)
        self._search_btn.setFixedHeight(28)
        self._search_btn.setStyleSheet(
            f"QPushButton {{ background-color: transparent; color: {ACCENT_COLOR}; "
            f"border: 1px solid {ACCENT_COLOR}; border-radius: {RADIUS_SM}px; "
            f"padding: 0 12px; font-size: 12px; }}"
            f"QPushButton:hover {{ background-color: rgba(34,211,238,0.10); }}"
        )
        self._search_btn.clicked.connect(self._search_memory)
        toolbar.addWidget(self._search_btn)

        toolbar.addStretch(1)

        self._clear_btn = QPushButton(_("Clear All Memory"))
        self._clear_btn.setCursor(Qt.PointingHandCursor)
        self._clear_btn.setFixedHeight(28)
        self._clear_btn.setStyleSheet(
            f"QPushButton {{ background-color: transparent; color: {DANGER_COLOR}; "
            f"border: 1px solid {DANGER_COLOR}; border-radius: {RADIUS_SM}px; "
            f"padding: 0 14px; font-size: 12px; font-weight: 600; }}"
            f"QPushButton:hover {{ background-color: rgba(239,68,68,0.12); }}"
        )
        self._clear_btn.clicked.connect(self._clear_memory)
        toolbar.addWidget(self._clear_btn)

        # ── Chain description editor button ──
        self._chain_editor_btn = QPushButton(_("Chain Descriptions"))
        self._chain_editor_btn.setCursor(Qt.PointingHandCursor)
        self._chain_editor_btn.setFixedHeight(28)
        self._chain_editor_btn.setStyleSheet(
            f"QPushButton {{ background-color: transparent; color: {ACCENT_COLOR}; "
            f"border: 1px solid {HAIRLINE}; border-radius: {RADIUS_SM}px; "
            f"padding: 0 14px; font-size: 12px; }}"
            f"QPushButton:hover {{ border-color: {ACCENT_COLOR}; }}"
        )
        self._chain_editor_btn.clicked.connect(self._open_chain_editor)
        toolbar.addWidget(self._chain_editor_btn)

        layout.addLayout(toolbar)

        # ── Graph ──
        graph_box = QWidget()
        graph_box.setMinimumHeight(260)
        graph_box.setStyleSheet(
            f"QWidget {{ background-color: {CENTER_BG}; "
            f"border: 1px solid {HAIRLINE}; border-radius: {RADIUS_MD}px; }}"
        )
        g_layout = QVBoxLayout(graph_box)
        g_layout.setContentsMargins(4, 4, 4, 4)

        self._graph_view = MemoryGraphWidget()
        self._graph_view.node_selected.connect(self._on_graph_selection)
        g_layout.addWidget(self._graph_view)

        layout.addWidget(graph_box, 2)

        # ── Tabs ──
        self._details_panel = MemoryDetailsPanel()
        self._details_panel.node_selected.connect(self._on_table_selection)
        layout.addWidget(self._details_panel, 3)

        # ── Status ──
        self._status_label = QLabel(_("No memory data"))
        self._status_label.setStyleSheet(
            f"color: rgba(255,255,255,0.4); font-size: 11px; padding: 4px 0;"
        )
        layout.addWidget(self._status_label)

    # ── Data ──

    def _refresh_data(self):
        try:
            self._goals = {}
            self._fragments = []
            self._memory_entries = []
            self._relationships = []

            # Load unified data into graph and details panel
            self._graph_view.load_data(
                self._goals, self._fragments,
                self._memory_entries, self._relationships,
            )
            self._details_panel.load_data(
                self._goals, self._fragments,
                self._memory_entries,
            )

            n_goals = len(self._goals)
            n_frags = len(self._fragments)
            n_mem = len(self._memory_entries)
            n_rel = len(self._relationships)

            if n_goals == 0 and n_frags == 0 and n_mem == 0:
                self._status_label.setText(
                    _("No memory yet — run some agent tasks first")
                )
            else:
                parts = []
                if n_goals:
                    parts.append(_("{} goal(s)").format(n_goals))
                if n_frags:
                    parts.append(_("{} execution(s)").format(n_frags))
                if n_mem:
                    parts.append(_("{} context entry(s)").format(n_mem))
                if n_rel:
                    parts.append(_("{} relationship(s)").format(n_rel))
                self._status_label.setText(", ".join(parts))
        except Exception as e:
            logger.exception("Failed to load memory")
            self._status_label.setText(
                _("Error: {}").format(str(e))
            )

    # ── Semantic search ──

    def _search_memory(self):
        query = (self._search_input.text() or "").strip()
        if not query:
            return
        self._status_label.setText(
            _("Memory search is unavailable (GraphRAG has been removed)")
        )

    # ── Cross-wiring ──

    def _on_graph_selection(self, node_id: str):
        self._details_panel.select_node(node_id)

    def _on_table_selection(self, node_id: str):
        pass  # graph handles its own highlight via _highlight

    # ── Clear ──

    def _clear_memory(self):
        reply = QMessageBox.warning(
            self,
            _("Clear All Memory"),
            _("This will permanently delete all stored goals, "
              "chain executions, and context entries.\n\n"
              "This cannot be undone. Continue?"),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        try:
            self._goals.clear()
            self._fragments.clear()
            self._memory_entries.clear()
            self._relationships.clear()
            self._graph_view.load_data({}, [])
            self._details_panel.load_data({}, [])
            self._status_label.setText(_("Memory cleared"))
            logger.info("Agent memory cleared via settings dialog")
        except Exception as e:
            logger.exception("Clear memory failed")
            QMessageBox.critical(
                self, _("Error"),
                _("Failed to clear memory: {}").format(str(e)),
            )

    def _open_chain_editor(self):
        """Open the chain description editor dialog."""
        try:
            dlg = ChainDescriptionEditor(self)
            dlg.exec_()
        except Exception:
            logger.exception("Failed to open chain editor")


# ═══════════════════════════════════════════════════════════════════
# ChainDescriptionEditor — manually edit tool chain descriptions
# ═══════════════════════════════════════════════════════════════════


class ChainDescriptionEditor(ModernDialog):
    """Dialog to view and edit descriptions of all non-system chains.

    The description field is what the LLM router uses for tool selection.
    Editing it directly lets users tune routing behavior without relying
    on GraphRAG feedback adaptation.
    """

    def __init__(self, parent=None):
        super().__init__(parent, title=_("Edit Chain Descriptions"), help_topic="general")
        self.setModal(True)
        self._chains: list[dict] = []  # {name, path, description}
        self._build_ui()
        self._scan_chains()

    def _build_ui(self):
        hsplit = QHBoxLayout(self.content_layout)
        hsplit.setSpacing(10)

        # ── Left: chain list ──
        left = QVBoxLayout()
        left.setSpacing(4)
        left_label = QLabel(_("Chains (non-system)"))
        left_label.setStyleSheet(f"color: rgba(255,255,255,0.6); font-size: 11px;")
        left.addWidget(left_label)

        self._chain_list = QListWidget()
        self._chain_list.setMinimumWidth(180)
        self._chain_list.setStyleSheet(
            f"QListWidget {{ background-color: {CENTER_BG}; color: {TEXT_COLOR}; "
            f"border: 1px solid {HAIRLINE}; border-radius: {RADIUS_SM}px; "
            f"font-size: 12px; padding: 4px; }}"
            f"QListWidget::item:selected {{ background-color: {ACCENT_COLOR}; color: {DARK_GREY}; }}"
        )
        self._chain_list.currentRowChanged.connect(self._on_selection_changed)
        left.addWidget(self._chain_list, 1)
        hsplit.addLayout(left, 1)

        # ── Right: description editor ──
        right = QVBoxLayout()
        right.setSpacing(4)
        right_label = QLabel(_("Description (used by LLM router for tool selection)"))
        right_label.setStyleSheet(f"color: rgba(255,255,255,0.6); font-size: 11px;")
        right.addWidget(right_label)

        self._desc_editor = QTextEdit()
        self._desc_editor.setPlaceholderText(_("Select a chain to edit its description..."))
        self._desc_editor.setStyleSheet(
            f"QTextEdit {{ background-color: {CENTER_BG}; color: {TEXT_COLOR}; "
            f"border: 1px solid {HAIRLINE}; border-radius: {RADIUS_SM}px; "
            f"padding: 8px; font-size: 13px; }}"
            f"QTextEdit:focus {{ border-color: {ACCENT_COLOR}; }}"
        )
        right.addWidget(self._desc_editor, 1)

        # ── Save button ──
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        btn_row.addStretch(1)

        self._save_btn = QPushButton(_("Save Description"))
        self._save_btn.setCursor(Qt.PointingHandCursor)
        self._save_btn.setFixedHeight(30)
        self._save_btn.setStyleSheet(
            f"QPushButton {{ background-color: {ACCENT_COLOR}; color: {BTN_PRIMARY_TEXT}; "
            f"border: 0px; border-radius: {RADIUS_SM}px; padding: 0 20px; "
            f"font-size: 12px; font-weight: 700; }}"
            f"QPushButton:hover {{ background-color: {ACCENT_HOVER}; }}"
        )
        self._save_btn.clicked.connect(self._save_description)
        btn_row.addWidget(self._save_btn)

        right.addLayout(btn_row)
        hsplit.addLayout(right, 2)

    def _scan_chains(self):
        """Scan the chains directory for all non-system chain JSON files."""
        self._chains.clear()
        self._chain_list.clear()
        chains_dir = os.path.join(os.path.dirname(__file__), "..", "..", "chains")
        chains_dir = os.path.abspath(chains_dir)
        if not os.path.isdir(chains_dir):
            return
        for root, _dirs, files in os.walk(chains_dir):
            for fname in sorted(files):
                if not fname.endswith(".json"):
                    continue
                if "BASE_SYSTEM_CHAIN" in fname.upper():
                    continue
                path = os.path.join(root, fname)
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                except Exception:
                    continue
                name = os.path.splitext(fname)[0]
                desc = (cfg.get("description") or "").strip()
                self._chains.append({"name": name, "path": path, "description": desc})
                self._chain_list.addItem(f"{name}" if desc else f"{name}  (no description)")

    def _on_selection_changed(self, row: int):
        if row < 0 or row >= len(self._chains):
            self._desc_editor.clear()
            self._desc_editor.setEnabled(False)
            self._save_btn.setEnabled(False)
            return
        chain = self._chains[row]
        self._desc_editor.setEnabled(True)
        self._save_btn.setEnabled(True)
        self._desc_editor.setPlainText(chain["description"])

    def _save_description(self):
        row = self._chain_list.currentRow()
        if row < 0 or row >= len(self._chains):
            return
        chain = self._chains[row]
        new_desc = self._desc_editor.toPlainText().strip()
        try:
            with open(chain["path"], "r", encoding="utf-8") as f:
                cfg = json.load(f)
            cfg["description"] = new_desc
            with open(chain["path"], "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
            chain["description"] = new_desc
            # Update list item text
            item = self._chain_list.item(row)
            if item:
                item.setText(f"{chain['name']}" if new_desc else f"{chain['name']}  (no description)")
            logger.info("Chain editor: saved description for %s", chain["name"])
        except Exception as exc:
            logger.exception("Chain editor: save failed")
            QMessageBox.warning(self, _("Save Error"), str(exc))
