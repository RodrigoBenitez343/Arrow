import logging
import os
import json
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QToolButton,
    QFrame, QSizePolicy, QLabel, QApplication, QScrollArea,
    QGraphicsDropShadowEffect, QStyleOptionButton, QStyle,
    QMenu, QAction, QInputDialog, QLineEdit
)
from PyQt5.QtCore import Qt, QPoint, QSize, QFileSystemWatcher, QTimer, QMimeData
from PyQt5.QtGui import QDrag, QPixmap, QPainter, QColor, QIcon

from ..constants import (
    DARK_GREY, LIGHT_GREY, MEDIUM_GREY, TEXT_COLOR, ACCENT_COLOR,
    BLOCK_COLOR, BLOCK_HOVER, CHAIN_IMPORT_COLOR, CONTEXT_NODE_COLOR,
    CODE_NODE_COLOR, LLM_COLOR, SIDE_PANEL_BG
)
from .collapsible_toolbar import get_node_icon, load_tabler_icon
from ..i18n import _

logger = logging.getLogger(__name__)

class DraggableChainButton(QPushButton):
    """Green card-style button representing a chain, supporting drag-to-add."""
    
    def __init__(self, label: str, file_path: str, graph_view, parent=None):
        # Remove .json from label for display
        self._full_text = os.path.splitext(label)[0]
        super().__init__(self._full_text, parent)
        self.file_path = file_path
        self.graph_view = graph_view
        self._drag_start_pos = None
        self.setCursor(Qt.OpenHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        
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
        
        # Set icon (chain_import icon)
        icon = get_node_icon('chain_import', 24)
        if icon:
            self.setIcon(icon)
            self.setIconSize(QSize(24, 24))
            
        self.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {CHAIN_IMPORT_COLOR};
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
                background-color: {CHAIN_IMPORT_COLOR};
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
        self.setText(fm.elidedText(self._full_text, Qt.ElideRight, max(0, text_rect.width())))

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        
        # Add Description action
        edit_desc_action = QAction(_("Edit Description"), self)
        edit_desc_action.triggered.connect(self._edit_description)
        menu.addAction(edit_desc_action)
        
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
        self._drag_start_pos = None
        self.setCursor(Qt.OpenHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        
        # Build tooltip with context node info
        scope = context_node_data.get('scope', 'chain')
        node_id = context_node_data.get('node_id', 'unknown')
        context_label = context_node_data.get('context_node_label', '')
        
        tooltip = f"Context Node: {label}\n"
        tooltip += f"Chain: {os.path.basename(chain_file)}\n"
        tooltip += f"Scope: {scope}\n"
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

class DraggableCodeNodeButton(QPushButton):
    """Gold card-style button representing a code node from another chain, supporting drag-to-add."""

    def __init__(self, label: str, chain_file: str, code_node_data: dict, graph_view, parent=None):
        super().__init__(label, parent)
        self.chain_file = chain_file
        self.code_node_data = code_node_data
        self.graph_view = graph_view
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

        edit_desc_action = QAction(_("Edit Description"), self)
        edit_desc_action.triggered.connect(self._edit_description)
        menu.addAction(edit_desc_action)

        menu.exec_(event.globalPos())

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

        edit_desc_action = QAction(_("Edit Description"), self)
        edit_desc_action.triggered.connect(self._edit_description)
        menu.addAction(edit_desc_action)

        menu.exec_(event.globalPos())

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


class DraggableSequenceFileButton(QPushButton):
    """Card-style button representing a sequence file, supporting drag-to-add and description editing."""

    def __init__(self, label: str, file_path: str, graph_view, parent=None):
        self._full_text = os.path.splitext(label)[0]
        super().__init__(self._full_text, parent)
        self.file_path = file_path
        self.graph_view = graph_view
        self._drag_start_pos = None
        self.setCursor(Qt.OpenHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

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

        self.setStyleSheet(
            f"""
            QPushButton {{
                background-color: rgba(255, 255, 255, 0.04);
                color: {TEXT_COLOR};
                border: 1px solid rgba(255, 255, 255, 0.08);
                padding: 8px 12px;
                border-radius: 12px;
                min-height: 44px;
                font-weight: 600;
                text-align: left;
            }}
            QPushButton:hover {{
                border-color: {ACCENT_COLOR};
                background-color: rgba(255, 255, 255, 0.08);
            }}
            QPushButton:pressed {{
                background-color: rgba(255, 255, 255, 0.12);
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
        self.setText(fm.elidedText(self._full_text, Qt.ElideRight, max(0, text_rect.width())))

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        edit_desc_action = QAction(_("Edit Description"), self)
        edit_desc_action.triggered.connect(self._edit_description)
        menu.addAction(edit_desc_action)
        menu.exec_(event.globalPos())

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


class ChainsLibrary(QWidget):
    """Collapsible left panel containing a library of chains with tabs for context and code nodes."""
    
    def __init__(self, graph_view, parent=None):
        super().__init__(parent)
        self.graph_view = graph_view
        self._collapsed = False
        
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
        
        # Chains tab button
        self._chains_tab_btn = QPushButton(_("Chains"))
        self._chains_tab_btn.setFixedHeight(32)
        self._chains_tab_btn.setCheckable(True)
        self._chains_tab_btn.setChecked(True)
        self._chains_tab_btn.setStyleSheet(
            f"""
            QPushButton {{
                background-color: rgba(255, 255, 255, 0.04);
                color: {TEXT_COLOR};
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
                font-weight: 600;
                font-size: 12px;
            }}
            QPushButton:checked {{
                background-color: {CHAIN_IMPORT_COLOR};
                color: #000000;
                border-color: {ACCENT_COLOR};
            }}
            QPushButton:hover {{
                background-color: rgba(255, 255, 255, 0.08);
                border-color: {ACCENT_COLOR};
            }}
            """
        )
        self._chains_tab_btn.clicked.connect(self._switch_to_chains_tab)
        
        # Context Nodes tab button
        self._context_tab_btn = QPushButton(_("Context"))
        self._context_tab_btn.setFixedHeight(32)
        self._context_tab_btn.setCheckable(True)
        self._context_tab_btn.setChecked(False)
        self._context_tab_btn.setStyleSheet(
            f"""
            QPushButton {{
                background-color: rgba(255, 255, 255, 0.04);
                color: {TEXT_COLOR};
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
                font-weight: 600;
                font-size: 12px;
            }}
            QPushButton:checked {{
                background-color: {CONTEXT_NODE_COLOR};
                color: #000000;
                border-color: {ACCENT_COLOR};
            }}
            QPushButton:hover {{
                background-color: rgba(255, 255, 255, 0.08);
                border-color: {ACCENT_COLOR};
            }}
            """
        )
        self._context_tab_btn.clicked.connect(self._switch_to_context_tab)
        
        # Code Nodes tab button
        self._code_tab_btn = QPushButton(_("Code"))
        self._code_tab_btn.setFixedHeight(32)
        self._code_tab_btn.setCheckable(True)
        self._code_tab_btn.setChecked(False)
        self._code_tab_btn.setStyleSheet(
            f"""
            QPushButton {{
                background-color: rgba(255, 255, 255, 0.04);
                color: {TEXT_COLOR};
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
                font-weight: 600;
                font-size: 12px;
            }}
            QPushButton:checked {{
                background-color: {CODE_NODE_COLOR};
                color: #000000;
                border-color: {ACCENT_COLOR};
            }}
            QPushButton:hover {{
                background-color: rgba(255, 255, 255, 0.08);
                border-color: {ACCENT_COLOR};
            }}
            """
        )
        self._code_tab_btn.clicked.connect(self._switch_to_code_tab)
        
        # LLM Nodes tab button
        self._llm_tab_btn = QPushButton(_("LLM"))
        self._llm_tab_btn.setFixedHeight(32)
        self._llm_tab_btn.setCheckable(True)
        self._llm_tab_btn.setChecked(False)
        self._llm_tab_btn.setStyleSheet(
            f"""
            QPushButton {{
                background-color: rgba(255, 255, 255, 0.04);
                color: {TEXT_COLOR};
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
                font-weight: 600;
                font-size: 12px;
            }}
            QPushButton:checked {{
                background-color: {LLM_COLOR};
                color: #000000;
                border-color: {ACCENT_COLOR};
            }}
            QPushButton:hover {{
                background-color: rgba(255, 255, 255, 0.08);
                border-color: {ACCENT_COLOR};
            }}
            """
        )
        self._llm_tab_btn.clicked.connect(self._switch_to_llm_tab)
        
        # Sequences tab button
        self._sequences_tab_btn = QPushButton(_("Sequences"))
        self._sequences_tab_btn.setFixedHeight(32)
        self._sequences_tab_btn.setCheckable(True)
        self._sequences_tab_btn.setChecked(False)
        self._sequences_tab_btn.setStyleSheet(
            f"""
            QPushButton {{
                background-color: rgba(255, 255, 255, 0.04);
                color: {TEXT_COLOR};
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
                font-weight: 600;
                font-size: 12px;
            }}
            QPushButton:checked {{
                background-color: rgba(255, 255, 255, 0.04);
                color: {TEXT_COLOR};
                border-color: {ACCENT_COLOR};
            }}
            QPushButton:hover {{
                background-color: rgba(255, 255, 255, 0.08);
                border-color: {ACCENT_COLOR};
            }}
            """
        )
        self._sequences_tab_btn.clicked.connect(self._switch_to_sequences_tab)

        tab_buttons_layout.addWidget(self._sequences_tab_btn)
        tab_buttons_layout.addWidget(self._chains_tab_btn)
        tab_buttons_layout.addWidget(self._llm_tab_btn)
        tab_buttons_layout.addWidget(self._context_tab_btn)
        tab_buttons_layout.addWidget(self._code_tab_btn)
        content_layout.addLayout(tab_buttons_layout)

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

        # Search bar for filtering nodes in the active tab
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
        
        main_layout.addWidget(self._content_frame)

        # Toggle button
        self._toggle_btn = QToolButton(self.graph_view)
        ico = load_tabler_icon('CHEVRON_LEFT', 14)
        if ico:
            self._toggle_btn.setIcon(ico)
        else:
            self._toggle_btn.setText("❮")
        self._toggle_btn.setFixedSize(28, 28)
        self._toggle_btn.setCursor(Qt.PointingHandCursor)
        self._toggle_btn.setAutoRaise(True)
        self._toggle_btn.setStyleSheet(
            f"QToolButton {{ background-color: rgba(32,32,32,180); border: 1px solid {LIGHT_GREY}; border-radius: 14px; }}"
            f"QToolButton:hover {{ border-color: {ACCENT_COLOR}; }}"
        )
        self._toggle_btn.clicked.connect(self._toggle_panel)
        
        # Initial list refresh (start with sequences visible by default)
        self._switch_to_sequences_tab()
        self._refresh_sequences_list()
        self.refresh_chains_list()
        self._refresh_context_nodes_list()
        self._refresh_code_nodes_list()
        self._refresh_llm_nodes_list()
        
        # Position toggle button
        QTimer.singleShot(100, self._position_toggle)

    def _toggle_panel(self):
        self._collapsed = not self._collapsed
        if self._collapsed:
            self._content_frame.setVisible(False)
            self.setMinimumWidth(0)
            self.setMaximumWidth(0)
            ico = load_tabler_icon('CHEVRON_RIGHT', 14)
            if ico: self._toggle_btn.setIcon(ico)
        else:
            self._content_frame.setVisible(True)
            self.setMinimumWidth(320)
            self.setMaximumWidth(320)
            ico = load_tabler_icon('CHEVRON_LEFT', 14)
            if ico: self._toggle_btn.setIcon(ico)
        
        self._position_toggle()

    def _position_toggle(self):
        if not self._toggle_btn: return
        pos = self.mapTo(self.graph_view, QPoint(0, 0))
        # For left panel, toggle is on the right side of the panel
        x = pos.x() + self.width() - 2
        y = pos.y() + (self.height() // 2) - 14
        self._toggle_btn.move(x, y)
        self._toggle_btn.raise_()

    def refresh_sequences_list(self, path=None):
        """Public method to refresh the sequences list in the Sequences tab."""
        if self._sequences_scroll_area.isVisible():
            self._refresh_sequences_list()
        else:
            # Still rebuild behind the scenes
            self._refresh_sequences_list()

    def refresh_chains_list(self, path=None):
        # Clear existing buttons (except stretch)
        while self._container_layout.count() > 1:
            item = self._container_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        folder = getattr(self.graph_view, 'chains_folder', None)
        if folder and os.path.isdir(folder):
            try:
                files = sorted([f for f in os.listdir(folder) if f.lower().endswith('.json')])
                for name in files:
                    full_path = os.path.join(folder, name)
                    btn = DraggableChainButton(name, full_path, self.graph_view, self)
                    self._container_layout.insertWidget(self._container_layout.count() - 1, btn)
            except Exception as e:
                logger.error(f"Error listing chains: {e}")
        
        if self._container_layout.count() == 1: # Only stretch
            placeholder = QLabel(_("No chains"))
            placeholder.setAlignment(Qt.AlignCenter)
            placeholder.setStyleSheet(f"color: {TEXT_COLOR}; opacity: 0.5; padding: 20px;")
            self._container_layout.insertWidget(0, placeholder)
        QTimer.singleShot(0, self._sync_chain_buttons)

    def _sync_chain_buttons(self):
        if not hasattr(self, '_scroll_area') or not self._scroll_area:
            return
        viewport = self._scroll_area.viewport()
        if not viewport:
            return
        target_width = max(0, viewport.width() - 10)
        for i in range(self._container_layout.count()):
            item = self._container_layout.itemAt(i)
            w = item.widget() if item else None
            if isinstance(w, DraggableChainButton):
                w.setFixedWidth(target_width)
                w.update_display_text()

    def _refresh_sequences_list(self, path=None):
        """Refresh the list of sequence files in the Sequences tab."""
        # Clear existing buttons (except stretch)
        while self._sequences_container_layout.count() > 1:
            item = self._sequences_container_layout.takeAt(0)
            if item.widget():
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
                    # Check if it's actually a chain file (has chain structure with 'sequences' key being a list)
                    # Show all .json files from the sequences folders
                    btn = DraggableSequenceFileButton(name, full_path, self.graph_view, self)
                    self._sequences_container_layout.insertWidget(self._sequences_container_layout.count() - 1, btn)
            except Exception as e:
                logger.error(f"Error listing sequences: {e}")

        if self._sequences_container_layout.count() == 1:  # Only stretch
            placeholder = QLabel(_("No sequences"))
            placeholder.setAlignment(Qt.AlignCenter)
            placeholder.setStyleSheet(f"color: {TEXT_COLOR}; opacity: 0.5; padding: 20px;")
            self._sequences_container_layout.insertWidget(0, placeholder)
        QTimer.singleShot(0, self._sync_sequence_buttons)

    def _sync_sequence_buttons(self):
        """Sync sequence button widths."""
        if not hasattr(self, '_sequences_scroll_area') or not self._sequences_scroll_area:
            return
        viewport = self._sequences_scroll_area.viewport()
        if not viewport:
            return
        target_width = max(0, viewport.width() - 10)
        for i in range(self._sequences_container_layout.count()):
            item = self._sequences_container_layout.itemAt(i)
            w = item.widget() if item else None
            if isinstance(w, DraggableSequenceFileButton):
                w.setFixedWidth(target_width)
                w.update_display_text()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._position_toggle)
        QTimer.singleShot(0, self._sync_chain_buttons)
        QTimer.singleShot(0, self._sync_sequence_buttons)
    
    def _switch_to_sequences_tab(self):
        """Switch to the Sequences tab."""
        self._sequences_tab_btn.setChecked(True)
        self._chains_tab_btn.setChecked(False)
        self._context_tab_btn.setChecked(False)
        self._code_tab_btn.setChecked(False)
        self._llm_tab_btn.setChecked(False)
        self._sequences_scroll_area.setVisible(True)
        self._scroll_area.setVisible(False)
        self._context_scroll_area.setVisible(False)
        self._code_scroll_area.setVisible(False)
        self._llm_scroll_area.setVisible(False)
        self._refresh_sequences_list()
        QTimer.singleShot(0, self._filter_active_tab)

    def _switch_to_chains_tab(self):
        """Switch to the Chains tab."""
        self._sequences_tab_btn.setChecked(False)
        self._chains_tab_btn.setChecked(True)
        self._context_tab_btn.setChecked(False)
        self._code_tab_btn.setChecked(False)
        self._llm_tab_btn.setChecked(False)
        self._sequences_scroll_area.setVisible(False)
        self._scroll_area.setVisible(True)
        self._context_scroll_area.setVisible(False)
        self._code_scroll_area.setVisible(False)
        self._llm_scroll_area.setVisible(False)
        QTimer.singleShot(0, self._sync_chain_buttons)
        QTimer.singleShot(0, self._filter_active_tab)
    
    def _switch_to_context_tab(self):
        """Switch to the Context Nodes tab."""
        self._sequences_tab_btn.setChecked(False)
        self._chains_tab_btn.setChecked(False)
        self._context_tab_btn.setChecked(True)
        self._code_tab_btn.setChecked(False)
        self._llm_tab_btn.setChecked(False)
        self._sequences_scroll_area.setVisible(False)
        self._scroll_area.setVisible(False)
        self._context_scroll_area.setVisible(True)
        self._code_scroll_area.setVisible(False)
        self._llm_scroll_area.setVisible(False)
        self._refresh_context_nodes_list()
        QTimer.singleShot(0, self._filter_active_tab)
    
    def _switch_to_code_tab(self):
        """Switch to the Code Nodes tab."""
        self._sequences_tab_btn.setChecked(False)
        self._chains_tab_btn.setChecked(False)
        self._context_tab_btn.setChecked(False)
        self._code_tab_btn.setChecked(True)
        self._llm_tab_btn.setChecked(False)
        self._sequences_scroll_area.setVisible(False)
        self._scroll_area.setVisible(False)
        self._context_scroll_area.setVisible(False)
        self._code_scroll_area.setVisible(True)
        self._llm_scroll_area.setVisible(False)
        self._refresh_code_nodes_list()
        QTimer.singleShot(0, self._filter_active_tab)
    
    def _switch_to_llm_tab(self):
        """Switch to the LLM Nodes tab."""
        self._sequences_tab_btn.setChecked(False)
        self._chains_tab_btn.setChecked(False)
        self._context_tab_btn.setChecked(False)
        self._code_tab_btn.setChecked(False)
        self._llm_tab_btn.setChecked(True)
        self._sequences_scroll_area.setVisible(False)
        self._scroll_area.setVisible(False)
        self._context_scroll_area.setVisible(False)
        self._code_scroll_area.setVisible(False)
        self._llm_scroll_area.setVisible(True)
        self._refresh_llm_nodes_list()
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
            else:
                return

            has_visible = False
            for i in range(container_layout.count()):
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
                            context_label = ctx_node.get('context_node_label', '')
                            scope = ctx_node.get('scope', 'chain')
                            
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
        target_width = max(0, viewport.width() - 10)
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
        target_width = max(0, viewport.width() - 10)
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
        target_width = max(0, viewport.width() - 10)
        for i in range(self._llm_container_layout.count()):
            item = self._llm_container_layout.itemAt(i)
            w = item.widget() if item else None
            if isinstance(w, DraggableLLMNodeButton):
                w.setFixedWidth(target_width)
                w.update_display_text()
