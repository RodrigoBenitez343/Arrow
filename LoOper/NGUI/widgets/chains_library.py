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
    BLOCK_COLOR, BLOCK_HOVER, CHAIN_IMPORT_COLOR, CONTEXT_NODE_COLOR
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

class ChainsLibrary(QWidget):
    """Collapsible left panel containing a library of chains with tabs for context nodes."""
    
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
        self.setMinimumWidth(160)
        self.setMaximumWidth(160)
        
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(4, 0, 0, 0)
        main_layout.setSpacing(0)
        self.setAttribute(Qt.WA_TranslucentBackground)

        # Content frame
        self._content_frame = QFrame(self)
        self._content_frame.setObjectName("chainsContent")
        self._content_frame.setStyleSheet(
            f"QFrame#chainsContent {{ background-color: {DARK_GREY}; border: 2px solid {LIGHT_GREY}; border-radius: 10px; }}"
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
                background-color: {BLOCK_COLOR};
                color: {TEXT_COLOR};
                border: 1px solid {LIGHT_GREY};
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
                background-color: {BLOCK_COLOR};
                color: {TEXT_COLOR};
                border: 1px solid {LIGHT_GREY};
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
                border-color: {ACCENT_COLOR};
            }}
            """
        )
        self._context_tab_btn.clicked.connect(self._switch_to_context_tab)
        
        tab_buttons_layout.addWidget(self._chains_tab_btn)
        tab_buttons_layout.addWidget(self._context_tab_btn)
        content_layout.addLayout(tab_buttons_layout)

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
        
        # Initial list refresh
        self.refresh_chains_list()
        self._refresh_context_nodes_list()
        
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
            self.setMinimumWidth(160)
            self.setMaximumWidth(160)
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
        target_width = max(0, viewport.width() - 2)
        for i in range(self._container_layout.count()):
            item = self._container_layout.itemAt(i)
            w = item.widget() if item else None
            if isinstance(w, DraggableChainButton):
                w.setFixedWidth(target_width)
                w.update_display_text()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._position_toggle)
        QTimer.singleShot(0, self._sync_chain_buttons)
    
    def _switch_to_chains_tab(self):
        """Switch to the Chains tab."""
        self._chains_tab_btn.setChecked(True)
        self._context_tab_btn.setChecked(False)
        self._scroll_area.setVisible(True)
        self._context_scroll_area.setVisible(False)
    
    def _switch_to_context_tab(self):
        """Switch to the Context Nodes tab."""
        self._chains_tab_btn.setChecked(False)
        self._context_tab_btn.setChecked(True)
        self._scroll_area.setVisible(False)
        self._context_scroll_area.setVisible(True)
        self._refresh_context_nodes_list()
    
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
        target_width = max(0, viewport.width() - 2)
        for i in range(self._context_container_layout.count()):
            item = self._context_container_layout.itemAt(i)
            w = item.widget() if item else None
            if isinstance(w, DraggableContextNodeButton):
                w.setFixedWidth(target_width)
                w.update_display_text()
