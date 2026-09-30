import logging
import os
import json
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QToolButton,
    QFrame, QSizePolicy, QLabel, QApplication, QScrollArea,
    QGraphicsDropShadowEffect, QStyleOptionButton, QStyle,
    QMenu, QAction, QInputDialog, QLineEdit, QComboBox, QMessageBox
)
from PyQt5.QtCore import Qt, QPoint, QSize, QFileSystemWatcher, QTimer, QMimeData, QPropertyAnimation, QEasingCurve, QAbstractAnimation
from PyQt5.QtGui import QDrag, QPixmap, QPainter, QColor, QIcon

from ..constants import (
    DARK_GREY, LIGHT_GREY, MEDIUM_GREY, TEXT_COLOR, ACCENT_COLOR,
    BLOCK_COLOR, BLOCK_HOVER, CHAIN_IMPORT_COLOR, CONTEXT_NODE_COLOR,
    CODE_NODE_COLOR, LLM_COLOR, WEB_SEQUENCE_COLOR, SIDE_PANEL_BG, GRAPH_PLANE
)
from .collapsible_toolbar import get_node_icon, load_tabler_icon, make_toggle_triangle_icon, _painter_icon_folder, TrashBinWidget
from ..i18n import _

logger = logging.getLogger(__name__)

class DraggableChainButton(QFrame):
    """Green card-style widget representing a chain, supporting drag-to-add and load-to-graph."""
    
    def __init__(self, label: str, file_path: str, graph_view, parent=None):
        # Remove .json from label for display
        self._full_text = os.path.splitext(label)[0]
        super().__init__(parent)
        self.file_path = file_path
        self.graph_view = graph_view
        self._drag_start_pos = None
        self.setCursor(Qt.OpenHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(48)

        # Load description for tooltip
        self._description = ""
        try:
            if os.path.exists(self.file_path):
                with open(self.file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self._description = data.get('description', '')
        except Exception:
            pass

        # Chain icon
        chain_icon = get_node_icon('chain_import', 20)

        # Layout: [chain_icon] [label_text] [folder_btn] [load_button]
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 6, 6)
        layout.setSpacing(6)

        # Left icon
        if chain_icon and not chain_icon.isNull():
            icon_label = QLabel(self)
            icon_label.setPixmap(chain_icon.pixmap(QSize(20, 20)))
            icon_label.setFixedSize(20, 20)
            icon_label.setStyleSheet("background: transparent; border: none;")
            layout.addWidget(icon_label)

        # Text label (takes remaining space)
        self._label = QLabel(self._full_text, self)
        self._label.setStyleSheet(
            f"color: {TEXT_COLOR}; font-weight: 600; background: transparent; border: none;"
        )
        layout.addWidget(self._label, 1)

        # Folder button to move chain between collections
        self._folder_btn = QToolButton(self)
        self._folder_btn.setFixedSize(22, 22)
        fld_icon = _painter_icon_folder(14)
        if fld_icon and not fld_icon.isNull():
            self._folder_btn.setIcon(fld_icon)
            self._folder_btn.setIconSize(QSize(14, 14))
        self._folder_btn.setToolTip(_("Move to collection"))
        self._folder_btn.setCursor(Qt.PointingHandCursor)
        self._folder_btn.setStyleSheet(
            f"""
            QToolButton {{
                background-color: transparent;
                border: none;
                border-radius: 11px;
                padding: 2px;
            }}
            QToolButton:hover {{
                background-color: #1FFFFFFF;
            }}
            """
        )
        self._folder_btn.clicked.connect(self._show_collection_menu)
        layout.addWidget(self._folder_btn)

        # Load-to-graph button
        self._load_btn = QToolButton(self)
        self._load_btn.setFixedSize(28, 28)
        btn_icon = load_tabler_icon('ARROW_RIGHT', 16)
        if btn_icon and not btn_icon.isNull():
            self._load_btn.setIcon(btn_icon)
            self._load_btn.setIconSize(QSize(16, 16))
        self._load_btn.setToolTip(_("Load chain into graph"))
        self._load_btn.setCursor(Qt.PointingHandCursor)
        self._load_btn.setStyleSheet(
            f"""
            QToolButton {{
                background-color: #0FFFFFFF;
                border: none;
                border-radius: 14px;
                padding: 2px;
            }}
            QToolButton:hover {{
                background-color: #2EFFFFFF;
            }}
            QToolButton:pressed {{
                background-color: #40FFFFFF;
            }}
            """
        )
        self._load_btn.clicked.connect(self._load_chain_to_graph)
        layout.addWidget(self._load_btn)

        # Tooltip
        if self._description:
            self.setToolTip(f"{self._full_text}\n{self._description}")
        else:
            self.setToolTip(self._full_text)

        # Card styling
        self.setStyleSheet(
            f"""
            DraggableChainButton {{
                background-color: {CHAIN_IMPORT_COLOR};
                color: {TEXT_COLOR};
                border: 1px solid {LIGHT_GREY};
                border-radius: 10px;
                font-weight: 600;
            }}
            DraggableChainButton:hover {{ 
                border-color: {ACCENT_COLOR}; 
                background-color: {CHAIN_IMPORT_COLOR};
            }}
            """
        )

    def _load_chain_to_graph(self):
        """Load this chain file into the graph view."""
        try:
            if hasattr(self.graph_view, 'load_chain'):
                self.graph_view.load_chain(self.file_path)
        except Exception as e:
            logger.error(f"Failed to load chain to graph: {e}")

    def text(self):
        """Return the chain display name (for search/filter compatibility)."""
        return self._full_text

    def update_display_text(self):
        """Elide label text to fit available width."""
        available_width = self.width() - 110  # margins + icon + folder btn + load button + spacing
        fm = self.fontMetrics()
        elided = fm.elidedText(self._full_text, Qt.ElideRight, max(0, available_width))
        self._label.setText(elided)

    def contextMenuEvent(self, event):
        menu = QMenu(self)

        # Add Description action
        edit_desc_action = QAction(_("Edit Description"), self)
        edit_desc_action.triggered.connect(self._edit_description)
        menu.addAction(edit_desc_action)

        menu.addSeparator()
        coll_menu = menu.addMenu(_painter_icon_folder(14), _("Move to Collection"))
        # Get collections from parent ChainsLibrary
        parent_lib = self.parent()
        while parent_lib and not isinstance(parent_lib, ChainsLibrary):
            parent_lib = parent_lib.parent()
        collections = parent_lib._get_collection_names() if parent_lib else []
        for coll in [''] + collections:
            label = _("(None)") if coll == '' else coll
            act = QAction(label, self)
            if coll == '':
                act.setIcon(_painter_icon_folder(14))
            act.triggered.connect(lambda checked, c=coll: self._set_collection(c))
            coll_menu.addAction(act)

        menu.exec_(event.globalPos())

    def _edit_description(self):
        # Load current description
        current_desc = ""
        try:
            with open(self.file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                current_desc = data.get('description', '')
        except Exception as e:
            logger.error(f"Failed to load chain for description: {e}")

        text, ok = QInputDialog.getText(
            self, 
            _("Chain Description"), 
            _("Enter description for this chain:"),
            QLineEdit.Normal, 
            current_desc
        )

        if ok:
            try:
                if os.path.exists(self.file_path):
                     with open(self.file_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                else:
                    data = {}

                data['description'] = text
                self._description = text

                with open(self.file_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2)

                # Update tooltip
                if text:
                    self.setToolTip(f"{self._full_text}\n{text}")
                else:
                    self.setToolTip(self._full_text)

            except Exception as e:
                logger.error(f"Failed to save chain description: {e}")

    def _show_collection_menu(self):
        menu = QMenu(self)
        parent_lib = self.parent()
        while parent_lib and not isinstance(parent_lib, ChainsLibrary):
            parent_lib = parent_lib.parent()
        collections = parent_lib._get_collection_names() if parent_lib else []
        for coll in [''] + collections:
            label = _("(None)") if coll == '' else coll
            act = QAction(label, self)
            if coll == '':
                act.setIcon(_painter_icon_folder(14))
            act.triggered.connect(lambda checked, c=coll: self._set_collection(c))
            menu.addAction(act)
        btn = self._folder_btn
        menu.exec_(btn.mapToGlobal(btn.rect().bottomLeft()))

    def _set_collection(self, collection_name):
        try:
            if os.path.exists(self.file_path):
                with open(self.file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            else:
                data = {}
            data['collection'] = collection_name
            with open(self.file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            parent_lib = self.parent()
            while parent_lib and not isinstance(parent_lib, ChainsLibrary):
                parent_lib = parent_lib.parent()
            if parent_lib:
                parent_lib.refresh_chains_list()
        except Exception as e:
            logger.error(f"Failed to set chain collection: {e}")

    def _sync_folder_btn_visibility(self):
        try:
            parent_lib = self.parent()
            while parent_lib and not isinstance(parent_lib, ChainsLibrary):
                parent_lib = parent_lib.parent()
            has_colls = bool(parent_lib and parent_lib._get_collection_names())
            self._folder_btn.setVisible(has_colls)
        except Exception:
            pass

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_start_pos = event.pos()
        return super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_start_pos is None:
            return super().mouseMoveEvent(event)

        if (event.pos() - self._drag_start_pos).manhattanLength() >= 8:
            mime = QMimeData()
            # Mime type for chain import node
            mime.setData('application/x-looper-node-type', b'chain_import')
            mime.setText(self.file_path)
            mime.setData('application/x-looper-chain-file', self.file_path.encode('utf-8'))

            pixmap = QPixmap(self.size())
            pixmap.fill(Qt.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setBrush(QColor(CHAIN_IMPORT_COLOR))
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
        # Click behavior: Add chain import node to graph
        if self._drag_start_pos is not None and (event.pos() - self._drag_start_pos).manhattanLength() < 8:
            try:
                if hasattr(self.graph_view, 'node_operations'):
                    self.graph_view.node_operations.add_chain_import_from_file(self.file_path)
            except Exception as e:
                logger.error(f"ChainsLibrary click handler error: {e}")
        self._drag_start_pos = None
        return super().mouseReleaseEvent(event)


class DraggableContextNodeButton(QPushButton):
    """Blue card-style button representing a context node from another chain, supporting drag-to-add."""
    
    def __init__(self, label: str, chain_file: str, context_node_data: dict, graph_view, parent=None):
        super().__init__(label, parent)
        self.chain_file = chain_file
        self.context_node_data = context_node_data
        self.graph_view = graph_view
        self._library = parent
        self._drag_start_pos = None
        self.setCursor(Qt.OpenHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        
        # Build tooltip with context node info
        node_id = context_node_data.get('node_id', 'unknown')
        context_label = context_node_data.get('label', '') or context_node_data.get('context_node_label', '')
        persistent = context_node_data.get('persistent', True)
        scope = context_node_data.get('scope', 'local')

        tooltip = f"Context Node: {label}\n"
        tooltip += f"Chain: {os.path.basename(chain_file)}\n"
        tooltip += f"Scope: {scope}\n"
        tooltip += f"Persistent: {persistent}\n"
        if context_label:
            tooltip += f"Label: {context_label}\n"
        tooltip += f"ID: {node_id}"
        
        self.setToolTip(tooltip)
        
        # Set icon (context node icon)
        icon = get_node_icon('context', 24)
        if icon:
            self.setIcon(icon)
            self.setIconSize(QSize(24, 24))
            
        self.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {CONTEXT_NODE_COLOR};
                color: {TEXT_COLOR};
                border: 1px solid {LIGHT_GREY};
                padding: 8px 12px;
                border-radius: 10px;
                min-height: 44px;
                font-weight: 600;
                text-align: left;
            }}
            QPushButton:hover {{ 
                border-color: {ACCENT_COLOR}; 
                background-color: {CONTEXT_NODE_COLOR};
            }}
            QPushButton:pressed {{ 
                background-color: {BLOCK_HOVER}; 
            }}
            """
        )

    def update_display_text(self):
        option = QStyleOptionButton()
        self.initStyleOption(option)
        contents_rect = self.style().subElementRect(QStyle.SE_PushButtonContents, option, self)
        text_rect = contents_rect
        if not option.icon.isNull():
            text_rect.setLeft(text_rect.left() + option.iconSize.width() + 8)
        fm = self.fontMetrics()
        self.setText(fm.elidedText(self.text(), Qt.ElideRight, max(0, text_rect.width())))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_start_pos = event.pos()
        return super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_start_pos is None:
            return super().mouseMoveEvent(event)
        
        if (event.pos() - self._drag_start_pos).manhattanLength() >= 8:
            mime = QMimeData()
            # Mime type for context node
            mime.setData('application/x-looper-node-type', b'context')
            # Pass the context node configuration as JSON
            node_config = json.dumps({
                'chain_file': self.chain_file,
                'context_node_data': self.context_node_data
            })
            mime.setData('application/x-looper-context-config', node_config.encode('utf-8'))
            
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
        # Click behavior: Add context node to graph with shared chain file reference
        if self._drag_start_pos is not None and (event.pos() - self._drag_start_pos).manhattanLength() < 8:
            try:
                if hasattr(self.graph_view, 'node_operations'):
                    self.graph_view.node_operations.add_context_node_with_shared_file(
                        self.chain_file,
                        self.context_node_data
                    )
            except Exception as e:
                logger.error(f"ContextNodesLibrary click handler error: {e}")
        self._drag_start_pos = None
        return super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event):
        menu = QMenu(self)

        delete_action = QAction(_("Delete"), self)
        delete_action.triggered.connect(self._delete_from_library)
        menu.addAction(delete_action)

        menu.exec_(event.globalPos())

    def _delete_from_library(self):
        lib = getattr(self, '_library', None)
        if lib is not None and hasattr(lib, 'delete_shared_node'):
            lib.delete_shared_node('context', self.chain_file, self.context_node_data)

class DraggableCodeNodeButton(QPushButton):
    """Gold card-style button representing a code node from another chain, supporting drag-to-add."""

    def __init__(self, label: str, chain_file: str, code_node_data: dict, graph_view, parent=None):
        super().__init__(label, parent)
        self.chain_file = chain_file
        self.code_node_data = code_node_data
        self.graph_view = graph_view
        self._library = parent
        self._drag_start_pos = None
        self.setCursor(Qt.OpenHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        # Build tooltip with code node info
        description = code_node_data.get('description', '')
        file_path = code_node_data.get('file_path', '')
        node_id = code_node_data.get('node_id', 'unknown')

        tooltip = f"Code Node: {label}\n"
        tooltip += f"Chain: {os.path.basename(chain_file)}\n"
        if description:
            tooltip += f"Description: {description}\n"
        if file_path:
            tooltip += f"File: {file_path}\n"
        tooltip += f"ID: {node_id}"

        self.setToolTip(tooltip)

        # Set icon (code node icon)
        icon = get_node_icon('code', 24)
        if icon:
            self.setIcon(icon)
            self.setIconSize(QSize(24, 24))

        self.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {CODE_NODE_COLOR};
                color: {TEXT_COLOR};
                border: 1px solid {LIGHT_GREY};
                padding: 8px 12px;
                border-radius: 10px;
                min-height: 44px;
                font-weight: 600;
                text-align: left;
            }}
            QPushButton:hover {{
                border-color: {ACCENT_COLOR};
                background-color: {CODE_NODE_COLOR};
            }}
            QPushButton:pressed {{
                background-color: {BLOCK_HOVER};
            }}
            """
        )

    def update_display_text(self):
        option = QStyleOptionButton()
        self.initStyleOption(option)
        contents_rect = self.style().subElementRect(QStyle.SE_PushButtonContents, option, self)
        text_rect = contents_rect
        if not option.icon.isNull():
            text_rect.setLeft(text_rect.left() + option.iconSize.width() + 8)
        fm = self.fontMetrics()
        self.setText(fm.elidedText(self.text(), Qt.ElideRight, max(0, text_rect.width())))

    def contextMenuEvent(self, event):
        menu = QMenu(self)

        delete_action = QAction(_("Delete"), self)
        delete_action.triggered.connect(self._delete_from_library)
        menu.addAction(delete_action)

        menu.addSeparator()

        edit_desc_action = QAction(_("Edit Description"), self)
        edit_desc_action.triggered.connect(self._edit_description)
        menu.addAction(edit_desc_action)

        menu.exec_(event.globalPos())

    def _delete_from_library(self):
        lib = getattr(self, '_library', None)
        if lib is not None and hasattr(lib, 'delete_shared_node'):
            lib.delete_shared_node('code', self.chain_file, self.code_node_data)

    def _edit_description(self):
        current_desc = self.code_node_data.get('description', '')
        text, ok = QInputDialog.getText(
            self,
            _("Code Node Description"),
            _("Enter description for this code node:"),
            QLineEdit.Normal,
            current_desc
        )

        if ok:
            try:
                # Update the code node data in memory
                self.code_node_data['description'] = text

                # Update JSON in chain file
                if os.path.exists(self.chain_file):
                    with open(self.chain_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)

                    # Find and update the matching code node
                    code_nodes = data.get('code_nodes', [])
                    node_id = self.code_node_data.get('node_id', '')
                    for cn in code_nodes:
                        if cn.get('node_id') == node_id:
                            cn['description'] = text
                            break

                    with open(self.chain_file, 'w', encoding='utf-8') as f:
                        json.dump(data, f, indent=2)

                # Update tooltip
                file_path = self.code_node_data.get('file_path', '')
                tooltip = f"Code Node: {self.text()}\n"
                tooltip += f"Chain: {os.path.basename(self.chain_file)}\n"
                if text:
                    tooltip += f"Description: {text}\n"
                if file_path:
                    tooltip += f"File: {file_path}\n"
                tooltip += f"ID: {self.code_node_data.get('node_id', 'unknown')}"
                self.setToolTip(tooltip)

            except Exception as e:
                logger.error(f"Failed to save code node description: {e}")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_start_pos = event.pos()
        return super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_start_pos is None:
            return super().mouseMoveEvent(event)

        if (event.pos() - self._drag_start_pos).manhattanLength() >= 8:
            mime = QMimeData()
            mime.setData('application/x-looper-node-type', b'code')
            # Pass the code node configuration as JSON
            node_config = json.dumps({
                'chain_file': self.chain_file,
                'code_node_data': self.code_node_data
            })
            mime.setData('application/x-looper-code-config', node_config.encode('utf-8'))

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
        # Click behavior: Add code node to graph with shared chain file reference
        if self._drag_start_pos is not None and (event.pos() - self._drag_start_pos).manhattanLength() < 8:
            try:
                if hasattr(self.graph_view, 'node_operations'):
                    self.graph_view.node_operations.add_code_node_with_shared_file(
                        self.chain_file,
                        self.code_node_data
                    )
            except Exception as e:
                logger.error(f"CodeNodesLibrary click handler error: {e}")
        self._drag_start_pos = None
        return super().mouseReleaseEvent(event)


class DraggableLLMNodeButton(QPushButton):
    """Orange card-style button representing an LLM node from another chain, supporting drag-to-add."""

    def __init__(self, label: str, chain_file: str, llm_node_data: dict, graph_view, parent=None):
        super().__init__(label, parent)
        self.chain_file = chain_file
        self.llm_node_data = llm_node_data
        self.graph_view = graph_view
        self._library = parent
        self._drag_start_pos = None
        self.setCursor(Qt.OpenHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        # Build tooltip with LLM node info
        semantic_desc = llm_node_data.get('semantic_description', '')
        model = llm_node_data.get('model', 'llama3.2:latest')
        prompt_preview = llm_node_data.get('prompt', '').replace('\n', ' ').strip()[:60]
        node_id = llm_node_data.get('node_id', 'unknown')

        tooltip = f"LLM Node: {label}\n"
        tooltip += f"Chain: {os.path.basename(chain_file)}\n"
        tooltip += f"Model: {model}\n"
        if semantic_desc:
            tooltip += f"Description: {semantic_desc}\n"
        if prompt_preview:
            tooltip += f"Prompt: {prompt_preview}\n"
        tooltip += f"ID: {node_id}"

        self.setToolTip(tooltip)

        # Set icon (LLM node icon)
        icon = get_node_icon('llm', 24)
        if icon:
            self.setIcon(icon)
            self.setIconSize(QSize(24, 24))

        self.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {LLM_COLOR};
                color: {TEXT_COLOR};
                border: 1px solid {LIGHT_GREY};
                padding: 8px 12px;
                border-radius: 10px;
                min-height: 44px;
                font-weight: 600;
                text-align: left;
            }}
            QPushButton:hover {{
                border-color: {ACCENT_COLOR};
                background-color: {LLM_COLOR};
            }}
            QPushButton:pressed {{
                background-color: {BLOCK_HOVER};
            }}
            """
        )

    def update_display_text(self):
        option = QStyleOptionButton()
        self.initStyleOption(option)
        contents_rect = self.style().subElementRect(QStyle.SE_PushButtonContents, option, self)
        text_rect = contents_rect
        if not option.icon.isNull():
            text_rect.setLeft(text_rect.left() + option.iconSize.width() + 8)
        fm = self.fontMetrics()
        self.setText(fm.elidedText(self.text(), Qt.ElideRight, max(0, text_rect.width())))

    def contextMenuEvent(self, event):
        menu = QMenu(self)

        delete_action = QAction(_("Delete"), self)
        delete_action.triggered.connect(self._delete_from_library)
        menu.addAction(delete_action)

        menu.addSeparator()

        edit_desc_action = QAction(_("Edit Description"), self)
        edit_desc_action.triggered.connect(self._edit_description)
        menu.addAction(edit_desc_action)

        menu.exec_(event.globalPos())

    def _delete_from_library(self):
        lib = getattr(self, '_library', None)
        if lib is not None and hasattr(lib, 'delete_shared_node'):
            lib.delete_shared_node('llm', self.chain_file, self.llm_node_data)

    def _edit_description(self):
        current_desc = self.llm_node_data.get('semantic_description', '')
        text, ok = QInputDialog.getText(
            self,
            _("LLM Node Description"),
            _("Enter description for this LLM node:"),
            QLineEdit.Normal,
            current_desc
        )

        if ok:
            try:
                # Update the LLM node data in memory
                self.llm_node_data['semantic_description'] = text

                # Update JSON in chain file
                if os.path.exists(self.chain_file):
                    with open(self.chain_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)

                    # Find and update the matching LLM node
                    llm_nodes = data.get('llm_nodes', [])
                    node_id = self.llm_node_data.get('node_id', '')
                    for ln in llm_nodes:
                        if ln.get('node_id') == node_id:
                            ln['semantic_description'] = text
                            break

                    with open(self.chain_file, 'w', encoding='utf-8') as f:
                        json.dump(data, f, indent=2)

                # Update tooltip
                model = self.llm_node_data.get('model', 'llama3.2:latest')
                prompt_preview = self.llm_node_data.get('prompt', '').replace('\n', ' ').strip()[:60]
                tooltip = f"LLM Node: {self.text()}\n"
                tooltip += f"Chain: {os.path.basename(self.chain_file)}\n"
                tooltip += f"Model: {model}\n"
                if text:
                    tooltip += f"Description: {text}\n"
                if prompt_preview:
                    tooltip += f"Prompt: {prompt_preview}\n"
                tooltip += f"ID: {self.llm_node_data.get('node_id', 'unknown')}"
                self.setToolTip(tooltip)

            except Exception as e:
                logger.error(f"Failed to save LLM node description: {e}")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_start_pos = event.pos()
        return super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_start_pos is None:
            return super().mouseMoveEvent(event)

        if (event.pos() - self._drag_start_pos).manhattanLength() >= 8:
            mime = QMimeData()
            mime.setData('application/x-looper-node-type', b'llm')
            # Pass the LLM node configuration as JSON
            node_config = json.dumps({
                'chain_file': self.chain_file,
                'llm_node_data': self.llm_node_data
            })
            mime.setData('application/x-looper-llm-config', node_config.encode('utf-8'))

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
        # Click behavior: Add LLM node to graph with shared chain file reference
        if self._drag_start_pos is not None and (event.pos() - self._drag_start_pos).manhattanLength() < 8:
            try:
                if hasattr(self.graph_view, 'node_operations'):
                    self.graph_view.node_operations.add_llm_node_with_shared_file(
                        self.chain_file,
                        self.llm_node_data
                    )
            except Exception as e:
                logger.error(f"LLMNodesLibrary click handler error: {e}")
        self._drag_start_pos = None
        return super().mouseReleaseEvent(event)


class DraggableSequenceFileButton(QFrame):
    """Card-style widget representing a sequence file, supporting drag-to-add and folder move button."""

    def __init__(self, label: str, file_path: str, graph_view, parent=None):
        self._full_text = os.path.splitext(label)[0]
        super().__init__(parent)
        self.file_path = file_path
        self.graph_view = graph_view
        self._drag_start_pos = None
        self.setCursor(Qt.OpenHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(48)

        # Load description for tooltip
        self._description = ""
        try:
            if os.path.exists(self.file_path):
                with open(self.file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self._description = data.get('description', '')
        except Exception:
            pass

        if self._description:
            self.setToolTip(f"{self._full_text}\n{self._description}")
        else:
            self.setToolTip(self._full_text)

        # Layout: [label] [folder_btn]
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 6, 6)
        layout.setSpacing(6)

        # Text label (takes remaining space)
        self._label = QLabel(self._full_text, self)
        self._label.setStyleSheet(
            f"color: {TEXT_COLOR}; font-weight: 600; background: transparent; border: none;"
        )
        layout.addWidget(self._label, 1)

        # Folder button to move sequence between collections
        self._folder_btn = QToolButton(self)
        self._folder_btn.setFixedSize(22, 22)
        fld_icon = _painter_icon_folder(14)
        if fld_icon and not fld_icon.isNull():
            self._folder_btn.setIcon(fld_icon)
            self._folder_btn.setIconSize(QSize(14, 14))
        self._folder_btn.setToolTip(_("Move to collection"))
        self._folder_btn.setCursor(Qt.PointingHandCursor)
        self._folder_btn.setStyleSheet(
            f"""
            QToolButton {{
                background-color: transparent;
                border: none;
                border-radius: 11px;
                padding: 2px;
            }}
            QToolButton:hover {{
                background-color: #1FFFFFFF;
            }}
            """
        )
        self._folder_btn.clicked.connect(self._show_seq_collection_menu)
        layout.addWidget(self._folder_btn)

        # Card styling
        self.setStyleSheet(
            f"""
            DraggableSequenceFileButton {{
                background-color: rgba(255, 255, 255, 0.04);
                color: {TEXT_COLOR};
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 12px;
                font-weight: 600;
            }}
            DraggableSequenceFileButton:hover {{ 
                border-color: {ACCENT_COLOR}; 
                background-color: rgba(255, 255, 255, 0.08);
            }}
            """
        )

    def update_display_text(self):
        """Elide label text to fit available width."""
        available_width = self.width() - 70  # margins + folder btn + spacing
        fm = self.fontMetrics()
        elided = fm.elidedText(self._full_text, Qt.ElideRight, max(0, available_width))
        self._label.setText(elided)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        edit_desc_action = QAction(_("Edit Description"), self)
        edit_desc_action.triggered.connect(self._edit_description)
        menu.addAction(edit_desc_action)

        menu.addSeparator()
        coll_menu = menu.addMenu(_painter_icon_folder(14), _("Move to Collection"))
        parent_lib = self.parent()
        while parent_lib and not isinstance(parent_lib, ChainsLibrary):
            parent_lib = parent_lib.parent()
        if parent_lib:
            old_type = parent_lib._active_coll_type
            parent_lib._active_coll_type = 'sequence'
            collections = parent_lib._get_collection_names()
            parent_lib._active_coll_type = old_type
            for coll in [''] + collections:
                label = _("(None)") if coll == '' else coll
                act = QAction(label, self)
                if coll == '':
                    act.setIcon(_painter_icon_folder(14))
                act.triggered.connect(lambda checked, c=coll: self._set_seq_collection(c))
                coll_menu.addAction(act)

        menu.exec_(event.globalPos())

    def _set_seq_collection(self, collection_name):
        try:
            if os.path.exists(self.file_path):
                with open(self.file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            else:
                data = {}
            data['collection'] = collection_name
            with open(self.file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            parent_lib = self.parent()
            while parent_lib and not isinstance(parent_lib, ChainsLibrary):
                parent_lib = parent_lib.parent()
            if parent_lib:
                parent_lib.refresh_sequences_list()
        except Exception as e:
            logger.error(f"Failed to set sequence collection: {e}")

    def _show_seq_collection_menu(self):
        menu = QMenu(self)
        parent_lib = self.parent()
        while parent_lib and not isinstance(parent_lib, ChainsLibrary):
            parent_lib = parent_lib.parent()
        if parent_lib:
            old_type = parent_lib._active_coll_type
            parent_lib._active_coll_type = 'sequence'
            collections = parent_lib._get_collection_names()
            parent_lib._active_coll_type = old_type
            for coll in [''] + collections:
                label = _("(None)") if coll == '' else coll
                act = QAction(label, self)
                if coll == '':
                    act.setIcon(_painter_icon_folder(14))
                act.triggered.connect(lambda checked, c=coll: self._set_seq_collection(c))
                menu.addAction(act)
        btn = self._folder_btn
        menu.exec_(btn.mapToGlobal(btn.rect().bottomLeft()))

    def _sync_seq_folder_btn_visibility(self):
        try:
            parent_lib = self.parent()
            while parent_lib and not isinstance(parent_lib, ChainsLibrary):
                parent_lib = parent_lib.parent()
            if parent_lib:
                old_type = parent_lib._active_coll_type
                parent_lib._active_coll_type = 'sequence'
                has_colls = bool(parent_lib._get_collection_names())
                parent_lib._active_coll_type = old_type
                self._folder_btn.setVisible(has_colls)
        except Exception:
            pass

    def _edit_description(self):
        current_desc = ""
        try:
            with open(self.file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                current_desc = data.get('description', '')
        except Exception as e:
            logger.error(f"Failed to load sequence for description: {e}")

        text, ok = QInputDialog.getText(
            self,
            _("Sequence Description"),
            _("Enter description for this sequence:"),
            QLineEdit.Normal,
            current_desc
        )

        if ok:
            try:
                if os.path.exists(self.file_path):
                    with open(self.file_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                else:
                    data = {}

                data['description'] = text
                self._description = text

                with open(self.file_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2)

                # Update tooltip
                if text:
                    self.setToolTip(f"{self._full_text}\n{text}")
                else:
                    self.setToolTip(self._full_text)

            except Exception as e:
                logger.error(f"Failed to save sequence description: {e}")

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
                logger.error(f"SequencesLibrary click handler error: {e}")
        self._drag_start_pos = None
        return super().mouseReleaseEvent(event)


class DraggableWebSequenceFileButton(QFrame):
    """Card-style widget representing a web session file (WebSequenceNode).

    Supports drag-to-add (drops a web_sequence node with the session file)
    and click-to-add (opens the node with the session attached).
    """

    def __init__(self, label: str, file_path: str, graph_view, parent=None):
        self._full_text = os.path.splitext(label)[0]
        super().__init__(parent)
        self.file_path = file_path
        self.graph_view = graph_view
        self._drag_start_pos = None
        self.setCursor(Qt.OpenHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(48)
        self.setToolTip(self._full_text)

        # Layout: [label]
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 6, 6)
        layout.setSpacing(6)

        self._label = QLabel(self._full_text, self)
        self._label.setStyleSheet(
            f"color: {TEXT_COLOR}; font-weight: 600; background: transparent; border: none;"
        )
        layout.addWidget(self._label, 1)

        # Card styling (matches the sequence cards)
        self.setStyleSheet(
            f"""
            DraggableWebSequenceFileButton {{
                background-color: rgba(255, 255, 255, 0.04);
                color: {TEXT_COLOR};
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 12px;
                font-weight: 600;
            }}
            DraggableWebSequenceFileButton:hover {{ 
                border-color: {ACCENT_COLOR}; 
                background-color: rgba(255, 255, 255, 0.08);
            }}
            """
        )

    def update_display_text(self):
        """Elide label text to fit available width."""
        available_width = self.width() - 30  # margins + spacing
        fm = self.fontMetrics()
        elided = fm.elidedText(self._full_text, Qt.ElideRight, max(0, available_width))
        self._label.setText(elided)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_start_pos = event.pos()
        return super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_start_pos is None:
            return super().mouseMoveEvent(event)
        if (event.pos() - self._drag_start_pos).manhattanLength() >= 8:
            mime = QMimeData()
            mime.setData('application/x-looper-node-type', b'web_sequence')
            mime.setText(self.file_path)
            mime.setData('application/x-looper-web-session-file', self.file_path.encode('utf-8'))
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
                    self.graph_view.node_operations.add_web_sequence_from_file(self.file_path)
            except Exception as e:
                logger.error(f"Web sequences library click handler error: {e}")
        self._drag_start_pos = None
        return super().mouseReleaseEvent(event)


class ChainsLibrary(QWidget):
    """Collapsible left panel containing a library of chains with tabs for context and code nodes."""
    
    def __init__(self, graph_view, parent=None):
        super().__init__(parent)
        self.graph_view = graph_view
        self._collapsed = False
        self._collection_names = []  # in-memory list of known chain collections
        self._seq_collection_names = []  # in-memory list of known sequence collections
        self._active_coll_type = 'chain'
        
        # Watch the chains folder for changes
        try:
            self._watcher = QFileSystemWatcher(self)
            folder = getattr(self.graph_view, 'chains_folder', None)
            if folder:
                os.makedirs(folder, exist_ok=True)
                self._watcher.addPath(folder)
                self._watcher.directoryChanged.connect(self.refresh_chains_list)
                # Also watch files for content changes if needed, but directory is enough for list
        except Exception as e:
            logger.error(f"ChainsLibrary: Failed to initialize watcher: {e}")
            self._watcher = None

        # Watch the sequences folder for changes (auto-refresh sequences list)
        try:
            self._sequences_watcher = QFileSystemWatcher(self)
            seq_folder = getattr(self.graph_view, 'sequences_folder', None)
            if seq_folder:
                os.makedirs(seq_folder, exist_ok=True)
                self._sequences_watcher.addPath(seq_folder)
                self._sequences_watcher.directoryChanged.connect(self._on_sequences_folder_changed)
        except Exception as e:
            logger.error(f"ChainsLibrary: Failed to initialize sequences watcher: {e}")
            self._sequences_watcher = None

        # Watch the web_sequences folder for changes (auto-refresh web sessions list)
        try:
            self._web_sequences_watcher = QFileSystemWatcher(self)
            web_seq_folder = getattr(self.graph_view, 'web_sequences_folder', None)
            if web_seq_folder:
                os.makedirs(web_seq_folder, exist_ok=True)
                self._web_sequences_watcher.addPath(web_seq_folder)
                self._web_sequences_watcher.directoryChanged.connect(self._on_web_sequences_folder_changed)
        except Exception as e:
            logger.error(f"ChainsLibrary: Failed to initialize web sequences watcher: {e}")
            self._web_sequences_watcher = None
            
        self._build_ui()

    def _build_ui(self):
        self.setMinimumWidth(320)
        self.setMaximumWidth(320)
        
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(4, 0, 0, 0)
        main_layout.setSpacing(0)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet("background: transparent;")

        # Content frame
        self._content_frame = QFrame(self)
        self._content_frame.setObjectName("chainsContent")
        self._content_frame.setStyleSheet(
            f"QFrame#chainsContent {{ background-color: {SIDE_PANEL_BG}; border-radius: 10px; }}"
        )
        content_layout = QVBoxLayout(self._content_frame)
        content_layout.setContentsMargins(8, 8, 8, 8)
        content_layout.setSpacing(6)

        # Tab buttons container
        tab_buttons_layout = QHBoxLayout()
        tab_buttons_layout.setSpacing(4)
        
        # -- Helper for icon-only tab buttons --
        def _make_tab_btn(icon_node_type, icon_tabler_name, tooltip_text, checked_color, is_checked=False):
            btn = QPushButton()
            btn.setFixedHeight(32)
            btn.setCheckable(True)
            btn.setChecked(is_checked)
            # Try node icon first, then tabler icon
            ico = get_node_icon(icon_node_type, 18) if icon_node_type else None
            if ico is None or ico.isNull():
                ico = load_tabler_icon(icon_tabler_name, 18)
            if ico and not ico.isNull():
                btn.setIcon(ico)
                btn.setIconSize(QSize(18, 18))
            btn.setToolTip(tooltip_text)
            btn.setStyleSheet(
                f"""
                QPushButton {{
                    background-color: rgba(255, 255, 255, 0.04);
                    color: {TEXT_COLOR};
                    border: 1px solid rgba(255, 255, 255, 0.08);
                    border-radius: 8px;
                    font-weight: 600;
                }}
                QPushButton:checked {{
                    background-color: {checked_color};
                    color: #000000;
                    border-color: {ACCENT_COLOR};
                }}
                QPushButton:hover {{
                    background-color: rgba(255, 255, 255, 0.08);
                    border-color: {ACCENT_COLOR};
                }}
                """
            )
            return btn

        # Icon definitions: (icon_node_type, tabler_icon_fallback, tooltip_text, checked_color)
        _tab_icons = [
            (None,   'FILES',         _("Sequences"), 'rgba(255, 255, 255, 0.04)'),
            ('chain_import', 'BOX_MULTIPLE', _("Chains"),   CHAIN_IMPORT_COLOR),
            ('llm',   'ROBOT',         _("LLM"),       LLM_COLOR),
            ('context', 'DATABASE',      _("Context"),   CONTEXT_NODE_COLOR),
            ('code',  'CODE',          _("Code"),      CODE_NODE_COLOR),
            ('web_sequence', 'GLOBE', _("Web Sequences"), WEB_SEQUENCE_COLOR),
        ]

        self._sequences_tab_btn = _make_tab_btn(*_tab_icons[0], is_checked=False)
        self._chains_tab_btn = _make_tab_btn(*_tab_icons[1], is_checked=True)
        self._llm_tab_btn = _make_tab_btn(*_tab_icons[2], is_checked=False)
        self._context_tab_btn = _make_tab_btn(*_tab_icons[3], is_checked=False)
        self._code_tab_btn = _make_tab_btn(*_tab_icons[4], is_checked=False)
        self._web_seq_tab_btn = _make_tab_btn(*_tab_icons[5], is_checked=False)

        self._sequences_tab_btn.clicked.connect(self._switch_to_sequences_tab)
        self._chains_tab_btn.clicked.connect(self._switch_to_chains_tab)
        self._llm_tab_btn.clicked.connect(self._switch_to_llm_tab)
        self._context_tab_btn.clicked.connect(self._switch_to_context_tab)
        self._code_tab_btn.clicked.connect(self._switch_to_code_tab)
        self._web_seq_tab_btn.clicked.connect(self._switch_to_web_seq_tab)

        tab_buttons_layout.addWidget(self._sequences_tab_btn)
        tab_buttons_layout.addWidget(self._chains_tab_btn)
        tab_buttons_layout.addWidget(self._llm_tab_btn)
        tab_buttons_layout.addWidget(self._context_tab_btn)
        tab_buttons_layout.addWidget(self._code_tab_btn)
        tab_buttons_layout.addWidget(self._web_seq_tab_btn)
        content_layout.addLayout(tab_buttons_layout)

        # Search bar for filtering nodes in the active tab
        # Placed BEFORE all scroll areas so it stays at the top for every tab
        self._search_field = QLineEdit(self._content_frame)
        self._search_field.setPlaceholderText(_("Filter nodes…"))
        self._search_field.setStyleSheet(
            f"""
            QLineEdit {{
                background-color: rgba(255, 255, 255, 0.04);
                color: {TEXT_COLOR};
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 20px;
                padding: 4px 12px;
                font-size: 12px;
                min-height: 24px;
            }}
            QLineEdit:focus {{
                border-color: {ACCENT_COLOR};
            }}
            QLineEdit::placeholder {{
                color: rgba(255, 255, 255, 0.3);
            }}
            """
        )
        content_layout.addWidget(self._search_field)
        self._search_field.textChanged.connect(self._filter_active_tab)

        # --- Collection management bar (visible only on Chains tab) ---
        self._collections_bar = QFrame(self._content_frame)
        self._collections_bar.setVisible(False)
        coll_bar_layout = QHBoxLayout(self._collections_bar)
        coll_bar_layout.setContentsMargins(0, 0, 0, 0)
        coll_bar_layout.setSpacing(4)

        self._collections_combo = QComboBox()
        self._collections_combo.setMinimumHeight(28)
        self._collections_combo.setStyleSheet(
            f"""
            QComboBox {{
                background-color: rgba(255, 255, 255, 0.04);
                color: {TEXT_COLOR};
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 14px;
                padding: 2px 10px;
                font-size: 12px;
                min-height: 24px;
            }}
            QComboBox:hover {{
                border-color: {ACCENT_COLOR};
            }}
            QComboBox::drop-down {{
                border: none;
                width: 18px;
            }}
            QComboBox::down-arrow {{
                image: none;
                border: none;
            }}
            QComboBox QAbstractItemView {{
                background-color: {DARK_GREY};
                color: {TEXT_COLOR};
                border: 1px solid rgba(255, 255, 255, 0.08);
                selection-background-color: {ACCENT_COLOR};
                selection-color: #000000;
            }}
            """
        )
        self._collections_combo.currentIndexChanged.connect(self._on_collection_filter_changed)
        coll_bar_layout.addWidget(self._collections_combo, 1)

        def _make_coll_btn(text, tooltip, color=None):
            btn = QPushButton(text)
            btn.setFixedSize(28, 28)
            btn.setToolTip(tooltip)
            btn.setCursor(Qt.PointingHandCursor)
            ss = (
                f"background-color: rgba(255,255,255,0.04); color: {TEXT_COLOR};"
                f"border: 1px solid rgba(255,255,255,0.08); border-radius: 14px; font-size: 14px; font-weight: bold;"
            )
            if color:
                ss += f"color: {color};"
            btn.setStyleSheet(ss + "QPushButton:hover { border-color: " + ACCENT_COLOR + "; }")
            return btn

        self._add_coll_btn = _make_coll_btn("+", _("Create collection"))
        self._add_coll_btn.clicked.connect(self._create_collection)
        coll_bar_layout.addWidget(self._add_coll_btn)

        self._rename_coll_btn = _make_coll_btn("✎", _("Rename collection"))
        self._rename_coll_btn.clicked.connect(self._rename_collection)
        coll_bar_layout.addWidget(self._rename_coll_btn)

        self._delete_coll_btn = _make_coll_btn("✕", _("Delete collection"), "#FF6B6B")
        self._delete_coll_btn.clicked.connect(self._delete_collection)
        coll_bar_layout.addWidget(self._delete_coll_btn)

        content_layout.addWidget(self._collections_bar)
        # --- end collection bar ---

        # Scroll area for sequences (initially hidden, shown when Sequences tab active)
        self._sequences_scroll_area = QScrollArea()
        self._sequences_scroll_area.setWidgetResizable(True)
        self._sequences_scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._sequences_scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._sequences_scroll_area.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self._sequences_scroll_area.setVisible(False)

        self._sequences_container = QFrame()
        self._sequences_container_layout = QVBoxLayout(self._sequences_container)
        self._sequences_container_layout.setContentsMargins(0, 4, 8, 4)
        self._sequences_container_layout.setSpacing(8)
        self._sequences_container_layout.addStretch()

        self._sequences_scroll_area.setWidget(self._sequences_container)
        content_layout.addWidget(self._sequences_scroll_area)

        # Scroll area for web sequences (initially hidden)
        self._web_seq_scroll_area = QScrollArea()
        self._web_seq_scroll_area.setWidgetResizable(True)
        self._web_seq_scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._web_seq_scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._web_seq_scroll_area.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self._web_seq_scroll_area.setVisible(False)

        self._web_seq_container = QFrame()
        self._web_seq_container_layout = QVBoxLayout(self._web_seq_container)
        self._web_seq_container_layout.setContentsMargins(0, 4, 8, 4)
        self._web_seq_container_layout.setSpacing(8)
        self._web_seq_container_layout.addStretch()

        self._web_seq_scroll_area.setWidget(self._web_seq_container)
        content_layout.addWidget(self._web_seq_scroll_area)

        # Scroll area for chains
        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._scroll_area.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        
        self._container = QFrame()
        self._container_layout = QVBoxLayout(self._container)
        self._container_layout.setContentsMargins(0, 4, 8, 4)
        self._container_layout.setSpacing(8)
        self._container_layout.addStretch()
        
        self._scroll_area.setWidget(self._container)
        content_layout.addWidget(self._scroll_area)
        
        # Scroll area for context nodes (initially hidden)
        self._context_scroll_area = QScrollArea()
        self._context_scroll_area.setWidgetResizable(True)
        self._context_scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._context_scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._context_scroll_area.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self._context_scroll_area.setVisible(False)
        
        self._context_container = QFrame()
        self._context_container_layout = QVBoxLayout(self._context_container)
        self._context_container_layout.setContentsMargins(0, 4, 8, 4)
        self._context_container_layout.setSpacing(8)
        self._context_container_layout.addStretch()
        
        self._context_scroll_area.setWidget(self._context_container)
        content_layout.addWidget(self._context_scroll_area)

        # Scroll area for code nodes (initially hidden)
        self._code_scroll_area = QScrollArea()
        self._code_scroll_area.setWidgetResizable(True)
        self._code_scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._code_scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._code_scroll_area.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self._code_scroll_area.setVisible(False)

        self._code_container = QFrame()
        self._code_container_layout = QVBoxLayout(self._code_container)
        self._code_container_layout.setContentsMargins(0, 4, 8, 4)
        self._code_container_layout.setSpacing(8)
        self._code_container_layout.addStretch()

        self._code_scroll_area.setWidget(self._code_container)
        content_layout.addWidget(self._code_scroll_area)
        
        # Scroll area for LLM nodes (initially hidden)
        self._llm_scroll_area = QScrollArea()
        self._llm_scroll_area.setWidgetResizable(True)
        self._llm_scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._llm_scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._llm_scroll_area.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self._llm_scroll_area.setVisible(False)
        
        self._llm_container = QFrame()
        self._llm_container_layout = QVBoxLayout(self._llm_container)
        self._llm_container_layout.setContentsMargins(0, 4, 8, 4)
        self._llm_container_layout.setSpacing(8)
        self._llm_container_layout.addStretch()
        
        self._llm_scroll_area.setWidget(self._llm_container)
        content_layout.addWidget(self._llm_scroll_area)
        
        # Trash bin for deleting saved files/library nodes via drag-and-drop
        self.trash_bin = TrashBinWidget(self._content_frame)
        self.trash_bin.set_node_drop_handler(self.delete_shared_node)
        content_layout.addWidget(self.trash_bin)
        
        main_layout.addWidget(self._content_frame)

        # Toggle button
        self._toggle_btn = QToolButton(self.graph_view)
        ico = make_toggle_triangle_icon('LEFT', 14, GRAPH_PLANE)
        self._toggle_btn.setIcon(ico)
        self._toggle_btn.setIconSize(QSize(14, 14))
        self._toggle_btn.setFixedSize(24, 28)
        self._toggle_btn.setCursor(Qt.PointingHandCursor)
        self._toggle_btn.setAutoRaise(True)
        self._toggle_btn.setStyleSheet(
            f"QToolButton {{ background-color: transparent; border: none; }}"
            f"QToolButton:hover {{ background-color: rgba(255,255,255,0.06); border-radius: 4px; }}"
        )
        self._toggle_btn.clicked.connect(self._toggle_panel)
        
        # Initial list refresh (start with sequences visible by default)
        self._switch_to_sequences_tab()
        self._refresh_sequences_list()
        self.refresh_chains_list()
        self._refresh_context_nodes_list()
        self._refresh_code_nodes_list()
        self._refresh_llm_nodes_list()
        self._refresh_web_seq_list()
        
        # Position toggle button
        QTimer.singleShot(100, self._position_toggle)

    def _toggle_panel(self):
        self._collapsed = not self._collapsed
        if self._collapsed:
            self.setMinimumWidth(0)
            start_w = max(0, self.width())
            self._anim_panel = QPropertyAnimation(self, b"maximumWidth")
            self._anim_panel.setDuration(200)
            self._anim_panel.setStartValue(start_w)
            self._anim_panel.setEndValue(0)
            self._anim_panel.setEasingCurve(QEasingCurve.InOutQuad)
            self._anim_panel.finished.connect(self._on_panel_collapse)
            self._anim_panel.start(QAbstractAnimation.DeleteWhenStopped)
            ico = make_toggle_triangle_icon('RIGHT', 14, SIDE_PANEL_BG)
            self._toggle_btn.setIcon(ico)
            self._toggle_btn.setIconSize(QSize(14, 14))
        else:
            self._content_frame.setVisible(True)
            self._anim_panel = QPropertyAnimation(self, b"maximumWidth")
            self._anim_panel.setDuration(200)
            self._anim_panel.setStartValue(0)
            self._anim_panel.setEndValue(320)
            self._anim_panel.setEasingCurve(QEasingCurve.InOutQuad)
            self._anim_panel.finished.connect(self._on_panel_expand)
            self._anim_panel.start(QAbstractAnimation.DeleteWhenStopped)
            ico = make_toggle_triangle_icon('LEFT', 14, GRAPH_PLANE)
            self._toggle_btn.setIcon(ico)
            self._toggle_btn.setIconSize(QSize(14, 14))

        QTimer.singleShot(0, self._position_toggle)
        QTimer.singleShot(250, self._position_toggle)

    def _on_panel_collapse(self):
        self._content_frame.setVisible(False)
        self.setMinimumWidth(0)
        QTimer.singleShot(0, self._position_toggle)

    def _on_panel_expand(self):
        self.setMinimumWidth(320)
        QTimer.singleShot(0, self._position_toggle)

    def _position_toggle(self):
        if not self._toggle_btn: return
        pos = self.mapTo(self.graph_view, QPoint(0, 0))
        # For left panel, toggle is on the right side of the panel
        x = pos.x() + self.width() - 2
        y = pos.y() + (self.height() // 2) - 14
        self._toggle_btn.move(x, y)
        self._toggle_btn.raise_()

    def delete_shared_node(self, kind, chain_file, node_data):
        """Delete a saved LLM/Code/Context node that lives inside a chain file.

        Removes the entry from its owning chain and strips live references
        (shared context clones) from every other chain that uses it.  Warns
        first when other chains reference the node, and refuses to edit a
        chain that is currently open on the canvas (the open graph would
        re-save it and silently resurrect the node).
        """
        list_key = {
            'llm': 'llm_nodes',
            'code': 'code_nodes',
            'context': 'context_nodes',
        }.get(kind)
        if not list_key or not isinstance(node_data, dict) or not chain_file:
            return False
        node_id = str(node_data.get('node_id') or node_data.get('id') or '').strip()
        if not node_id:
            return False

        folder = getattr(self.graph_view, 'chains_folder', None)
        source = os.path.abspath(str(chain_file))
        if not folder or not os.path.isfile(source):
            return False

        def _same_chain(a, b):
            a, b = str(a or ''), str(b or '')
            if not a or not b:
                return False
            if os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b)):
                return True
            return bool(os.path.basename(a)) and os.path.basename(a) == os.path.basename(b)

        display = str(
            node_data.get('label')
            or node_data.get('semantic_description')
            or node_data.get('description')
            or node_data.get('name')
            or node_id[:8]
        )

        # Chains that reference this node through a shared context clone.
        user_chains = []
        try:
            for name in sorted(f for f in os.listdir(folder) if f.lower().endswith('.json')):
                path = os.path.join(folder, name)
                if _same_chain(path, source):
                    continue
                try:
                    with open(path, 'r', encoding='utf-8') as fh:
                        data = json.load(fh)
                except Exception:
                    continue
                for ctx in data.get('context_nodes', []) or []:
                    if (
                        str(ctx.get('shared_context_node_id') or '').strip() == node_id
                        and _same_chain(ctx.get('shared_context_chain_file'), source)
                    ):
                        user_chains.append(path)
                        break
        except Exception as e:
            logger.error(f"Error scanning chains for node usage: {e}")
            return False

        # Refuse to edit a chain that is open on the canvas: the next save
        # would re-add the node and silently undo the deletion.
        open_chain = ''
        try:
            open_chain = getattr(self.graph_view.config_manager, '_current_chain_file', '') or ''
        except Exception:
            pass
        blocked = [p for p in [source] + user_chains if open_chain and _same_chain(p, open_chain)]
        if blocked:
            QMessageBox.information(
                self,
                _("Delete not allowed"),
                _("\"{name}\" belongs to \"{chain}\", which is open in the editor.\nRemove the node from the editor instead.").format(
                    name=display,
                    chain=os.path.basename(blocked[0]),
                ),
            )
            return False

        if user_chains:
            names = ', '.join(os.path.splitext(os.path.basename(p))[0] for p in user_chains)
            answer = QMessageBox.warning(
                self,
                _("Node is in use"),
                _("\"{name}\" is used by: {chains}.\nDelete it anyway? It will also be removed from those chains.").format(
                    name=display,
                    chains=names,
                ),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return False

        # Collect the files to rewrite (entry removal + reference stripping),
        # then write them all at once.
        files_to_write = []
        try:
            with open(source, 'r', encoding='utf-8') as fh:
                data = json.load(fh)
            kept = [
                n for n in data.get(list_key, [])
                if str(n.get('node_id') or n.get('id') or '').strip() != node_id
            ]
            if len(kept) < len(data.get(list_key, [])):
                data[list_key] = kept
                files_to_write.append((source, data))

            for path in user_chains:
                with open(path, 'r', encoding='utf-8') as fh:
                    data = json.load(fh)
                kept = [
                    c for c in data.get('context_nodes', [])
                    if not (
                        str(c.get('shared_context_node_id') or '').strip() == node_id
                        and _same_chain(c.get('shared_context_chain_file'), source)
                    )
                ]
                if len(kept) < len(data.get('context_nodes', [])):
                    data['context_nodes'] = kept
                    files_to_write.append((path, data))
        except Exception as e:
            logger.error(f"Error preparing node deletion: {e}")
            return False

        for path, data in files_to_write:
            try:
                with open(path, 'w', encoding='utf-8') as fh:
                    json.dump(data, fh, indent=2)
            except Exception as e:
                QMessageBox.critical(
                    self,
                    _("Error"),
                    _("Failed to update {file}: {error}").format(file=os.path.basename(path), error=e),
                )
                return False

        # Refresh the node library lists so the removed entries disappear.
        self._refresh_context_nodes_list()
        self._refresh_code_nodes_list()
        self._refresh_llm_nodes_list()
        return True

    def _coll_names(self):
        return self._collection_names if self._active_coll_type == 'chain' else self._seq_collection_names

    def _coll_folder(self):
        key = 'chains_folder' if self._active_coll_type == 'chain' else 'sequences_folder'
        return getattr(self.graph_view, key, None)

    def _coll_file_name(self):
        return 'chain_collections.json' if self._active_coll_type == 'chain' else 'sequence_collections.json'

    def _collections_file(self):
        folder = self._coll_folder()
        return os.path.join(folder, self._coll_file_name()) if folder else None

    def _get_collection_names(self):
        """Return sorted unique collection names (in-memory list + file scan for active type)."""
        names = set(self._coll_names())
        folder = self._coll_folder()
        if folder and os.path.isdir(folder):
            exclude = self._coll_file_name()
            for f in os.listdir(folder):
                if f.lower().endswith('.json') and f != exclude:
                    try:
                        with open(os.path.join(folder, f), 'r', encoding='utf-8') as fp:
                            data = json.load(fp)
                            coll = data.get('collection', '')
                            if coll:
                                names.add(coll)
                    except Exception:
                        pass
        # Always surface Default + System so the move-to-collection menus
        # show them even when no chain currently uses those collections.
        if self._active_coll_type == 'chain':
            names.update(("Default", "System"))
        return sorted(names)

    def active_collection(self) -> str:
        """The collection (workspace) the user is currently building in.

        Drives workspace organization: a chain created while a collection
        filter is active is auto-annotated with it (ChainsLibrary combo:
        index 0 = "All", index 1 = Default, then System + custom names).
        """
        try:
            combo = self._collections_combo
            if combo.currentIndex() <= 1:
                return ""
            return combo.currentText().strip()
        except Exception:
            return ""

    def _try_persist_collections(self):
        cf = self._collections_file
        if cf:
            try:
                os.makedirs(os.path.dirname(cf), exist_ok=True)
                with open(cf, 'w', encoding='utf-8') as fh:
                    json.dump(self._coll_names(), fh, indent=2)
            except Exception:
                pass

    def _update_collections_combo(self):
        combo = self._collections_combo
        current = combo.currentText()
        combo.blockSignals(True)
        combo.clear()
        # Permanent top-level tabs for chain type separation
        all_label = _("All chains") if self._active_coll_type == 'chain' else _("All sequences")
        combo.addItem(all_label)
        # ponytail: permanent Default + System collections — these define the two
        # tiers of chains: Default = tools the agent can use, System = agent cognition.
        # The "Default" filter matches chains with collection="" or collection="default".
        # The "System" filter matches chains with collection="system" or collection="System".
        combo.addItem("Default")
        combo.addItem("System")
        for c in self._get_collection_names():
            if c not in ("Default", "System", ""):  # avoid duplicating permanent entries
                combo.addItem(c)
        idx = combo.findText(current)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        combo.blockSignals(False)

    def _on_collection_filter_changed(self):
        if self._active_coll_type == 'chain':
            self.refresh_chains_list()
        else:
            self._refresh_sequences_list()

    def _create_collection(self):
        text, ok = QInputDialog.getText(self, _("New Collection"), _("Collection name:"))
        if ok and text.strip():
            name = text.strip()
            names = self._coll_names()
            if name not in names:
                names.append(name)
            self._update_collections_combo()
            idx = self._collections_combo.findText(name)
            if idx >= 0:
                self._collections_combo.setCurrentIndex(idx)
            self._try_persist_collections()
            if self._active_coll_type == 'chain':
                self.refresh_chains_list()
            else:
                self._refresh_sequences_list()

    def _rename_collection(self):
        old_name = self._collections_combo.currentText()
        all_label = _("All chains") if self._active_coll_type == 'chain' else _("All sequences")
        if old_name == all_label:
            return
        text, ok = QInputDialog.getText(self, _("Rename Collection"), _("New name:"), text=old_name)
        if ok and text.strip() and text.strip() != old_name:
            new_name = text.strip()
            names = self._coll_names()
            if old_name in names:
                names.remove(old_name)
            if new_name not in names:
                names.append(new_name)
            folder = self._coll_folder()
            exclude = self._coll_file_name()
            if folder and os.path.isdir(folder):
                for f in os.listdir(folder):
                    if f.lower().endswith('.json') and f != exclude:
                        try:
                            fp = os.path.join(folder, f)
                            with open(fp, 'r', encoding='utf-8') as fh:
                                data = json.load(fh)
                            if data.get('collection') == old_name:
                                data['collection'] = new_name
                                with open(fp, 'w', encoding='utf-8') as fh:
                                    json.dump(data, fh, indent=2)
                        except Exception:
                            pass
            self._update_collections_combo()
            idx = self._collections_combo.findText(new_name)
            if idx >= 0:
                self._collections_combo.setCurrentIndex(idx)
            self._try_persist_collections()
            if self._active_coll_type == 'chain':
                self.refresh_chains_list()
            else:
                self._refresh_sequences_list()

    def _delete_collection(self):
        name = self._collections_combo.currentText()
        all_label = _("All chains") if self._active_coll_type == 'chain' else _("All sequences")
        if name == all_label:
            return
        reply = QMessageBox.question(self, _("Delete Collection"),
            _("Remove collection \"{name}\"? Items in it will not be deleted.").format(name=name),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if reply == QMessageBox.Yes:
            names = self._coll_names()
            if name in names:
                names.remove(name)
            folder = self._coll_folder()
            exclude = self._coll_file_name()
            if folder and os.path.isdir(folder):
                for f in os.listdir(folder):
                    if f.lower().endswith('.json') and f != exclude:
                        try:
                            fp = os.path.join(folder, f)
                            with open(fp, 'r', encoding='utf-8') as fh:
                                data = json.load(fh)
                            if data.get('collection') == name:
                                data['collection'] = ''
                                with open(fp, 'w', encoding='utf-8') as fh:
                                    json.dump(data, fh, indent=2)
                        except Exception:
                            pass
            self._update_collections_combo()
            self._try_persist_collections()
            if self._active_coll_type == 'chain':
                self.refresh_chains_list()
            else:
                self._refresh_sequences_list()

    def refresh_sequences_list(self, path=None):
        """Public method to refresh the sequences list in the Sequences tab."""
        if self._sequences_scroll_area.isVisible():
            self._refresh_sequences_list()
        else:
            # Still rebuild behind the scenes
            self._refresh_sequences_list()

    def refresh_chains_list(self, path=None):
        # Determine current collection filter (only when the Chains tab is active)
        selected = self._collections_combo.currentText() if hasattr(self, '_collections_combo') else ''
        filter_coll = '' if self._active_coll_type != 'chain' or selected == _("All chains") else selected

        # Clear existing buttons (except stretch)
        while self._container_layout.count() > 1:
            item = self._container_layout.takeAt(0)
            if item.widget():
                item.widget().setVisible(False)
                item.widget().deleteLater()
        
        folder = getattr(self.graph_view, 'chains_folder', None)
        if folder and os.path.isdir(folder):
            try:
                files = sorted([f for f in os.listdir(folder) if f.lower().endswith('.json')])
                for name in files:
                    full_path = os.path.join(folder, name)
                    # Skip if filtered and chain doesn't belong to this collection
                    if filter_coll:
                        try:
                            with open(full_path, 'r', encoding='utf-8') as fh:
                                data = json.load(fh)
                            coll = data.get('collection', '') or ''
                            # "Default" filter matches empty collection or "default"
                            if filter_coll == "Default":
                                if coll not in ("", "default", "Default"):
                                    continue
                            # "System" filter matches "system" or "System"
                            elif filter_coll == "System":
                                if coll.lower() != "system":
                                    continue
                            else:
                                if coll != filter_coll:
                                    continue
                        except Exception:
                            continue
                    btn = DraggableChainButton(name, full_path, self.graph_view, self)
                    self._container_layout.insertWidget(self._container_layout.count() - 1, btn)
            except Exception as e:
                logger.error(f"Error listing chains: {e}")
        
        if self._container_layout.count() == 1: # Only stretch
            placeholder = QLabel(_("No chains in this collection") if filter_coll else _("No chains"))
            placeholder.setAlignment(Qt.AlignCenter)
            placeholder.setStyleSheet(f"color: {TEXT_COLOR}; opacity: 0.5; padding: 20px;")
            self._container_layout.insertWidget(0, placeholder)
        QTimer.singleShot(0, self._sync_chain_buttons)

    def _sync_chain_buttons(self):
        for i in range(self._container_layout.count()):
            item = self._container_layout.itemAt(i)
            w = item.widget() if item else None
            if isinstance(w, DraggableChainButton):
                w.update_display_text()

    def _on_sequences_folder_changed(self, path=None):
        """Handle sequences folder change events from QFileSystemWatcher with debounce."""
        # Debounce: cancel any pending refresh and schedule a new one
        if hasattr(self, '_seq_refresh_timer') and self._seq_refresh_timer:
            self._seq_refresh_timer.stop()
        self._seq_refresh_timer = QTimer(self)
        self._seq_refresh_timer.setSingleShot(True)
        self._seq_refresh_timer.setInterval(200)
        self._seq_refresh_timer.timeout.connect(self._refresh_sequences_list)
        self._seq_refresh_timer.start()

    def _refresh_sequences_list(self, path=None):
        """Refresh the list of sequence files in the Sequences tab."""
        selected = self._collections_combo.currentText() if hasattr(self, '_collections_combo') else ''
        filter_coll = '' if self._active_coll_type != 'sequence' or selected == _("All sequences") else selected

        # Clear existing buttons (except stretch)
        while self._sequences_container_layout.count() > 1:
            item = self._sequences_container_layout.takeAt(0)
            if item.widget():
                item.widget().setVisible(False)
                item.widget().deleteLater()

        folder = getattr(self.graph_view, 'sequences_folder', None)
        if folder and os.path.isdir(folder):
            try:
                files = sorted([f for f in os.listdir(folder) if f.lower().endswith('.json')])
                nested = os.path.join(folder, 'sequences')
                if os.path.isdir(nested):
                    extra = [f for f in os.listdir(nested) if f.lower().endswith('.json')]
                    files = sorted(set(files + extra))
                for name in files:
                    p1 = os.path.join(folder, name)
                    p2 = os.path.join(folder, 'sequences', name)
                    full_path = p1 if os.path.exists(p1) else p2
                    if filter_coll:
                        try:
                            with open(full_path, 'r', encoding='utf-8') as fh:
                                data = json.load(fh)
                            if data.get('collection', '') != filter_coll:
                                continue
                        except Exception:
                            continue
                    btn = DraggableSequenceFileButton(name, full_path, self.graph_view, self)
                    self._sequences_container_layout.insertWidget(self._sequences_container_layout.count() - 1, btn)
            except Exception as e:
                logger.error(f"Error listing sequences: {e}")

        if self._sequences_container_layout.count() == 1:  # Only stretch
            placeholder = QLabel(_("No sequences in this collection") if filter_coll else _("No sequences"))
            placeholder.setAlignment(Qt.AlignCenter)
            placeholder.setStyleSheet(f"color: {TEXT_COLOR}; opacity: 0.5; padding: 20px;")
            self._sequences_container_layout.insertWidget(0, placeholder)
        # Force immediate layout recalculation to prevent cards from stacking
        self._sequences_container_layout.activate()
        self._sequences_container.updateGeometry()
        QTimer.singleShot(0, self._sync_sequence_buttons)

    def _sync_sequence_buttons(self):
        """Sync sequence button widths."""
        if not hasattr(self, '_sequences_scroll_area') or not self._sequences_scroll_area:
            return
        viewport = self._sequences_scroll_area.viewport()
        if not viewport:
            return
        target_width = max(0, viewport.width() - 30)
        for i in range(self._sequences_container_layout.count()):
            item = self._sequences_container_layout.itemAt(i)
            w = item.widget() if item else None
            if isinstance(w, DraggableSequenceFileButton):
                w.setFixedWidth(target_width)
                w.update_display_text()

    def _on_web_sequences_folder_changed(self, path=None):
        """Handle web_sequences folder change events with debounce."""
        if hasattr(self, '_web_seq_refresh_timer') and self._web_seq_refresh_timer:
            self._web_seq_refresh_timer.stop()
        self._web_seq_refresh_timer = QTimer(self)
        self._web_seq_refresh_timer.setSingleShot(True)
        self._web_seq_refresh_timer.setInterval(200)
        self._web_seq_refresh_timer.timeout.connect(self._refresh_web_seq_list)
        self._web_seq_refresh_timer.start()

    def refresh_web_seq_list(self, path=None):
        """Public method to refresh the web sequences list in the Web Sequences tab."""
        self._refresh_web_seq_list()

    def _refresh_web_seq_list(self, path=None):
        """Refresh the list of web session files in the Web Sequences tab."""
        # Clear existing buttons (except stretch)
        while self._web_seq_container_layout.count() > 1:
            item = self._web_seq_container_layout.takeAt(0)
            if item.widget():
                item.widget().setVisible(False)
                item.widget().deleteLater()

        folder = getattr(self.graph_view, 'web_sequences_folder', None)
        if folder and os.path.isdir(folder):
            try:
                files = sorted([f for f in os.listdir(folder) if f.lower().endswith('.json')])
                for name in files:
                    full_path = os.path.join(folder, name)
                    # The session may carry a user-chosen name in its metadata
                    # (chosen at record time) - prefer it over the timestamped
                    # filename so the library shows what the user named it.
                    display = os.path.splitext(name)[0]
                    try:
                        with open(full_path, 'r', encoding='utf-8') as fh:
                            meta = json.load(fh).get('metadata', {}) or {}
                        if meta.get('name'):
                            display = str(meta['name']).strip()
                    except Exception:
                        pass
                    btn = DraggableWebSequenceFileButton(display, full_path, self.graph_view, self)
                    self._web_seq_container_layout.insertWidget(self._web_seq_container_layout.count() - 1, btn)
            except Exception as e:
                logger.error(f"Error listing web sequences: {e}")

        if self._web_seq_container_layout.count() == 1:  # Only stretch
            placeholder = QLabel(_("No web sequences"))
            placeholder.setAlignment(Qt.AlignCenter)
            placeholder.setStyleSheet(f"color: {TEXT_COLOR}; opacity: 0.5; padding: 20px;")
            self._web_seq_container_layout.insertWidget(0, placeholder)
        # Force immediate layout recalculation to prevent cards from stacking
        self._web_seq_container_layout.activate()
        self._web_seq_container.updateGeometry()
        QTimer.singleShot(0, self._sync_web_seq_buttons)

    def _sync_web_seq_buttons(self):
        """Sync web sequence button widths."""
        if not hasattr(self, '_web_seq_scroll_area') or not self._web_seq_scroll_area:
            return
        viewport = self._web_seq_scroll_area.viewport()
        if not viewport:
            return
        target_width = max(0, viewport.width() - 30)
        for i in range(self._web_seq_container_layout.count()):
            item = self._web_seq_container_layout.itemAt(i)
            w = item.widget() if item else None
            if isinstance(w, DraggableWebSequenceFileButton):
                w.setFixedWidth(target_width)
                w.update_display_text()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._position_toggle)
        QTimer.singleShot(0, self._sync_chain_buttons)
        QTimer.singleShot(0, self._sync_sequence_buttons)
        QTimer.singleShot(0, self._sync_web_seq_buttons)
    
    def _switch_to_sequences_tab(self):
        """Switch to the Sequences tab."""
        self._active_coll_type = 'sequence'
        self._sequences_tab_btn.setChecked(True)
        self._chains_tab_btn.setChecked(False)
        self._context_tab_btn.setChecked(False)
        self._code_tab_btn.setChecked(False)
        self._llm_tab_btn.setChecked(False)
        self._web_seq_tab_btn.setChecked(False)
        self._sequences_scroll_area.setVisible(True)
        self._scroll_area.setVisible(False)
        self._context_scroll_area.setVisible(False)
        self._code_scroll_area.setVisible(False)
        self._llm_scroll_area.setVisible(False)
        self._web_seq_scroll_area.setVisible(False)
        self._collections_bar.setVisible(True)
        self._update_collections_combo()
        self._refresh_sequences_list()
        QTimer.singleShot(0, self._filter_active_tab)

    def _switch_to_chains_tab(self):
        """Switch to the Chains tab."""
        self._active_coll_type = 'chain'
        self._sequences_tab_btn.setChecked(False)
        self._chains_tab_btn.setChecked(True)
        self._context_tab_btn.setChecked(False)
        self._code_tab_btn.setChecked(False)
        self._llm_tab_btn.setChecked(False)
        self._web_seq_tab_btn.setChecked(False)
        self._sequences_scroll_area.setVisible(False)
        self._scroll_area.setVisible(True)
        self._context_scroll_area.setVisible(False)
        self._code_scroll_area.setVisible(False)
        self._llm_scroll_area.setVisible(False)
        self._web_seq_scroll_area.setVisible(False)
        self._collections_bar.setVisible(True)
        self._update_collections_combo()
        QTimer.singleShot(0, self._sync_chain_buttons)
        QTimer.singleShot(0, self._filter_active_tab)
    
    def _switch_to_context_tab(self):
        """Switch to the Context Nodes tab."""
        self._sequences_tab_btn.setChecked(False)
        self._chains_tab_btn.setChecked(False)
        self._context_tab_btn.setChecked(True)
        self._code_tab_btn.setChecked(False)
        self._llm_tab_btn.setChecked(False)
        self._web_seq_tab_btn.setChecked(False)
        self._sequences_scroll_area.setVisible(False)
        self._scroll_area.setVisible(False)
        self._context_scroll_area.setVisible(True)
        self._code_scroll_area.setVisible(False)
        self._llm_scroll_area.setVisible(False)
        self._web_seq_scroll_area.setVisible(False)
        self._collections_bar.setVisible(False)
        self._refresh_context_nodes_list()
        QTimer.singleShot(0, self._filter_active_tab)
    
    def _switch_to_code_tab(self):
        """Switch to the Code Nodes tab."""
        self._sequences_tab_btn.setChecked(False)
        self._chains_tab_btn.setChecked(False)
        self._context_tab_btn.setChecked(False)
        self._code_tab_btn.setChecked(True)
        self._llm_tab_btn.setChecked(False)
        self._web_seq_tab_btn.setChecked(False)
        self._sequences_scroll_area.setVisible(False)
        self._scroll_area.setVisible(False)
        self._context_scroll_area.setVisible(False)
        self._code_scroll_area.setVisible(True)
        self._llm_scroll_area.setVisible(False)
        self._web_seq_scroll_area.setVisible(False)
        self._collections_bar.setVisible(False)
        self._refresh_code_nodes_list()
        QTimer.singleShot(0, self._filter_active_tab)
    
    def _switch_to_llm_tab(self):
        """Switch to the LLM Nodes tab."""
        self._sequences_tab_btn.setChecked(False)
        self._chains_tab_btn.setChecked(False)
        self._context_tab_btn.setChecked(False)
        self._code_tab_btn.setChecked(False)
        self._llm_tab_btn.setChecked(True)
        self._web_seq_tab_btn.setChecked(False)
        self._sequences_scroll_area.setVisible(False)
        self._scroll_area.setVisible(False)
        self._context_scroll_area.setVisible(False)
        self._code_scroll_area.setVisible(False)
        self._llm_scroll_area.setVisible(True)
        self._web_seq_scroll_area.setVisible(False)
        self._collections_bar.setVisible(False)
        self._refresh_llm_nodes_list()
        QTimer.singleShot(0, self._filter_active_tab)

    def _switch_to_web_seq_tab(self):
        """Switch to the Web Sequences tab."""
        self._sequences_tab_btn.setChecked(False)
        self._chains_tab_btn.setChecked(False)
        self._context_tab_btn.setChecked(False)
        self._code_tab_btn.setChecked(False)
        self._llm_tab_btn.setChecked(False)
        self._web_seq_tab_btn.setChecked(True)
        self._sequences_scroll_area.setVisible(False)
        self._scroll_area.setVisible(False)
        self._context_scroll_area.setVisible(False)
        self._code_scroll_area.setVisible(False)
        self._llm_scroll_area.setVisible(False)
        self._web_seq_scroll_area.setVisible(True)
        self._collections_bar.setVisible(False)
        self._refresh_web_seq_list()
        QTimer.singleShot(0, self._filter_active_tab)

    def _filter_active_tab(self):
        """Filter buttons in the currently active tab by the search text."""
        try:
            text = self._search_field.text().strip().lower()
            # Determine which container is active
            if self._sequences_scroll_area.isVisible():
                container_layout = self._sequences_container_layout
            elif self._scroll_area.isVisible():
                container_layout = self._container_layout
            elif self._context_scroll_area.isVisible():
                container_layout = self._context_container_layout
            elif self._code_scroll_area.isVisible():
                container_layout = self._code_container_layout
            elif self._llm_scroll_area.isVisible():
                container_layout = self._llm_container_layout
            elif self._web_seq_scroll_area.isVisible():
                container_layout = self._web_seq_container_layout
            else:
                return

            has_visible = False
            for i in range(container_layout.count()):
                try:
                    item = container_layout.itemAt(i)
                    w = item.widget() if item else None
                    if w is None:
                        continue
                    # Skip stretch items and placeholder labels
                    from PyQt5.QtWidgets import QLabel
                    if isinstance(w, QLabel):
                        w.setVisible(not bool(text))
                        if not text:
                            has_visible = True
                        continue
                    if not text:
                        w.setVisible(True)
                        has_visible = True
                    else:
                        btn_text = str(w.text()).lower() if hasattr(w, 'text') else ''
                        tooltip = str(w.toolTip()).lower() if hasattr(w, 'toolTip') else ''
                        match = text in btn_text or text in tooltip
                        w.setVisible(match)
                        if match:
                            has_visible = True
                except RuntimeError:
                    # A widget scheduled for deleteLater() can already be gone
                    # here; skip it instead of aborting the whole filter.
                    continue

            # Show/hide the "no results" hint when filtering yields nothing
            if text:
                # Find or create a no-results label
                no_results = getattr(self, '_no_results_label', None)
                if not has_visible:
                    if no_results is None:
                        from PyQt5.QtWidgets import QLabel
                        no_results = QLabel(_("No matching nodes"))
                        no_results.setAlignment(Qt.AlignCenter)
                        no_results.setStyleSheet(f"color: {TEXT_COLOR}; opacity: 0.5; padding: 20px;")
                        no_results.setWordWrap(True)
                        self._no_results_label = no_results
                    no_results.setVisible(True)
                    # Insert at the top of the container if not already added
                    parent_layout = no_results.parent().layout() if no_results.parent() else None
                    if parent_layout != container_layout:
                        if no_results.parent():
                            no_results.setParent(None)
                        container_layout.insertWidget(0, no_results)
                else:
                    if no_results is not None:
                        no_results.setVisible(False)
        except Exception as e:
            logger.error(f"Error filtering nodes: {e}")
    
    def _refresh_context_nodes_list(self):
        """Scan all chains and collect context nodes."""
        # Clear existing buttons (except stretch)
        while self._context_container_layout.count() > 1:
            item = self._context_container_layout.takeAt(0)
            if item.widget():
                item.widget().setVisible(False)
                item.widget().deleteLater()
        
        folder = getattr(self.graph_view, 'chains_folder', None)
        if folder and os.path.isdir(folder):
            try:
                files = sorted([f for f in os.listdir(folder) if f.lower().endswith('.json')])
                
                context_nodes_found = 0
                
                for chain_file in files:
                    chain_path = os.path.join(folder, chain_file)
                    try:
                        with open(chain_path, 'r', encoding='utf-8') as f:
                            chain_data = json.load(f)
                        
                        # Extract context nodes from this chain
                        context_nodes = chain_data.get('context_nodes', [])
                        
                        for ctx_node in context_nodes:
                            node_id = ctx_node.get('node_id', 'unknown')
                            context_label = ctx_node.get('label', '') or ctx_node.get('context_node_label', '')
                            
                            # Display label: chain_name / context_label (or node_id)
                            display_name = f"{os.path.splitext(chain_file)[0]}"
                            if context_label:
                                display_name += f" / {context_label}"
                            else:
                                display_name += f" / {node_id[:8]}"
                            
                            btn = DraggableContextNodeButton(
                                display_name,
                                chain_path,
                                ctx_node,
                                self.graph_view,
                                self
                            )
                            self._context_container_layout.insertWidget(self._context_container_layout.count() - 1, btn)
                            context_nodes_found += 1
                            
                    except Exception as e:
                        logger.error(f"Error reading chain {chain_file}: {e}")
                
                if context_nodes_found == 0:
                    placeholder = QLabel(_("No context nodes found"))
                    placeholder.setAlignment(Qt.AlignCenter)
                    placeholder.setStyleSheet(f"color: {TEXT_COLOR}; opacity: 0.5; padding: 20px;")
                    placeholder.setWordWrap(True)
                    self._context_container_layout.insertWidget(0, placeholder)
                    
            except Exception as e:
                logger.error(f"Error listing context nodes: {e}")
        
        QTimer.singleShot(0, self._sync_context_node_buttons)
    
    def _sync_context_node_buttons(self):
        """Sync context node button widths."""
        if not hasattr(self, '_context_scroll_area') or not self._context_scroll_area:
            return
        viewport = self._context_scroll_area.viewport()
        if not viewport:
            return
        target_width = max(0, viewport.width() - 30)
        for i in range(self._context_container_layout.count()):
            item = self._context_container_layout.itemAt(i)
            w = item.widget() if item else None
            if isinstance(w, DraggableContextNodeButton):
                w.setFixedWidth(target_width)
                w.update_display_text()

    def _refresh_code_nodes_list(self):
        """Scan all chains and collect code nodes."""
        # Clear existing buttons (except stretch)
        while self._code_container_layout.count() > 1:
            item = self._code_container_layout.takeAt(0)
            if item.widget():
                item.widget().setVisible(False)
                item.widget().deleteLater()

        folder = getattr(self.graph_view, 'chains_folder', None)
        if folder and os.path.isdir(folder):
            try:
                files = sorted([f for f in os.listdir(folder) if f.lower().endswith('.json')])

                code_nodes_found = 0

                for chain_file in files:
                    chain_path = os.path.join(folder, chain_file)
                    try:
                        with open(chain_path, 'r', encoding='utf-8') as f:
                            chain_data = json.load(f)

                        # Extract code nodes from this chain
                        code_nodes = chain_data.get('code_nodes', [])

                        for code_node in code_nodes:
                            node_id = code_node.get('node_id', 'unknown')
                            code = code_node.get('code', '')
                            description = code_node.get('description', '')
                            file_path = code_node.get('file_path', '')

                            # Build display label
                            chain_name = os.path.splitext(chain_file)[0]

                            if description:
                                display_name = f"{chain_name} / {description}"
                            elif file_path:
                                display_name = f"{chain_name} / {os.path.basename(file_path)}"
                            else:
                                # Use first 25 chars of code as preview
                                code_preview = code.replace('\n', ' ').strip()[:25]
                                display_name = f"{chain_name} / {code_preview}" if code_preview else f"{chain_name} / {node_id[:8]}"

                            btn = DraggableCodeNodeButton(
                                display_name,
                                chain_path,
                                code_node,
                                self.graph_view,
                                self
                            )
                            self._code_container_layout.insertWidget(self._code_container_layout.count() - 1, btn)
                            code_nodes_found += 1

                    except Exception as e:
                        logger.error(f"Error reading chain {chain_file}: {e}")

                if code_nodes_found == 0:
                    placeholder = QLabel(_("No code nodes found"))
                    placeholder.setAlignment(Qt.AlignCenter)
                    placeholder.setStyleSheet(f"color: {TEXT_COLOR}; opacity: 0.5; padding: 20px;")
                    placeholder.setWordWrap(True)
                    self._code_container_layout.insertWidget(0, placeholder)

            except Exception as e:
                logger.error(f"Error listing code nodes: {e}")

        QTimer.singleShot(0, self._sync_code_node_buttons)

    def _sync_code_node_buttons(self):
        """Sync code node button widths."""
        if not hasattr(self, '_code_scroll_area') or not self._code_scroll_area:
            return
        viewport = self._code_scroll_area.viewport()
        if not viewport:
            return
        target_width = max(0, viewport.width() - 30)
        for i in range(self._code_container_layout.count()):
            item = self._code_container_layout.itemAt(i)
            w = item.widget() if item else None
            if isinstance(w, DraggableCodeNodeButton):
                w.setFixedWidth(target_width)
                w.update_display_text()

    def _refresh_llm_nodes_list(self):
        """Scan all chains and collect LLM nodes."""
        # Clear existing buttons (except stretch)
        while self._llm_container_layout.count() > 1:
            item = self._llm_container_layout.takeAt(0)
            if item.widget():
                item.widget().setVisible(False)
                item.widget().deleteLater()

        folder = getattr(self.graph_view, 'chains_folder', None)
        if folder and os.path.isdir(folder):
            try:
                files = sorted([f for f in os.listdir(folder) if f.lower().endswith('.json')])

                llm_nodes_found = 0

                for chain_file in files:
                    chain_path = os.path.join(folder, chain_file)
                    try:
                        with open(chain_path, 'r', encoding='utf-8') as f:
                            chain_data = json.load(f)

                        # Extract LLM nodes from this chain
                        llm_nodes = chain_data.get('llm_nodes', [])

                        for llm_node in llm_nodes:
                            node_id = llm_node.get('node_id', 'unknown')
                            semantic_desc = llm_node.get('semantic_description', '')
                            model = llm_node.get('model', 'llama3.2:latest')
                            prompt = llm_node.get('prompt', '')

                            # Build display label
                            chain_name = os.path.splitext(chain_file)[0]

                            if semantic_desc:
                                display_name = f"{chain_name} / {semantic_desc}"
                            else:
                                # Use model name or first 25 chars of prompt
                                prompt_preview = prompt.replace('\n', ' ').strip()[:25]
                                display_name = f"{chain_name} / {model}" if not prompt_preview else f"{chain_name} / {prompt_preview}"

                            btn = DraggableLLMNodeButton(
                                display_name,
                                chain_path,
                                llm_node,
                                self.graph_view,
                                self
                            )
                            self._llm_container_layout.insertWidget(self._llm_container_layout.count() - 1, btn)
                            llm_nodes_found += 1

                    except Exception as e:
                        logger.error(f"Error reading chain {chain_file}: {e}")

                if llm_nodes_found == 0:
                    placeholder = QLabel(_("No LLM nodes found"))
                    placeholder.setAlignment(Qt.AlignCenter)
                    placeholder.setStyleSheet(f"color: {TEXT_COLOR}; opacity: 0.5; padding: 20px;")
                    placeholder.setWordWrap(True)
                    self._llm_container_layout.insertWidget(0, placeholder)

            except Exception as e:
                logger.error(f"Error listing LLM nodes: {e}")

        QTimer.singleShot(0, self._sync_llm_node_buttons)

    def _sync_llm_node_buttons(self):
        """Sync LLM node button widths."""
        if not hasattr(self, '_llm_scroll_area') or not self._llm_scroll_area:
            return
        viewport = self._llm_scroll_area.viewport()
        if not viewport:
            return
        target_width = max(0, viewport.width() - 30)
        for i in range(self._llm_container_layout.count()):
            item = self._llm_container_layout.itemAt(i)
            w = item.widget() if item else None
            if isinstance(w, DraggableLLMNodeButton):
                w.setFixedWidth(target_width)
                w.update_display_text()
