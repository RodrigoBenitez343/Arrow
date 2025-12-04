import logging
import os
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QToolButton,
    QFrame, QSizePolicy, QLabel, QApplication, QStyle, QScrollArea,
    QGraphicsDropShadowEffect
)
from PyQt5.QtCore import Qt, QPoint, QSize, QFileSystemWatcher, QTimer
from PyQt5.QtGui import QDrag, QPixmap, QPainter, QColor, QIcon
from PyQt5.QtCore import QMimeData
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
    BLOCK_COLOR, BLOCK_HOVER,
    LLM_COLOR, TTS_COLOR, CONDITIONAL_COLOR, CHAIN_IMPORT_COLOR, FORM_FILLER_COLOR
)


logger = logging.getLogger(__name__)
if not logger.handlers:
    fh = logging.FileHandler('graphui_debug.log')
    ch = logging.StreamHandler()
    fmt = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    fh.setFormatter(fmt)
    ch.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(ch)
    logger.setLevel(logging.DEBUG)


# Module-level helper for modern chevron icons with robust fallback
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
    # Fallback: draw minimal chevrons
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    pen = p.pen()
    pen.setColor(QColor(TEXT_COLOR))
    pen.setWidth(2)
    p.setPen(pen)
    s = size
    if name == 'CHEVRON_RIGHT':
        p.drawPolyline(QPoint(int(0.35*s), int(0.25*s)), QPoint(int(0.65*s), int(0.5*s)), QPoint(int(0.35*s), int(0.75*s)))
    elif name == 'CHEVRON_LEFT':
        p.drawPolyline(QPoint(int(0.65*s), int(0.25*s)), QPoint(int(0.35*s), int(0.5*s)), QPoint(int(0.65*s), int(0.75*s)))
    elif name == 'CHEVRON_UP':
        p.drawPolyline(QPoint(int(0.25*s), int(0.65*s)), QPoint(int(0.5*s), int(0.35*s)), QPoint(int(0.75*s), int(0.65*s)))
    else:  # CHEVRON_DOWN
        p.drawPolyline(QPoint(int(0.25*s), int(0.35*s)), QPoint(int(0.5*s), int(0.65*s)), QPoint(int(0.75*s), int(0.35*s)))
    p.end()
    return QIcon(pix)


class DraggableButton(QPushButton):
    """Colored node-type button supporting drag-to-add and click-to-add."""

    def __init__(self, label: str, node_type: str, bg_color: str, click_handler, parent=None):
        super().__init__(label, parent)
        self.node_type = node_type
        self._drag_start_pos = None
        self._click_handler = click_handler
        self.setCursor(Qt.OpenHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        # Colored background to reflect node type
        self.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {bg_color};
                color: {TEXT_COLOR};
                border: 1px solid {LIGHT_GREY};
                padding: 4px 10px;
                border-radius: 14px;
                min-width: 96px;
                min-height: 42px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                border-color: {ACCENT_COLOR};
            }}
            QPushButton:pressed {{
                background-color: {BLOCK_HOVER};
            }}
            """
        )

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_start_pos = event.pos()
        return super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_start_pos is None:
            return super().mouseMoveEvent(event)

        if (event.pos() - self._drag_start_pos).manhattanLength() >= 8:
            mime = QMimeData()
            mime.setData('application/x-looper-node-type', self.node_type.encode('utf-8'))

            # Create a pixmap for the drag icon
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
        # Treat click as add action
        if self._drag_start_pos is not None and (event.pos() - self._drag_start_pos).manhattanLength() < 8:
            try:
                if callable(self._click_handler):
                    self._click_handler()
            except Exception as e:
                logger.error(f"Toolbar click handler error: {e}")
        self._drag_start_pos = None
        return super().mouseReleaseEvent(event)


class DraggableSequenceButton(QPushButton):
    def __init__(self, label: str, file_path: str, graph_view, parent=None):
        super().__init__(label, parent)
        self.file_path = file_path
        self.graph_view = graph_view
        self._drag_start_pos = None
        self.setCursor(Qt.OpenHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {BLOCK_COLOR};
                color: {TEXT_COLOR};
                border: 1px solid {LIGHT_GREY};
                padding: 4px 10px;
                border-radius: 12px;
                min-width: 96px;
                min-height: 38px;
                font-weight: 600;
            }}
            QPushButton:hover {{ border-color: {ACCENT_COLOR}; }}
            QPushButton:pressed {{ background-color: {BLOCK_HOVER}; }}
            """
        )

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_start_pos = event.pos()
        return super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_start_pos is None:
            return super().mouseMoveEvent(event)
        if (event.pos() - self._drag_start_pos).manhattanLength() >= 8:
            mime = QMimeData()
            mime.setData('application/x-looper-node-type', b'sequence')
            mime.setText(self.file_path)
            mime.setData('application/x-looper-sequence-file', self.file_path.encode('utf-8'))
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
                    self.graph_view.node_operations.add_sequence_from_file(self.file_path)
            except Exception as e:
                logger.error(f"Toolbar sequence click handler error: {e}")
        self._drag_start_pos = None
        return super().mouseReleaseEvent(event)


