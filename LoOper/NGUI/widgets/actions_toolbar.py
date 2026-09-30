import logging
import os
import json
import math
from difflib import SequenceMatcher
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QToolButton,
    QFrame, QSizePolicy, QApplication, QGraphicsDropShadowEffect,
    QLineEdit, QComboBox, QScrollArea, QGridLayout
)
from PyQt5.QtCore import Qt, QPoint, QSize, QTimer, QMimeData, QPropertyAnimation, QEasingCurve, QAbstractAnimation
from PyQt5.QtCore import QFileSystemWatcher
from PyQt5.QtGui import QPixmap, QPainter, QColor, QIcon, QDrag
from PyQt5.QtWidgets import QApplication

try:
    from pytablericons import TablerIcons, OutlineIcon
except Exception:
    TablerIcons = None
    OutlineIcon = None

try:
    from PIL.ImageQt import ImageQt
except Exception:
    ImageQt = None

from ..constants import (
    DARK_GREY, LIGHT_GREY, MEDIUM_GREY, TEXT_COLOR, ACCENT_COLOR,
    BLOCK_COLOR, BLOCK_HOVER, CHAIN_IMPORT_COLOR, BAR_BG,
    SIDE_PANEL_BG, GRAPH_PLANE
)
from .collapsible_toolbar import make_toggle_triangle_icon
from ..i18n import _
from ..dialogs.toggle_switch import ModernToggle

logger = logging.getLogger(__name__)

