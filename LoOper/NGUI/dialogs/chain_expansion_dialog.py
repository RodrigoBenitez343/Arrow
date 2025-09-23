# dialogs/chain_expansion_dialog.py

import os
import json
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
                             QDialogButtonBox, QPushButton, QMessageBox, QSplitter)
from PyQt5.QtCore import Qt
from ..graph_elements.config_manager import ConfigManager
from ..constants import TEXT_COLOR

class ChainExpansionDialog(QDialog):
    """Dialog for expanding and editing imported chains in a separate graph view"""
    
    def __init__(self, chain_import_node, parent_graph_view, parent=None):
        super().__init__(parent)
        self.chain_import_node = chain_import_node
        self.parent_graph_view = parent_graph_view
        self.config_manager = None  # Will be initialized after graph_view is created
        
        self.setWindowTitle("Edit Imported Chain")
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
                QMessageBox.warning(self, "Warning", "Chain file not found or not configured.")
                return
            
            print(f"DEBUG: Loading chain file: {chain_file}")
            with open(chain_file, 'r', encoding='utf-8') as f:
                self.chain_config = json.load(f)
            print(f"DEBUG: Chain config loaded successfully. Keys: {list(self.chain_config.keys()) if isinstance(self.chain_config, dict) else 'not a dict'}")
                
        except Exception as e:
            print(f"DEBUG: Exception in load_chain_config: {str(e)}")
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "Error", f"Failed to load chain configuration: {str(e)}")
    
    def setup_ui(self):
        """Setup the dialog UI"""
        layout = QVBoxLayout(self)
        
        # Header
        header_layout = QHBoxLayout()
        chain_name = self.chain_import_node.name()
        header_label = QLabel(f"Editing Chain: {chain_name}")
        header_label.setStyleSheet(f"color: {TEXT_COLOR}; font-size: 14px; font-weight: bold;")
        header_layout.addWidget(header_label)
        header_layout.addStretch()
        
        layout.addLayout(header_layout)
        
        # Create splitter for graph view and info panel
        splitter = QSplitter(Qt.Horizontal)
        
        # Import GraphViewWidget dynamically to avoid circular import
        from ..graph_view import GraphViewWidget
        
        # Graph view widget
        self.graph_view = GraphViewWidget(self)
        splitter.addWidget(self.graph_view)
        
        # Chain info panel
        self.info_panel = self.create_info_panel()
        splitter.addWidget(self.info_panel)
        
        # Set splitter proportions (give more space to graph view)
        splitter.setSizes([800, 200])
        layout.addWidget(splitter)
        
        # Button box
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept_changes)
        button_box.rejected.connect(self.reject)
        
        # Add save button
        save_button = QPushButton("Save Changes")
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
                QMessageBox.warning(self, "Warning", "No chain file configured.")
                return
            
            # Save the updated configuration
            with open(chain_file, 'w', encoding='utf-8') as f:
                json.dump(updated_config, f, indent=2)
            
            QMessageBox.information(self, "Success", "Chain changes saved successfully.")
            
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save changes: {str(e)}")
    
    def accept_changes(self):
        """Accept and close dialog"""
        self.save_changes()
        self.accept()
    
    def get_updated_config(self):
        """Get the updated chain configuration"""
        if self.graph_view:
            return self.graph_view.get_chain_config()
        return None