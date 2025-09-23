# dialogs/chain_import_dialogs.py

import os
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, 
                             QDialogButtonBox, QPushButton, QFileDialog, QComboBox, 
                             QCheckBox, QTextEdit, QGroupBox, QMessageBox)
from PyQt5.QtCore import Qt
from ..constants import TEXT_COLOR


class ChainImportDialog(QDialog):
    """Dialog for configuring chain import settings"""
    
    def __init__(self, chain_import_config=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Chain Import Configuration")
        self.setModal(True)
        self.resize(600, 450)
        self.setMinimumSize(600, 450)
        
        # Apply styling consistent with other dialogs
        self.setStyleSheet(f"""
            QDialog {{
                background-color: #2b2b2b;
                color: {TEXT_COLOR};
            }}
            QGroupBox {{
                font-weight: bold;
                border: 2px solid #555;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
            }}
            QLineEdit {{
                background-color: #3c3c3c;
                border: 1px solid #555;
                border-radius: 3px;
                padding: 5px;
                color: {TEXT_COLOR};
            }}
            QComboBox {{
                background-color: #3c3c3c;
                border: 1px solid #555;
                border-radius: 3px;
                padding: 5px;
                color: {TEXT_COLOR};
            }}
            QTextEdit {{
                background-color: #3c3c3c;
                border: 1px solid #555;
                border-radius: 3px;
                padding: 5px;
                color: {TEXT_COLOR};
            }}
            QPushButton {{
                background-color: #4a4a4a;
                border: 1px solid #666;
                border-radius: 3px;
                padding: 5px 10px;
                color: {TEXT_COLOR};
            }}
            QPushButton:hover {{
                background-color: #5a5a5a;
            }}
            QCheckBox {{
                color: {TEXT_COLOR};
            }}
        """)
        
        # Initialize with default or provided config
        self.config = chain_import_config or {
            'chain_file': '',
            'import_mode': 'full',
            'prefix': '',
            'enabled': True
        }
        
        self.setup_ui()
        self.load_config()
        
    def setup_ui(self):
        """Setup the user interface"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)
        
        # Chain File Selection Group
        file_group = QGroupBox("Chain File Selection")
        file_layout = QVBoxLayout(file_group)
        file_layout.setContentsMargins(10, 15, 10, 10)
        file_layout.setSpacing(8)
        
        # Chain file path
        file_row = QHBoxLayout()
        self.chain_file_label = QLabel("Chain File:")
        self.chain_file_input = QLineEdit()
        self.chain_file_input.setReadOnly(True)
        self.browse_button = QPushButton("Browse...")
        self.browse_button.clicked.connect(self.browse_chain_file)
        
        file_row.addWidget(self.chain_file_label)
        file_row.addWidget(self.chain_file_input, 1)
        file_row.addWidget(self.browse_button)
        file_layout.addLayout(file_row)
        
        # Chain info display
        self.chain_info = QTextEdit()
        self.chain_info.setMaximumHeight(100)
        self.chain_info.setReadOnly(True)
        file_layout.addWidget(QLabel("Chain Information:"))
        file_layout.addWidget(self.chain_info)
        
        layout.addWidget(file_group)
        
        # Import Configuration Group
        config_group = QGroupBox("Import Configuration")
        config_layout = QVBoxLayout(config_group)
        config_layout.setContentsMargins(10, 15, 10, 10)
        config_layout.setSpacing(8)
        
        # Import mode
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Import Mode:"))
        self.import_mode_combo = QComboBox()
        self.import_mode_combo.addItems(['full', 'sequences_only', 'nodes_only'])
        self.import_mode_combo.setToolTip(
            "full: Import all sequences and nodes\n"
            "sequences_only: Import only sequence nodes\n"
            "nodes_only: Import only conditional, LLM, and TTS nodes"
        )
        mode_row.addWidget(self.import_mode_combo)
        mode_row.addStretch()
        config_layout.addLayout(mode_row)
        
        # Node prefix
        prefix_row = QHBoxLayout()
        prefix_row.addWidget(QLabel("Node Prefix:"))
        self.prefix_input = QLineEdit()
        self.prefix_input.setPlaceholderText("Optional prefix for imported node names")
        prefix_row.addWidget(self.prefix_input)
        config_layout.addLayout(prefix_row)
        
        # Enabled checkbox
        self.enabled_checkbox = QCheckBox("Enable this chain import")
        self.enabled_checkbox.setChecked(True)
        config_layout.addWidget(self.enabled_checkbox)
        
        layout.addWidget(config_group)
        
        # Button box
        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)
        
        # Connect signals
        self.chain_file_input.textChanged.connect(self.validate_chain_file)
        
    def browse_chain_file(self):
        """Open file dialog to select chain file"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Chain File",
            "",
            "JSON files (*.json);;All files (*.*)"
        )
        
        if file_path:
            self.chain_file_input.setText(file_path)
            self.validate_chain_file()
    
    def validate_chain_file(self):
        """Validate the selected chain file and display information"""
        file_path = self.chain_file_input.text()
        
        if not file_path:
            self.chain_info.setText("No file selected")
            self.button_box.button(QDialogButtonBox.Ok).setEnabled(False)
            return
        
        if not os.path.exists(file_path):
            self.chain_info.setText("File does not exist")
            self.button_box.button(QDialogButtonBox.Ok).setEnabled(False)
            return
        
        try:
            import json
            with open(file_path, 'r') as f:
                chain_config = json.load(f)
            
            # Count different node types
            sequences = len(chain_config.get('sequences', []))
            conditionals = len(chain_config.get('conditional_nodes', []))
            llm_nodes = len(chain_config.get('llm_nodes', []))
            tts_nodes = len(chain_config.get('tts_nodes', []))
            total_nodes = sequences + conditionals + llm_nodes + tts_nodes
            
            info_text = f"Chain: {os.path.basename(file_path)}\n"
            info_text += f"Total Nodes: {total_nodes}\n"
            info_text += f"  - Sequences: {sequences}\n"
            info_text += f"  - Conditionals: {conditionals}\n"
            info_text += f"  - LLM Nodes: {llm_nodes}\n"
            info_text += f"  - TTS Nodes: {tts_nodes}"
            
            self.chain_info.setText(info_text)
            self.button_box.button(QDialogButtonBox.Ok).setEnabled(total_nodes > 0)
            
        except Exception as e:
            self.chain_info.setText(f"Error reading chain file: {str(e)}")
            self.button_box.button(QDialogButtonBox.Ok).setEnabled(False)
    
    def load_config(self):
        """Load configuration into the dialog"""
        self.chain_file_input.setText(self.config.get('chain_file', ''))
        self.import_mode_combo.setCurrentText(self.config.get('import_mode', 'full'))
        self.prefix_input.setText(self.config.get('prefix', ''))
        self.enabled_checkbox.setChecked(self.config.get('enabled', True))
        
        # Validate the loaded file
        self.validate_chain_file()
    
    def set_config(self, config):
        """Set the configuration for the dialog"""
        self.config = config or {
            'chain_file': '',
            'import_mode': 'full',
            'prefix': '',
            'enabled': True
        }
        self.load_config()
    
    def get_config(self):
        """Get the configuration from the dialog"""
        return {
            'chain_file': self.chain_file_input.text(),
            'import_mode': self.import_mode_combo.currentText(),
            'prefix': self.prefix_input.text().strip(),
            'enabled': self.enabled_checkbox.isChecked()
        }
    
    def accept(self):
        """Override accept to validate before closing"""
        config = self.get_config()
        
        if not config['chain_file']:
            QMessageBox.warning(self, "Warning", "Please select a chain file.")
            return
        
        if not os.path.exists(config['chain_file']):
            QMessageBox.warning(self, "Warning", "Selected chain file does not exist.")
            return
        
        super().accept()