class CollapsibleToolbar(QWidget):
    """Collapsible top toolbar that mirrors context menu actions."""

    def __init__(self, graph_view, parent=None):
        super().__init__(parent)
        self.graph_view = graph_view
        self._collapsed = False
        try:
            self._watcher = QFileSystemWatcher(self)
            folder = getattr(self.graph_view, 'sequences_folder', None)
            if folder:
                try:
                    os.makedirs(folder, exist_ok=True)
                except Exception:
                    pass
                try:
                    self._watcher.addPath(folder)
                except Exception:
                    pass
            try:
                self._watcher.directoryChanged.connect(self.refresh_sequences_list)
                self._watcher.fileChanged.connect(self.refresh_sequences_list)
            except Exception:
                pass
        except Exception:
            self._watcher = None
        self._build_ui()

    def _build_ui(self):
        # Start expanded
        self.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Expanding)
        self.setMinimumWidth(160)
        self.setMaximumWidth(160)
        main_layout = QVBoxLayout(self)
        # Minimal margins so we can shrink to near-zero
        main_layout.setContentsMargins(0, 0, 4, 0)
        main_layout.setSpacing(0)
        
        # Make the main widget transparent (content frame provides background)
        # This ensures no "black frame" when integrated in layout
        self.setAttribute(Qt.WA_TranslucentBackground)

        # Container for content
        self._content_frame = QFrame(self)
        self._content_frame.setObjectName("toolbarContent")
        self._content_frame.setStyleSheet(
            f"QFrame#toolbarContent {{ background-color: {DARK_GREY}; border: 1px solid {LIGHT_GREY}; border-radius: 6px; }}"
        )
        content_layout = QVBoxLayout(self._content_frame)
        content_layout.setContentsMargins(2, 2, 2, 2)
        content_layout.setSpacing(4)

        top_cluster = QFrame(self._content_frame)
        top_cluster.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._top_layout = QVBoxLayout(top_cluster)
        self._top_layout.setContentsMargins(0, 0, 0, 0)
        self._top_layout.setSpacing(1)
        self._sequences_scroll = self._make_sequences_scroll_box()
        self._top_layout.addWidget(self._sequences_scroll)

        nodes_col = QVBoxLayout()
        nodes_col.setSpacing(1)
        nodes_col.setContentsMargins(0, 0, 0, 0)
        nodes_col.addWidget(self._make_node_button("LLM", "llm", LLM_COLOR, lambda: self.graph_view.add_llm_node(), tooltip="Add LLM Node"))
        nodes_col.addWidget(self._make_node_button("Chain", "chain_import", CHAIN_IMPORT_COLOR, lambda: self.graph_view.add_chain_import_node(), tooltip="Add Chain Import Node"))
        nodes_col.addWidget(self._make_node_button("TTS", "tts", TTS_COLOR, lambda: self.graph_view.add_tts_node(), tooltip="Add TTS Node"))
        nodes_col.addWidget(self._make_node_button("Cond", "conditional", CONDITIONAL_COLOR, lambda: self.graph_view.add_conditional_node(), tooltip="Add Conditional Node"))
        self._top_layout.addLayout(nodes_col)
        content_layout.addWidget(top_cluster)

        content_layout.addSpacing(8)
        try:
            content_layout.setStretch(0, 1)
        except Exception:
            pass

        main_layout.addWidget(self._content_frame)

        # Floating toggle button (modern chevron), parented to graph_view to float outside
        self._toggle_btn_nodes = QToolButton(self.graph_view)
        self._toggle_btn_nodes.setToolTip("Toggle Nodes Toolbar")
        ico = load_tabler_icon('CHEVRON_RIGHT', 14)
        if ico:
            self._toggle_btn_nodes.setIcon(ico)
            self._toggle_btn_nodes.setIconSize(QSize(14, 14))
        else:
            self._toggle_btn_nodes.setText("❯")
        self._toggle_btn_nodes.setAutoRaise(True)
        self._toggle_btn_nodes.setCursor(Qt.PointingHandCursor)
        self._toggle_btn_nodes.setFixedSize(28, 28)
        self._toggle_btn_nodes.setStyleSheet(
            f"QToolButton {{ background-color: rgba(32,32,32,180); border: 1px solid {LIGHT_GREY}; border-radius: 14px; }}"
            f"QToolButton:hover {{ border-color: {ACCENT_COLOR}; }}"
        )
        # Shadow effect removed to prevent "floating window" artifacts during toggle
        self._toggle_btn_nodes.clicked.connect(self._toggle_nodes_toolbar)
        
        # Initial position
        QTimer.singleShot(0, self._position_nodes_toggle)

    def _toggle_nodes_toolbar(self):
        try:
            self._collapsed = not self._collapsed
            if self._collapsed:
                # Hide content and shrink toolbar to zero (visually)
                self._content_frame.setVisible(False)
                self.setMinimumWidth(0)
                self.setMaximumWidth(0)
                
                ico = load_tabler_icon('CHEVRON_LEFT', 14)
                if ico:
                    self._toggle_btn_nodes.setIcon(ico)
                else:
                    self._toggle_btn_nodes.setText("❮")
            else:
                # Show content and restore width
                self._content_frame.setVisible(True)
                self.setMinimumWidth(160)
                self.setMaximumWidth(160)
                
                ico = load_tabler_icon('CHEVRON_RIGHT', 14)
                if ico:
                    self._toggle_btn_nodes.setIcon(ico)
                else:
                    self._toggle_btn_nodes.setText("❯")
            
            # Re-position vertically and horizontally after layout update
            QTimer.singleShot(0, self._position_nodes_toggle)
            QTimer.singleShot(50, self._position_nodes_toggle)
        except Exception:
            pass

    def _position_nodes_toggle(self):
        # Position the button relative to the toolbar's position on the screen
        if hasattr(self, '_toggle_btn_nodes') and self._toggle_btn_nodes:
            try:
                # Map our (0,0) to the parent (graph_view)
                # If we are collapsed (width 0), mapTo parent might give the position where we would be?
                # Actually, mapTo(parent, (0,0)) gives the top-left corner in parent coords.
                
                # If collapsed (width 0), we want the button to be at the right edge of the graph view?
                # No, the toolbar is on the right. So (0,0) of the toolbar is the start of the toolbar.
                # If collapsed, width is 0, so it's at the far right.
                
                pos_in_parent = self.mapTo(self.graph_view, QPoint(0, 0))
                
                # We want the button to be to the LEFT of the toolbar.
                # Button width is 28.
                # Overlap slightly? 
                # Let's say we want it exactly adjacent to the left edge of the toolbar.
                x = pos_in_parent.x() - 28 + 2 # +2 overlap
                
                # Vertical center relative to toolbar
                y = pos_in_parent.y() + (self.height() // 2) - 14
                
                # Constrain y to be within graph view
                if y < 0: y = 0
                
                self._toggle_btn_nodes.move(x, y)
                self._toggle_btn_nodes.raise_()
            except Exception:
                pass
        try:
            new_scroll = self._make_sequences_scroll_box()
            if hasattr(self, '_sequences_scroll') and self._sequences_scroll is not None:
                old_scroll = self._sequences_scroll
                try:
                    # Hide it first to avoid ghost window
                    old_scroll.hide()
                    # Remove from layout
                    self._top_layout.removeWidget(old_scroll)
                    old_scroll.setParent(None)
                    old_scroll.deleteLater()
                except Exception:
                    pass
                
                # Insert new scroll at top (index 0)
                self._sequences_scroll = new_scroll
                self._top_layout.insertWidget(0, self._sequences_scroll)
            else:
                self._sequences_scroll = new_scroll
                self._top_layout.insertWidget(0, self._sequences_scroll)
            try:
                self._sequences_scroll.update()
                self._top_layout.update()
                self._content_frame.update()
                self.update()
                try:
                    QApplication.processEvents()
                except Exception:
                    pass
            except Exception:
                pass
        except Exception as e:
            logger.error(f"Failed to refresh sequences list: {e}")

    def _make_sequences_scroll_box(self):
        scroll = QScrollArea(self)
        scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        try:
            scroll.setWidgetResizable(True)
        except Exception:
            pass
        try:
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        except Exception:
            pass
        

        container = QFrame(scroll)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(6)
        folder = getattr(self.graph_view, 'sequences_folder', None)
        files = []
        try:
            if folder and os.path.isdir(folder):
                files = sorted([f for f in os.listdir(folder) if f.lower().endswith('.json')])
                nested = os.path.join(folder, 'sequences')
                if os.path.isdir(nested):
                    extra = [f for f in os.listdir(nested) if f.lower().endswith('.json')]
                    files = sorted(set(files + extra))
        except Exception:
            files = []
        if not files:
            placeholder = QLabel("No sequences")
            placeholder.setAlignment(Qt.AlignCenter)
            placeholder.setStyleSheet(
                f"""
                QLabel {{
                    background-color: {MEDIUM_GREY};
                    color: {TEXT_COLOR};
                    border: 1px solid {LIGHT_GREY};
                    border-radius: 10px;
                    min-height: 40px;
                }}
                """
            )
            layout.addWidget(placeholder)
            scroll.setWidget(container)
            return scroll
        for name in files:
            p1 = os.path.join(folder, name)
            p2 = os.path.join(folder, 'sequences', name)
            full_path = p1 if os.path.exists(p1) else p2
            btn = DraggableSequenceButton(name, full_path, self.graph_view, self)
            layout.addWidget(btn)
        scroll.setWidget(container)
        return scroll

    def _make_node_button(self, label, node_type, color, click_handler, tooltip=None):
        # Use icon-only modern button when tabler is available
        if TablerIcons and OutlineIcon:
            icon_map = {
                'llm': OutlineIcon.BRAIN,
                'chain_import': OutlineIcon.LINK,
                'tts': OutlineIcon.MICROPHONE,
                'conditional': OutlineIcon.CIRCUIT_SWITCH_CLOSED,
            }
            icon_key = icon_map.get(node_type)
            if icon_key:
                btn = DraggableButton("", node_type, color, click_handler, self)
                try:
                    img = TablerIcons.load(icon_key, size=20, color=TEXT_COLOR, stroke_width=2.0)
                    btn.setIcon(QIcon(img.toqpixmap()))
                    btn.setIconSize(QSize(20, 20))
                except Exception:
                    pass
            else:
                btn = DraggableButton(label, node_type, color, click_handler, self)
        else:
            btn = DraggableButton(label, node_type, color, click_handler, self)
        if tooltip:
            btn.setToolTip(tooltip)
        return btn

    def resizeEvent(self, event):
        super().resizeEvent(event)
        try:
            QTimer.singleShot(0, self._position_nodes_toggle)
        except Exception:
            pass
