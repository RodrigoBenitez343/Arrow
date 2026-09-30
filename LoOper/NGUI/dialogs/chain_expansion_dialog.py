# dialogs/chain_expansion_dialog.py

from .base_dialog import ModernDialog
import os
import json
from PyQt5.QtWidgets import (QVBoxLayout, QHBoxLayout, QLabel, 
                             QDialogButtonBox, QPushButton, QMessageBox, QStyle)
from PyQt5.QtCore import Qt, QSize
from PyQt5.QtGui import QIcon, QPixmap, QPainter, QColor
from ..i18n import _
from ..graph_elements.config_manager import ConfigManager
from ..constants import TEXT_COLOR

# Import cache invalidation function
try:
    from ...player.json_cache import invalidate_file, clear_cache
except ImportError:
    # Fallback if import fails
    def invalidate_file(file_path):
        pass
    def clear_cache():
        pass

class ChainExpansionDialog(ModernDialog):
    """Dialog for expanding and editing imported chains in a separate graph view"""
    
    def __init__(self, chain_import_node, parent_graph_view, parent=None):
        super().__init__(parent, title=_("Edit Imported Chain"), help_topic="chain-import-dialog")
        self.chain_import_node = chain_import_node
        self.parent_graph_view = parent_graph_view
        self.config_manager = None  # Will be initialized after graph_view is created
        
        self.setModal(True)
        self.resize(1000, 700)
        
        self.setup_ui()
        self.load_chain_config()
        # Load the chain into the graph view after config is loaded
        self.load_chain_into_graph()
    
    def load_chain_config(self):
        """Load the chain configuration from the import node"""
        try:
            print("DEBUG: load_chain_config called")
            import_config = self.chain_import_node.get_chain_import_config()
            print(f"DEBUG: import_config: {import_config}")
            chain_file = import_config.get('chain_file', '')
            print(f"DEBUG: chain_file: {chain_file}")
            
            if not chain_file or not os.path.exists(chain_file):
                print(f"DEBUG: Chain file not found or not configured. File: {chain_file}, Exists: {os.path.exists(chain_file) if chain_file else False}")
                QMessageBox.warning(self, _("Warning"), _("Chain file not found or not configured."))
                return
            
            print(f"DEBUG: Loading chain file: {chain_file}")
            with open(chain_file, 'r', encoding='utf-8') as f:
                self.chain_config = json.load(f)
            print(f"DEBUG: Chain config loaded successfully. Keys: {list(self.chain_config.keys()) if isinstance(self.chain_config, dict) else 'not a dict'}")
                
        except Exception as e:
            print(f"DEBUG: Exception in load_chain_config: {str(e)}")
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, _("Error"), _("Failed to load chain configuration: {error}").format(error=str(e)))
    
    def setup_ui(self):
        """Setup the dialog UI"""
        # Use content_layout from ModernDialog
        layout = self.content_layout
        
        # Header
        header_layout = QHBoxLayout()
        chain_name = self.chain_import_node.name()
        header_label = QLabel(_("Editing Chain: {chain_name}").format(chain_name=chain_name))
        header_label.setStyleSheet(f"color: {TEXT_COLOR}; font-size: 14px; font-weight: bold;")
        header_layout.addWidget(header_label)
        header_layout.addStretch()
        
        # Add maximize / restore button using standard window icons
        self.maximize_button = QPushButton(self)
        self.maximize_button.setFixedSize(30, 30)
        self.maximize_button.setToolTip(_("Maximize/Restore"))
        self.maximize_button.setIcon(self._max_icon(16))
        self.maximize_button.setIconSize(QSize(16, 16))
        self.maximize_button.clicked.connect(self.toggle_maximize)
        header_layout.addWidget(self.maximize_button)
        
        layout.addLayout(header_layout)
        
        # Import GraphViewWidget dynamically to avoid circular import
        from ..graph_view import GraphViewWidget
        
        # Graph view widget (add directly without extra splitter pane)
        self.graph_view = GraphViewWidget(self)
        if hasattr(self.graph_view, 'actions_toolbar'):
            self.graph_view.actions_toolbar.hide()
            if hasattr(self.graph_view.actions_toolbar, '_toggle_btn_actions'):
                self.graph_view.actions_toolbar._toggle_btn_actions.hide()
        layout.addWidget(self.graph_view)
        
        # Button box
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept_changes)
        button_box.rejected.connect(self.reject)
        
        # Add save button
        save_button = QPushButton(_("Save Changes"))
        save_button.clicked.connect(self.save_changes)
        button_box.addButton(save_button, QDialogButtonBox.ActionRole)
        
        layout.addWidget(button_box)
    
    def load_chain_into_graph(self):
        """Load the chain configuration into the graph view"""
        try:
            print(f"DEBUG: load_chain_into_graph called")
            print(f"DEBUG: hasattr chain_config: {hasattr(self, 'chain_config')}")
            if hasattr(self, 'chain_config'):
                print(f"DEBUG: chain_config exists: {bool(self.chain_config)}")
                if self.chain_config:
                    print(f"DEBUG: chain_config keys: {list(self.chain_config.keys()) if isinstance(self.chain_config, dict) else 'not a dict'}")
            print(f"DEBUG: graph_view exists: {bool(self.graph_view)}")
            
            if not hasattr(self, 'chain_config') or not self.chain_config or not self.graph_view:
                print("DEBUG: Early return - missing chain_config or graph_view")
                return
            
            # Initialize the config manager with the graph view
            print("DEBUG: Initializing ConfigManager")
            self.config_manager = ConfigManager(self.graph_view)
            self.config_manager.chain_config = self.chain_config
            print("DEBUG: Calling build_graph_from_config")
            self.config_manager.build_graph_from_config()
            
            # Note: Not calling auto_layout_nodes() to preserve saved positions from configuration
            
            # Update the info display with loaded chain information
            if hasattr(self, 'info_display'):
                print("DEBUG: Updating info display")
                self.update_info_display()
            
            print("DEBUG: load_chain_into_graph completed successfully")
            
        except Exception as e:
            print(f"DEBUG: Exception in load_chain_into_graph: {str(e)}")
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "Error", f"Failed to load chain into graph view: {str(e)}")
    
    def create_info_panel(self):
        """Create an info panel showing chain details"""
        from PyQt5.QtWidgets import QWidget, QVBoxLayout, QTextEdit
        
        panel = QWidget()
        panel.setMaximumWidth(200)
        panel.setMinimumWidth(180)
        layout = QVBoxLayout(panel)
        
        # Chain info display
        self.info_display = QTextEdit()
        self.info_display.setReadOnly(True)
        self.info_display.setStyleSheet(f"""
            QTextEdit {{
                color: {TEXT_COLOR};
                background-color: #2b2b2b;
                border: 1px solid #555;
                padding: 10px;
                font-family: 'Consolas', monospace;
                font-size: 11px;
            }}
        """)
        
        layout.addWidget(self.info_display)
        self.update_info_display()
        
        return panel
    
    def update_info_display(self):
        """Update the info display with current chain information"""
        try:
            import_config = self.chain_import_node.get_chain_import_config()
            chain_file = import_config.get('chain_file', 'Not configured')
            
            info_text = f"""Chain File Information
{'=' * 25}

File Path:
{chain_file}

Chain Name:
{self.chain_import_node.name()}

"""
            
            if hasattr(self, 'chain_config') and self.chain_config:
                nodes = self.chain_config.get('nodes', [])
                connections = self.chain_config.get('connections', [])
                
                info_text += f"""Nodes: {len(nodes)}
Connections: {len(connections)}

Node Types:
"""
                
                # Count node types
                node_types = {}
                for node in nodes:
                    node_type = node.get('type', 'Unknown')
                    node_types[node_type] = node_types.get(node_type, 0) + 1
                
                for node_type, count in sorted(node_types.items()):
                    info_text += f"  {node_type}: {count}\n"
            else:
                info_text += "Chain not loaded yet..."
            
            self.info_display.setPlainText(info_text)
            
        except Exception as e:
            self.info_display.setPlainText(f"Error loading chain info:\n{str(e)}")
    
    def save_changes(self):
        """Save changes back to the chain file"""
        try:
            if not self.graph_view:
                return
            
            # Get updated chain configuration from graph view
            updated_config = self.graph_view.get_chain_config()
            
            # Get the original chain file path
            import_config = self.chain_import_node.get_chain_import_config()
            chain_file = import_config.get('chain_file', '')
            
            if not chain_file:
                QMessageBox.warning(self, _("Warning"), _("No chain file configured."))
                return
            
            # Save the updated configuration
            with open(chain_file, 'w', encoding='utf-8') as f:
                json.dump(updated_config, f, indent=2)
            
            # Clear entire cache to ensure all chains are updated immediately
            print(f"DEBUG: Clearing entire cache after modifying chain: {chain_file}")
            clear_cache()
            
            QMessageBox.information(self, _("Success"), _("Chain changes saved successfully."))
            
        except Exception as e:
            QMessageBox.critical(self, _("Error"), _("Failed to save changes: {error}").format(error=str(e)))
    
    def accept_changes(self):
        """Accept and close dialog"""
        self.save_changes()
        self.accept()
    
    def get_updated_config(self):
        """Get the updated chain configuration"""
        if self.graph_view:
            return self.graph_view.get_chain_config()
        return None
    
    def toggle_maximize(self):
        """Toggle between maximized and normal window state"""
        if self.isMaximized():
            self.showNormal()
            self.maximize_button.setToolTip(_("Maximize/Restore"))
            self.maximize_button.setIcon(self._max_icon(16))
        else:
            self.showMaximized()
            self.maximize_button.setToolTip(_("Maximize/Restore"))
            self.maximize_button.setIcon(self._restore_icon(16))

    def _max_icon(self, size: int) -> QIcon:
        pix = QPixmap(size, size)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        pen = p.pen()
        pen.setColor(QColor(TEXT_COLOR))
        try:
            pen.setWidthF(2.0)
        except Exception:
            pen.setWidth(2)
        p.setPen(pen)
        s = size
        m = int(s * 0.18)
        p.drawRoundedRect(m, m, s - 2 * m, s - 2 * m, 3, 3)
        p.end()
        return QIcon(pix)

    def _restore_icon(self, size: int) -> QIcon:
        pix = QPixmap(size, size)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        pen = p.pen()
        pen.setColor(QColor(TEXT_COLOR))
        try:
            pen.setWidthF(2.0)
        except Exception:
            pen.setWidth(2)
        p.setPen(pen)
        s = size
        m = int(s * 0.22)
        p.drawRoundedRect(m - 2, m, s - 2 * m, s - 2 * m, 3, 3)
        p.drawRoundedRect(m, m - 2, s - 2 * m, s - 2 * m, 3, 3)
        p.end()
        return QIcon(pix)
