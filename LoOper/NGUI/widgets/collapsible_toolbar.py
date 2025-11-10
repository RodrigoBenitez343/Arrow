import logging
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QToolButton,
    QFrame, QSizePolicy, QLabel, QApplication, QStyle
)
from PyQt5.QtCore import Qt, QPoint, QSize
from PyQt5.QtGui import QDrag, QPixmap, QPainter, QColor
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
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
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


class CollapsibleToolbar(QWidget):
    """Collapsible top toolbar that mirrors context menu actions."""

    def __init__(self, graph_view, parent=None):
        super().__init__(parent)
        self.graph_view = graph_view
        self._collapsed = False
        self._build_ui()

    def _build_ui(self):
        # Right-side vertical panel: resizable via splitter, expands vertically
        self.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Expanding)
        # Allow manual resizing with reasonable limits (allow full collapse to 0)
        self.setMinimumWidth(0)
        self.setMaximumWidth(120)
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

        # Section 1: Actions (vertical)
        actions_col = QVBoxLayout()
        actions_col.setSpacing(1)
        actions_col.setContentsMargins(0, 0, 0, 0)
        actions_col.addWidget(self._make_plain_button("Record", lambda: self.graph_view.record_new_sequence(), tooltip="Record Sequence"))
        actions_col.addWidget(self._make_plain_button("Add Seq", lambda: self.graph_view.add_sequence(), tooltip="Add Previously Recorded Sequence"))
        content_layout.addLayout(actions_col)

        # Section 2: Node types (vertical, draggable)
        nodes_col = QVBoxLayout()
        nodes_col.setSpacing(1)
        nodes_col.setContentsMargins(0, 0, 0, 0)
        nodes_col.addWidget(self._make_node_button("LLM", "llm", LLM_COLOR, lambda: self.graph_view.add_llm_node(), tooltip="Add LLM Node"))
        nodes_col.addWidget(self._make_node_button("Chain", "chain_import", CHAIN_IMPORT_COLOR, lambda: self.graph_view.add_chain_import_node(), tooltip="Add Chain Import Node"))
        nodes_col.addWidget(self._make_node_button("TTS", "tts", TTS_COLOR, lambda: self.graph_view.add_tts_node(), tooltip="Add TTS Node"))
        nodes_col.addWidget(self._make_node_button("Cond", "conditional", CONDITIONAL_COLOR, lambda: self.graph_view.add_conditional_node(), tooltip="Add Conditional Node"))
        content_layout.addLayout(nodes_col)

        # Section 3: Edit ops (vertical)
        edit_col = QVBoxLayout()
        edit_col.setSpacing(1)
        edit_col.setContentsMargins(0, 0, 0, 0)
        edit_col.addWidget(self._make_plain_button("Copy", lambda: self.graph_view.copy_selected_nodes(), tooltip="Copy Selected"))
        edit_col.addWidget(self._make_plain_button("Delete", lambda: self.graph_view.delete_selected_nodes(), tooltip="Delete Selected"))
        content_layout.addLayout(edit_col)

        # Section 4: Chain ops (vertical skeuomorphic)
        chain_col = QVBoxLayout()
        chain_col.setSpacing(1)
        chain_col.setContentsMargins(0, 0, 0, 0)
        # Icon-only buttons for Run/Load/Save using standard Qt icons
        chain_col.addWidget(self._make_icon_button(QStyle.SP_MediaPlay, self._run_chain, tooltip="Run Chain"))
        chain_col.addWidget(self._make_icon_button(QStyle.SP_DialogOpenButton, self._open_chain, tooltip="Load Chain"))
        chain_col.addWidget(self._make_icon_button(QStyle.SP_DialogSaveButton, self._save_chain, tooltip="Save Chain"))
        content_layout.addLayout(chain_col)

        main_layout.addWidget(self._content_frame)

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
        btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
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
        btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
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