class SearchResultButton(QPushButton):
    def __init__(self, item_type: str, title: str, description: str, file_path: str, graph_view, parent=None):
        super().__init__(title, parent)
        self.item_type = item_type
        self.file_path = file_path
        self.graph_view = graph_view
        self._drag_start_pos = None
        self.setCursor(Qt.OpenHandCursor)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        bg_color = BLOCK_COLOR if item_type == "sequence" else CHAIN_IMPORT_COLOR
        self.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {bg_color};
                color: {TEXT_COLOR};
                border: 1px solid {LIGHT_GREY};
                padding: 2px 8px;
                border-radius: 10px;
                min-width: 80px;
                min-height: 26px;
                max-width: 220px;
                font-weight: 600;
                text-align: center;
            }}
            QPushButton:hover {{ border-color: {ACCENT_COLOR}; }}
            QPushButton:pressed {{ background-color: {BLOCK_HOVER}; }}
            """
        )
        if description:
            self.setToolTip(f"{title}\n{description}")
        else:
            self.setToolTip(title)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_start_pos = event.pos()
        return super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_start_pos is None:
            return super().mouseMoveEvent(event)
        if (event.pos() - self._drag_start_pos).manhattanLength() >= 8:
            mime = QMimeData()
            if self.item_type == "sequence":
                mime.setData('application/x-looper-node-type', b'sequence')
                mime.setText(self.file_path)
                mime.setData('application/x-looper-sequence-file', self.file_path.encode('utf-8'))
            else:
                mime.setData('application/x-looper-node-type', b'chain_import')
                mime.setText(self.file_path)
                mime.setData('application/x-looper-chain-file', self.file_path.encode('utf-8'))
            pixmap = QPixmap(self.size())
            pixmap.fill(Qt.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setBrush(QColor(self.palette().button().color()))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(self.rect(), 10, 10)
            painter.end()
            drag = QDrag(self)
            drag.setMimeData(mime)
            drag.setPixmap(pixmap)
            drag.setHotSpot(event.pos() - self.rect().topLeft())
            drag.exec_(Qt.CopyAction)
            return
        return super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._drag_start_pos is not None and (event.pos() - self._drag_start_pos).manhattanLength() < 8:
            try:
                if hasattr(self.graph_view, 'node_operations'):
                    if self.item_type == "sequence":
                        self.graph_view.node_operations.add_sequence_from_file(self.file_path)
                    else:
                        self.graph_view.node_operations.add_chain_import_from_file(self.file_path)
            except Exception:
                pass
        self._drag_start_pos = None
        return super().mouseReleaseEvent(event)

def load_tabler_icon(name: str, size: int) -> QIcon:
    try:
        if TablerIcons and OutlineIcon:
            icon_enum = getattr(OutlineIcon, name, None)
            if icon_enum is not None:
                img = TablerIcons.load(icon_enum, size=size, color=TEXT_COLOR, stroke_width=2.0)
                try:
                    return QIcon(img.toqpixmap())
                except Exception:
                    qimg = ImageQt(img) if ImageQt else None
                    if qimg:
                        return QIcon(QPixmap.fromImage(qimg))
    except Exception:
        pass
    # Fallback: draw minimal chevrons with DPR-aware pixmap
    dpr = 1.0
    try:
        scr = QApplication.primaryScreen()
        dpr = float(getattr(scr, 'devicePixelRatio', lambda: 1.0)()) if scr else 1.0
    except Exception:
        dpr = 1.0
    w = int(size * dpr)
    h = int(size * dpr)
    pix = QPixmap(w, h)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    pen = p.pen()
    pen.setColor(QColor(TEXT_COLOR))
    try:
        pen.setWidthF(2.0)
    except Exception:
        pen.setWidth(2)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    s = float(size) * dpr
    if name == 'CHEVRON_RIGHT':
        p.drawPolyline([QPoint(int(0.35*s), int(0.25*s)), QPoint(int(0.65*s), int(0.5*s)), QPoint(int(0.35*s), int(0.75*s))])
    elif name == 'CHEVRON_LEFT':
        p.drawPolyline([QPoint(int(0.65*s), int(0.25*s)), QPoint(int(0.35*s), int(0.5*s)), QPoint(int(0.65*s), int(0.75*s))])
    elif name == 'CHEVRON_UP':
        p.drawPolyline([QPoint(int(0.25*s), int(0.65*s)), QPoint(int(0.5*s), int(0.35*s)), QPoint(int(0.75*s), int(0.65*s))])
    else:  # CHEVRON_DOWN
        p.drawPolyline([QPoint(int(0.25*s), int(0.35*s)), QPoint(int(0.5*s), int(0.65*s)), QPoint(int(0.75*s), int(0.35*s))])
    p.end()
    try:
        pix.setDevicePixelRatio(dpr)
    except Exception:
        pass
    return QIcon(pix)

class SearchBarFrame(QFrame):
    def __init__(self, graph_view, parent=None):
        super().__init__(parent)
        self.graph_view = graph_view
        self._search_items = []
        self._index_dirty = True
        self._watcher = None
        self._watched_paths = set()
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 2, 0, 2)
        root.setSpacing(4)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.search_field = QLineEdit(self)
        self.search_field.setPlaceholderText(_("Search sequences/chains"))
        row.addWidget(self.search_field)
        root.addLayout(row)
        self._results_frame = QFrame(self)
        self._results_frame.setObjectName("searchResultsFrame")
        self._results_frame.setStyleSheet(
            f"QFrame#searchResultsFrame {{ background-color: {DARK_GREY}; border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 8px; }}"
        )
        results_layout = QVBoxLayout(self._results_frame)
        results_layout.setContentsMargins(5, 5, 5, 5)
        results_layout.setSpacing(5)
        self._results_scroll = QScrollArea(self._results_frame)
        self._results_scroll.setWidgetResizable(True)
        self._results_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._results_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._results_container = QFrame(self._results_scroll)
        self._results_layout = QGridLayout(self._results_container)
        self._results_layout.setContentsMargins(0, 0, 0, 0)
        self._results_layout.setHorizontalSpacing(5)
        self._results_layout.setVerticalSpacing(5)
        self._results_scroll.setWidget(self._results_container)
        results_layout.addWidget(self._results_scroll)
        root.addWidget(self._results_frame)
        self._results_frame.setVisible(False)
        self._install_watchers()
        self._rebuild_search_index()
        try:
            self.search_field.textChanged.connect(self._on_search_changed)
        except Exception:
            pass
    
    def _install_watchers(self):
        try:
            self._watcher = QFileSystemWatcher(self)
            try:
                self._watcher.directoryChanged.connect(self._on_search_dirs_changed)
                self._watcher.fileChanged.connect(self._on_search_dirs_changed)
            except Exception:
                pass
            self._sync_watched_paths()
        except Exception:
            self._watcher = None
            self._watched_paths = set()
    
    def _sync_watched_paths(self):
        if self._watcher is None:
            return
        desired = set()
        try:
            seq_folder = getattr(self.graph_view, 'sequences_folder', None)
            if seq_folder and os.path.isdir(seq_folder):
                desired.add(os.path.abspath(seq_folder))
                nested = os.path.join(seq_folder, 'sequences')
                if os.path.isdir(nested):
                    desired.add(os.path.abspath(nested))
        except Exception:
            pass
        try:
            chain_folder = getattr(self.graph_view, 'chains_folder', None)
            if chain_folder and os.path.isdir(chain_folder):
                desired.add(os.path.abspath(chain_folder))
        except Exception:
            pass

        to_add = sorted(desired - self._watched_paths)
        to_remove = sorted(self._watched_paths - desired)
        for p in to_remove:
            try:
                self._watcher.removePath(p)
            except Exception:
                pass
            try:
                self._watched_paths.discard(p)
            except Exception:
                pass
        for p in to_add:
            try:
                ok = self._watcher.addPath(p)
                if ok:
                    self._watched_paths.add(p)
            except Exception:
                pass
    
    def _on_search_dirs_changed(self, path=None):
        self._index_dirty = True
        try:
            self._sync_watched_paths()
        except Exception:
            pass
        try:
            if str(self.search_field.text()).strip():
                QTimer.singleShot(0, self._on_search_changed)
        except Exception:
            pass

    def _tokenize_text(self, text: str):
        tokens = []
        current = []
        for ch in str(text).lower():
            if ch.isalnum():
                current.append(ch)
            else:
                if current:
                    tokens.append("".join(current))
                    current = []
        if current:
            tokens.append("".join(current))
        return tokens

    def _text_to_vector(self, text: str):
        tokens = self._tokenize_text(text)
        vec = {}
        for t in tokens:
            vec[t] = vec.get(t, 0.0) + 1.0
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        for k in list(vec.keys()):
            vec[k] = vec[k] / norm
        return vec

    def _cosine_similarity(self, v1, v2):
        if not v1 or not v2:
            return 0.0
        if len(v1) < len(v2):
            v1, v2 = v2, v1
        s = 0.0
        for token, w in v2.items():
            s += w * v1.get(token, 0.0)
        return s

    def _string_similarity(self, a: str, b: str):
        try:
            return SequenceMatcher(None, a, b).ratio()
        except Exception:
            return 0.0

    def _partial_similarity(self, q_tokens, i_tokens):
        if not q_tokens or not i_tokens:
            return 0.0
        total = 0.0
        for qt in q_tokens:
            best = 0.0
            for it in i_tokens:
                if qt == it:
                    if 1.0 > best:
                        best = 1.0
                    continue
                if qt in it:
                    score = len(qt) / max(1, len(it))
                    if score > best:
                        best = score
                else:
                    ratio = self._string_similarity(qt, it)
                    if ratio > best:
                        best = ratio
            total += best
        return total / max(1, len(q_tokens))
    
    def _ordered_contiguous_similarity(self, q: str, item_text: str):
        try:
            ql = str(q).lower()
            tl = str(item_text).lower()
            if not ql or not tl:
                return 0.0
            if ql in tl:
                return 1.0
            m = SequenceMatcher(None, ql, tl).find_longest_match(0, len(ql), 0, len(tl))
            longest = getattr(m, 'size', 0)
            return float(longest) / max(1, len(ql))
        except Exception:
            return 0.0

    def _rebuild_search_index(self):
        items = []
        seen = set()
        try:
            self._sync_watched_paths()
        except Exception:
            pass
        seq_folder = getattr(self.graph_view, 'sequences_folder', None)
        try:
            if seq_folder and os.path.isdir(seq_folder):
                files = [f for f in os.listdir(seq_folder) if f.lower().endswith('.json')]
                nested = os.path.join(seq_folder, 'sequences')
                if os.path.isdir(nested):
                    extra = [f for f in os.listdir(nested) if f.lower().endswith('.json')]
                    files = sorted(set(files + extra))
                else:
                    files = sorted(files)
                for name in files:
                    p1 = os.path.join(seq_folder, name)
                    p2 = os.path.join(seq_folder, 'sequences', name)
                    full_path = p1 if os.path.exists(p1) else p2
                    if full_path in seen:
                        continue
                    seen.add(full_path)
                    base_name = os.path.splitext(name)[0]
                    description = ""
                    try:
                        with open(full_path, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                        base_name = data.get('name', base_name)
                        description = data.get('description', '')
                    except Exception:
                        pass
                    text = " ".join([str(base_name), str(description), full_path])
                    vec = self._text_to_vector(text)
                    toks = self._tokenize_text(text)
                    items.append(
                        {
                            "type": "sequence",
                            "name": str(base_name),
                            "description": str(description),
                            "path": full_path,
                            "vector": vec,
                            "tokens": toks,
                            "text": text.lower(),
                        }
                    )
        except Exception:
            pass
        chain_folder = getattr(self.graph_view, 'chains_folder', None)
        try:
            if chain_folder and os.path.isdir(chain_folder):
                files = sorted([f for f in os.listdir(chain_folder) if f.lower().endswith('.json')])
                for name in files:
                    full_path = os.path.join(chain_folder, name)
                    if full_path in seen:
                        continue
                    seen.add(full_path)
                    base_name = os.path.splitext(name)[0]
                    description = ""
                    try:
                        with open(full_path, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                        base_name = data.get('name', base_name)
                        description = data.get('description', '')
                    except Exception:
                        pass
                    text = " ".join([str(base_name), str(description), full_path])
                    vec = self._text_to_vector(text)
                    toks = self._tokenize_text(text)
                    items.append(
                        {
                            "type": "chain",
                            "name": str(base_name),
                            "description": str(description),
                            "path": full_path,
                            "vector": vec,
                            "tokens": toks,
                            "text": text.lower(),
                        }
                    )
        except Exception:
            pass
        self._search_items = items
        self._index_dirty = False

    def _clear_results(self):
        if not hasattr(self, "_results_layout") or self._results_layout is None:
            return
        while self._results_layout.count():
            item = self._results_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _on_search_changed(self):
        try:
            text = self.search_field.text()
        except Exception:
            text = ""
        mode = "both"
        if self._index_dirty:
            try:
                self._rebuild_search_index()
            except Exception:
                pass
        self._clear_results()
        if not str(text).strip():
            self._results_frame.setVisible(False)
            return
        query_vec = self._text_to_vector(text)
        query_tokens = self._tokenize_text(text)
        scored = []
        for item in self._search_items:
            cos = self._cosine_similarity(item.get("vector"), query_vec)
            part = self._partial_similarity(query_tokens, item.get("tokens", []))
            ordered = self._ordered_contiguous_similarity(" ".join(query_tokens), item.get("text", ""))
            score = 0.5 * ordered + 0.3 * part + 0.2 * cos
            # Trim weak matches aggressively: require some meaningful alignment
            min_required = 0.15 if len(" ".join(query_tokens)) <= 3 else 0.25
            if ordered < min_required and part < min_required and cos < (min_required * 0.8):
                continue
            scored.append((score, item))
        scored.sort(key=lambda x: x[0], reverse=True)
        max_results = 30
        columns = 4
        for index, (score, item) in enumerate(scored[:max_results]):
            title = f"[{item['type']}]" + " " + item.get("name", "")
            btn = SearchResultButton(
                item.get("type"),
                title,
                item.get("description", ""),
                item.get("path", ""),
                self.graph_view,
                self._results_container,
            )
            row = index // columns
            col = index % columns
            self._results_layout.addWidget(btn, row, col)
        self._results_frame.setVisible(bool(scored))


class ActionsToolbar(QWidget):
    def __init__(self, graph_view, parent=None):
        super().__init__(parent)
        self.graph_view = graph_view
        self._collapsed = False
        self._build_ui()

    def _build_ui(self):
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet("background: transparent;")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        # Content frame (framed)
        self._content_frame = QFrame(self)
        self._content_frame.setObjectName("actionsContent")
        self._content_frame.setStyleSheet(
            f"QFrame#actionsContent {{ background-color: {SIDE_PANEL_BG}; border-radius: 10px; }}"
        )
        layout = QHBoxLayout(self._content_frame)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        layout.setAlignment(Qt.AlignVCenter)
        layout.addWidget(self._make_record_icon_button(), alignment=Qt.AlignVCenter)
        layout.addWidget(self._make_custom_icon_button(self._icon_open(), self._open_chain, tooltip=_("Load Chain"), label_text=_("Load")), alignment=Qt.AlignVCenter)
        layout.addWidget(self._make_custom_icon_button(self._icon_save(), self._save_chain, tooltip=_("Save Chain"), label_text=_("Save")), alignment=Qt.AlignVCenter)
        layout.addWidget(self._make_custom_icon_button(self._icon_play(), self._run_chain, tooltip=_("Run Chain"), label_text=_("Run")), alignment=Qt.AlignVCenter)
        layout.addWidget(self._make_custom_icon_button(self._icon_delete(), self._delete_selected, tooltip=_("Delete Selected"), label_text=_("Delete")), alignment=Qt.AlignVCenter)
        layout.addWidget(self._make_custom_icon_button(self._icon_copy(), self._copy_selected, tooltip=_("Copy Selected"), label_text=_("Copy")), alignment=Qt.AlignVCenter)
        layout.addWidget(self._make_custom_icon_button(self._icon_paste(), self._paste_selected, tooltip=_("Paste"), label_text=_("Paste")), alignment=Qt.AlignVCenter)
        layout.addWidget(self._make_custom_icon_button(self._icon_undo(), self._undo_selected, tooltip=_("Undo Ctrl+Z"), label_text=_("Undo")), alignment=Qt.AlignVCenter)
        layout.addWidget(self._make_custom_icon_button(self._icon_redo(), self._redo_selected, tooltip=_("Redo Ctrl+Y"), label_text=_("Redo")), alignment=Qt.AlignVCenter)
        outer.addWidget(self._content_frame)

        # Floating toggle button (solid triangle) centered under the actions frame
        self._toggle_btn_actions = QToolButton(self.graph_view)
        self._toggle_btn_actions.setToolTip(_("Toggle Actions Toolbar"))
        ico2 = make_toggle_triangle_icon('UP', 14, GRAPH_PLANE)
        self._toggle_btn_actions.setIcon(ico2)
        self._toggle_btn_actions.setIconSize(QSize(14, 14))
        self._toggle_btn_actions.setAutoRaise(True)
        self._toggle_btn_actions.setCursor(Qt.PointingHandCursor)
        self._toggle_btn_actions.setFixedSize(24, 28)
        self._toggle_btn_actions.setStyleSheet(
            f"QToolButton {{ background-color: transparent; border: none; }}"
            f"QToolButton:hover {{ background-color: rgba(255,255,255,0.06); border-radius: 4px; }}"
        )
        self._toggle_btn_actions.clicked.connect(self._toggle_actions_toolbar)
        try:
            QTimer.singleShot(0, self._position_actions_toggle)
        except Exception:
            pass

    def _toggle_actions_toolbar(self):
        try:
            self._collapsed = not self._collapsed
            if self._collapsed:
                start_h = max(0, self._content_frame.height())
                self._anim_actions = QPropertyAnimation(self._content_frame, b"maximumHeight")
                self._anim_actions.setDuration(200)
                self._anim_actions.setStartValue(start_h)
                self._anim_actions.setEndValue(0)
                self._anim_actions.setEasingCurve(QEasingCurve.InOutQuad)
                self._anim_actions.finished.connect(self._on_actions_collapse)
                self._anim_actions.start(QAbstractAnimation.DeleteWhenStopped)
            else:
                self._content_frame.setVisible(True)
                self._content_frame.setMaximumHeight(0)
                QApplication.processEvents()
                target_h = self._content_frame.minimumSizeHint().height()
                self._anim_actions = QPropertyAnimation(self._content_frame, b"maximumHeight")
                self._anim_actions.setDuration(200)
                self._anim_actions.setStartValue(0)
                self._anim_actions.setEndValue(target_h)
                self._anim_actions.setEasingCurve(QEasingCurve.InOutQuad)
                self._anim_actions.finished.connect(self._on_actions_expand)
                self._anim_actions.start(QAbstractAnimation.DeleteWhenStopped)
            direction = 'DOWN' if self._collapsed else 'UP'
            color = SIDE_PANEL_BG if self._collapsed else GRAPH_PLANE
            ico = make_toggle_triangle_icon(direction, 14, color)
            self._toggle_btn_actions.setIcon(ico)
            self._toggle_btn_actions.setIconSize(QSize(14, 14))
            QTimer.singleShot(250, self._position_actions_toggle)
        except Exception:
            pass

    def _on_actions_collapse(self):
        self._content_frame.setVisible(False)
        self._content_frame.setMaximumHeight(16777215)

    def _on_actions_expand(self):
        self._content_frame.setMaximumHeight(16777215)
        QTimer.singleShot(0, self._position_actions_toggle)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        try:
            QTimer.singleShot(0, self._position_actions_toggle)
        except Exception:
            pass

    def _position_actions_toggle(self):
        try:
            if not hasattr(self, '_toggle_btn_actions') or self._toggle_btn_actions is None:
                return
            
            # Always center horizontally relative to the ActionsToolbar itself
            toolbar_top_left = self.mapTo(self.graph_view, QPoint(0, 0))
            x = toolbar_top_left.x() + self.width() // 2 - 14
            
            # Position Y just below the toolbar
            y = toolbar_top_left.y() + self.height() + 4
                
            self._toggle_btn_actions.move(x, y)
            self._toggle_btn_actions.raise_()
        except Exception:
            pass

    def _make_custom_icon_button(self, icon: QIcon, handler, tooltip=None, label_text=None):
        btn = QPushButton(label_text or "", self)
        btn.clicked.connect(handler)
        if tooltip:
            btn.setToolTip(tooltip)
        btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        try:
            btn.setFixedHeight(26)
        except Exception:
            pass
        btn.setIcon(icon)
        btn.setIconSize(QSize(18, 18))
        btn.setStyleSheet(
            f"""
            QPushButton {{
                background: rgba(255, 255, 255, 0.05);
                color: {TEXT_COLOR};
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 8px;
                padding: 1px 6px;
                min-width: 64px;
                min-height: 22px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background: rgba(255, 255, 255, 0.08);
                border-color: {ACCENT_COLOR};
            }}
            QPushButton:pressed {{
                background: rgba(255, 255, 255, 0.12);
            }}
            """
        )
        return btn

    def _make_record_icon_button(self):
        btn = QPushButton(_("Record"), self)
        btn.clicked.connect(lambda: self.graph_view.record_new_sequence())
        btn.setToolTip(_("Record Sequence"))
        btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        try:
            btn.setFixedHeight(26)
        except Exception:
            pass
        pix = QPixmap(22, 22)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(220, 0, 0))
        p.setPen(Qt.NoPen)
        p.drawEllipse(3, 3, 16, 16)
        p.end()
        btn.setIcon(QIcon(pix))
        btn.setIconSize(QSize(18, 18))
        btn.setStyleSheet(
            f"""
            QPushButton {{
                background: rgba(255, 255, 255, 0.05);
                color: {TEXT_COLOR};
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 8px;
                padding: 1px 6px;
                min-width: 64px;
                min-height: 22px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background: rgba(255, 255, 255, 0.08);
                border-color: {ACCENT_COLOR};
            }}
            QPushButton:pressed {{
                background: rgba(255, 255, 255, 0.12);
            }}
            """
        )
        return btn

    def _run_chain(self):
        try:
            window = self.graph_view.window()
            if hasattr(window, 'run_chain'):
                window.run_chain()
            else:
                logger.warning('MainWindow.run_chain not found')
        except Exception as e:
            logger.error(f"Run chain error: {e}")

    def _delete_selected(self):
        try:
            if hasattr(self.graph_view, 'delete_selected_nodes'):
                self.graph_view.delete_selected_nodes()
        except Exception as e:
            logger.error(f"Delete selected error: {e}")

    def _copy_selected(self):
        try:
            if hasattr(self.graph_view, 'copy_selected_nodes'):
                self.graph_view.copy_selected_nodes()
        except Exception as e:
            logger.error(f"Copy selected error: {e}")

    def _undo_selected(self):
        try:
            if hasattr(self.graph_view, 'undo'):
                self.graph_view.undo()
        except Exception as e:
            logger.error(f"Undo error: {e}")

    def _redo_selected(self):
        try:
            if hasattr(self.graph_view, 'redo'):
                self.graph_view.redo()
        except Exception as e:
            logger.error(f"Redo error: {e}")

    def _paste_selected(self):
        try:
            if hasattr(self.graph_view, 'paste_copied_nodes'):
                self.graph_view.paste_copied_nodes()
        except Exception as e:
            logger.error(f"Paste error: {e}")

    def _open_chain(self):
        try:
            window = self.graph_view.window()
            if hasattr(window, 'open_chain'):
                window.open_chain()
            else:
                logger.warning('MainWindow.open_chain not found')
        except Exception as e:
            logger.error(f"Open chain error: {e}")

    def _save_chain(self):
        try:
            window = self.graph_view.window()
            if hasattr(window, 'save_chain'):
                window.save_chain()
            else:
                logger.warning('MainWindow.save_chain not found')
        except Exception as e:
            logger.error(f"Save chain error: {e}")

    def _icon_play(self) -> QIcon:
        if TablerIcons and OutlineIcon:
            try:
                img = TablerIcons.load(OutlineIcon.PLAYER_PLAY, size=24, color=TEXT_COLOR, stroke_width=2.0)
                return QIcon(img.toqpixmap())
            except Exception:
                pass
        pix = QPixmap(24, 24)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        pen = p.pen()
        pen.setColor(QColor(TEXT_COLOR))
        pen.setWidth(2)
        p.setPen(pen)
        p.setBrush(QColor(TEXT_COLOR))
        points = [
            QPoint(6, 5), QPoint(19, 12), QPoint(6, 19)
        ]
        p.drawPolygon(*points)
        p.end()
        return QIcon(pix)

    def _icon_open(self) -> QIcon:
        if TablerIcons and OutlineIcon:
            try:
                img = TablerIcons.load(OutlineIcon.FOLDER_OPEN, size=24, color=TEXT_COLOR, stroke_width=2.0)
                return QIcon(img.toqpixmap())
            except Exception:
                pass
        pix = QPixmap(24, 24)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        pen = p.pen()
        pen.setColor(QColor(TEXT_COLOR))
        pen.setWidth(2)
        p.setPen(pen)
        # folder outline
        p.drawRoundedRect(4, 8, 16, 10, 3, 3)
        p.drawLine(4, 8, 10, 8)
        p.drawLine(10, 8, 12, 6)
        # up arrow inside
        p.drawLine(12, 15, 12, 11)
        p.drawLine(12, 11, 9, 14)
        p.drawLine(12, 11, 15, 14)
        p.end()
        return QIcon(pix)

    def _icon_save(self) -> QIcon:
        if TablerIcons and OutlineIcon:
            try:
                img = TablerIcons.load(OutlineIcon.DEVICE_FLOPPY, size=24, color=TEXT_COLOR, stroke_width=2.0)
                return QIcon(img.toqpixmap())
            except Exception:
                pass
        pix = QPixmap(24, 24)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        pen = p.pen()
        pen.setColor(QColor(TEXT_COLOR))
        pen.setWidth(2)
        p.setPen(pen)
        # tray
        p.drawRoundedRect(5, 14, 14, 5, 2, 2)
        # down arrow
        p.drawLine(12, 6, 12, 13)
        p.drawLine(12, 13, 9, 10)
        p.drawLine(12, 13, 15, 10)
        p.end()
        return QIcon(pix)

    def _icon_delete(self) -> QIcon:
        if TablerIcons and OutlineIcon:
            try:
                img = TablerIcons.load(OutlineIcon.TRASH, size=24, color=TEXT_COLOR, stroke_width=2.0)
                return QIcon(img.toqpixmap())
            except Exception:
                pass
        pix = QPixmap(24, 24)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        pen = p.pen()
        pen.setColor(QColor(TEXT_COLOR))
        pen.setWidth(2)
        p.setPen(pen)
        p.drawRect(6, 9, 12, 12)
        p.drawLine(9, 12, 9, 18)
        p.drawLine(15, 12, 15, 18)
        p.drawLine(7, 9, 17, 9)
        p.drawLine(10, 6, 14, 6)
        p.drawLine(10, 6, 8, 9)
        p.drawLine(14, 6, 16, 9)
        p.end()
        return QIcon(pix)

    def _icon_copy(self) -> QIcon:
        if TablerIcons and OutlineIcon:
            try:
                img = TablerIcons.load(OutlineIcon.COPY, size=24, color=TEXT_COLOR, stroke_width=2.0)
                return QIcon(img.toqpixmap())
            except Exception:
                pass
        pix = QPixmap(24, 24)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        pen = p.pen()
        pen.setColor(QColor(TEXT_COLOR))
        pen.setWidth(2)
        p.setPen(pen)
        p.drawRect(6, 6, 10, 10)
        p.drawRect(10, 10, 10, 10)
        p.end()
        return QIcon(pix)

    def _icon_paste(self) -> QIcon:
        if TablerIcons and OutlineIcon:
            try:
                img = TablerIcons.load(OutlineIcon.CLIPBOARD_TEXT, size=24, color=TEXT_COLOR, stroke_width=2.0)
                return QIcon(img.toqpixmap())
            except Exception:
                pass
        pix = QPixmap(24, 24)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        pen = p.pen()
        pen.setColor(QColor(TEXT_COLOR))
        pen.setWidth(2)
        p.setPen(pen)
        p.drawRoundedRect(6, 8, 12, 12, 2, 2)
        p.drawLine(8, 12, 16, 12)
        p.drawLine(8, 15, 16, 15)
        p.drawLine(8, 18, 14, 18)
        p.end()
        return QIcon(pix)

    def _icon_undo(self) -> QIcon:
        if TablerIcons and OutlineIcon:
            try:
                img = TablerIcons.load(OutlineIcon.ARROW_BACK_UP, size=24, color=TEXT_COLOR, stroke_width=2.0)
                return QIcon(img.toqpixmap())
            except Exception:
                pass
        pix = QPixmap(24, 24)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        pen = p.pen()
        pen.setColor(QColor(TEXT_COLOR))
        pen.setWidthF(2.0)
        p.setPen(pen)
        # curved arrow pointing left
        p.drawArc(4, 12, 10, 8, 0 * 16, 180 * 16)
        p.drawLine(4, 16, 8, 12)
        p.drawLine(4, 16, 8, 20)
        p.end()
        return QIcon(pix)

    def _icon_redo(self) -> QIcon:
        if TablerIcons and OutlineIcon:
            try:
                img = TablerIcons.load(OutlineIcon.ARROW_FORWARD_UP, size=24, color=TEXT_COLOR, stroke_width=2.0)
                return QIcon(img.toqpixmap())
            except Exception:
                pass
        pix = QPixmap(24, 24)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        pen = p.pen()
        pen.setColor(QColor(TEXT_COLOR))
        pen.setWidthF(2.0)
        p.setPen(pen)
        # curved arrow pointing right
        p.drawArc(10, 12, 10, 8, 0 * 16, -180 * 16)
        p.drawLine(20, 16, 16, 12)
        p.drawLine(20, 16, 16, 20)
        p.end()
        return QIcon(pix)
