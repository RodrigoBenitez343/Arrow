import logging
import os
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QToolButton,
    QFrame, QSizePolicy, QLabel, QApplication, QStyle, QScrollArea
)
from PyQt5.QtCore import Qt, QPoint, QSize, QFileSystemWatcher
from PyQt5.QtGui import QDrag, QPixmap, QPainter, QColor, QIcon
from PyQt5.QtCore import QMimeData

from ..constants import (
    DARK_GREY, LIGHT_GREY, MEDIUM_GREY, TEXT_COLOR,
    LLM_COLOR, TTS_COLOR, CONDITIONAL_COLOR, CHAIN_IMPORT_COLOR
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
                padding: 4px 8px;
                border-radius: 10px;
                min-width: 90px;
                min-height: 44px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {bg_color};
            }}
            QPushButton:pressed {{
                background-color: {bg_color};
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
                background-color: {MEDIUM_GREY};
                color: {TEXT_COLOR};
                border: 1px solid {LIGHT_GREY};
                padding: 4px 8px;
                border-radius: 10px;
                min-width: 90px;
                min-height: 40px;
                font-weight: 600;
            }}
            QPushButton:hover {{ background-color: {LIGHT_GREY}; }}
            QPushButton:pressed {{ background-color: {DARK_GREY}; }}
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
        # Right-side vertical panel: resizable via splitter, expands vertically
        self.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Expanding)
        # Allow manual resizing with reasonable limits (allow full collapse to 0)
        self.setMinimumWidth(0)
        self.setMaximumWidth(160)
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(2)

        # No header; start content at the very top

        # Content frame
        self._content_frame = QFrame(self)
        self._content_frame.setObjectName("toolbarContent")
        self._content_frame.setStyleSheet("")
        content_layout = QVBoxLayout(self._content_frame)
        content_layout.setContentsMargins(2, 2, 2, 2)
        content_layout.setSpacing(1)

        # Top cluster: sequences list and node types
        top_cluster = QFrame(self)
        top_cluster.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._top_layout = QVBoxLayout(top_cluster)
        self._top_layout.setContentsMargins(0, 0, 0, 0)
        self._top_layout.setSpacing(1)
        self._sequences_scroll = self._make_sequences_scroll_box()
        self._top_layout.addWidget(self._sequences_scroll)

        # Section 2: Node types (vertical, draggable)
        nodes_col = QVBoxLayout()
        nodes_col.setSpacing(1)
        nodes_col.setContentsMargins(0, 0, 0, 0)
        nodes_col.addWidget(self._make_node_button("LLM", "llm", LLM_COLOR, lambda: self.graph_view.add_llm_node(), tooltip="Add LLM Node"))
        nodes_col.addWidget(self._make_node_button("Chain", "chain_import", CHAIN_IMPORT_COLOR, lambda: self.graph_view.add_chain_import_node(), tooltip="Add Chain Import Node"))
        nodes_col.addWidget(self._make_node_button("TTS", "tts", TTS_COLOR, lambda: self.graph_view.add_tts_node(), tooltip="Add TTS Node"))
        nodes_col.addWidget(self._make_node_button("Cond", "conditional", CONDITIONAL_COLOR, lambda: self.graph_view.add_conditional_node(), tooltip="Add Conditional Node"))
        self._top_layout.addLayout(nodes_col)
        content_layout.addWidget(top_cluster)

        

        # Spacing between clusters
        content_layout.addSpacing(8)
        # Navigation/System cluster: Record, Load, Save, Playback
        nav_cluster = QFrame(self)
        nav_cluster.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        chain_col = QVBoxLayout(nav_cluster)
        chain_col.setSpacing(1)
        chain_col.setContentsMargins(0, 0, 0, 0)
        chain_col.addWidget(self._make_record_icon_button())
        chain_col.addWidget(self._make_icon_button(QStyle.SP_DialogOpenButton, self._open_chain, tooltip="Load Chain"))
        chain_col.addWidget(self._make_icon_button(QStyle.SP_DialogSaveButton, self._save_chain, tooltip="Save Chain"))
        chain_col.addWidget(self._make_icon_button(QStyle.SP_MediaPlay, self._run_chain, tooltip="Run Chain"))
        content_layout.addWidget(nav_cluster)
        try:
            content_layout.setStretch(0, 1)
            content_layout.setStretch(2, 0)
        except Exception:
            pass

        main_layout.addWidget(self._content_frame)

    def refresh_sequences_list(self):
        try:
            new_scroll = self._make_sequences_scroll_box()
            if hasattr(self, '_sequences_scroll') and self._sequences_scroll is not None:
                try:
                    idx = self._top_layout.indexOf(self._sequences_scroll)
                except Exception:
                    idx = 0
                try:
                    self._sequences_scroll.setParent(None)
                except Exception:
                    pass
                try:
                    self._sequences_scroll.deleteLater()
                except Exception:
                    pass
                self._sequences_scroll = new_scroll
                if idx < 0:
                    self._top_layout.insertWidget(0, self._sequences_scroll)
                else:
                    self._top_layout.insertWidget(idx, self._sequences_scroll)
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
            full_path = os.path.join(folder, name)
            btn = DraggableSequenceButton(name, full_path, self.graph_view, self)
            layout.addWidget(btn)
        scroll.setWidget(container)
        return scroll

    def _make_node_button(self, label, node_type, color, click_handler, tooltip=None):
        btn = DraggableButton(label, node_type, color, click_handler, self)
        if tooltip:
            btn.setToolTip(tooltip)
        return btn

    def _make_plain_button(self, label, handler, tooltip=None):
        btn = QPushButton(label, self)
        btn.clicked.connect(handler)
        if tooltip:
            btn.setToolTip(tooltip)
        btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        btn.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {MEDIUM_GREY};
                color: {TEXT_COLOR};
                border: 1px solid {LIGHT_GREY};
                border-radius: 10px;
                padding: 4px 8px;
                min-width: 90px;
                min-height: 52px;
                font-weight: 600;
            }}
            QPushButton:hover {{ background-color: {LIGHT_GREY}; }}
            QPushButton:pressed {{ background-color: {DARK_GREY}; }}
            """
        )
        return btn

    def _make_icon_button(self, std_icon, handler, tooltip=None):
        btn = QPushButton("", self)
        btn.clicked.connect(handler)
        if tooltip:
            btn.setToolTip(tooltip)
        btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        # Use standard Qt icons to avoid external assets
        style = QApplication.style()
        btn.setIcon(style.standardIcon(std_icon))
        btn.setIconSize(QSize(28, 28))
        # Simple flat style, taller height
        btn.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {MEDIUM_GREY};
                color: {TEXT_COLOR};
                border: 1px solid {LIGHT_GREY};
                border-radius: 10px;
                padding: 6px;
                min-width: 90px;
                min-height: 52px;
            }}
            QPushButton:hover {{ background-color: {LIGHT_GREY}; }}
            QPushButton:pressed {{ background-color: {DARK_GREY}; }}
            """
        )
        return btn

    def _make_record_icon_button(self):
        btn = QPushButton("", self)
        btn.clicked.connect(lambda: self.graph_view.record_new_sequence())
        btn.setToolTip("Record Sequence")
        btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        pix = QPixmap(28, 28)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(220, 0, 0))
        p.setPen(Qt.NoPen)
        p.drawEllipse(4, 4, 20, 20)
        p.end()
        btn.setIcon(QIcon(pix))
        btn.setIconSize(QSize(28, 28))
        btn.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {MEDIUM_GREY};
                color: {TEXT_COLOR};
                border: 1px solid {LIGHT_GREY};
                border-radius: 10px;
                padding: 6px;
                min-width: 90px;
                min-height: 52px;
            }}
            QPushButton:hover {{ background-color: {LIGHT_GREY}; }}
            QPushButton:pressed {{ background-color: {DARK_GREY}; }}
            """
        )
        return btn

    # Removed: add action helper per request (not needed)

    # Chain operations wiring via MainWindow where appropriate
    def _run_chain(self):
        try:
            window = self.graph_view.window()
            if hasattr(window, 'run_chain'):
                window.run_chain()
            else:
                logger.warning('MainWindow.run_chain not found')
        except Exception as e:
            logger.error(f"Run chain error: {e}")

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
