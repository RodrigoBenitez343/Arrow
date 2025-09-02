# dialogs/fallback_dialogs.py

import json
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QLabel, QLineEdit, QPushButton, QHBoxLayout,
                             QGroupBox, QGridLayout, QSpinBox, QDoubleSpinBox, QCheckBox, QWidget, QFileDialog)
from ..constants import TEXT_COLOR


class ActionFallbackConfigDialog(QDialog):
    """Dialog for configuring action fallbacks"""
    
    def __init__(self, parent, sequence_idx, action_idx, chain_config, current_fallback=None):
        super().__init__(parent)
        self.sequence_idx = sequence_idx
        self.action_idx = action_idx
        self.chain_config = chain_config
        self.current_fallback = current_fallback
        
        self.setWindowTitle(f"Configure Fallback - Sequence {sequence_idx + 1}, Action {action_idx + 1}")
        self.setModal(True)
        self.resize(400, 300)
        
        self.setup_ui()
    
    def setup_ui(self):
        """Setup the UI for the dialog"""
        layout = QVBoxLayout(self)
        
        # Action info
        info_group = QGroupBox("Action Information")
        info_layout = QVBoxLayout(info_group)
        
        sequence_file = self.chain_config[self.sequence_idx]['sequence_file']
        try:
            with open(sequence_file, 'r') as f:
                sequence_data = json.load(f)
            
            action = sequence_data['actions'][self.action_idx]
            action_info = f"Type: {action.get('type', 'unknown')}\n"
            action_info += f"Position: ({action.get('x', 0)}, {action.get('y', 0)})\n"
            action_info += f"Has Screenshot: {'Yes' if action.get('screenshot') else 'No'}"
            
            info_label = QLabel(action_info)
            info_label.setStyleSheet(f"color: {TEXT_COLOR};")
            info_layout.addWidget(info_label)
            
        except Exception as e:
            error_label = QLabel(f"Error loading action info: {str(e)}")
            error_label.setStyleSheet("color: #FF6B6B;")
            info_layout.addWidget(error_label)
        
        layout.addWidget(info_group)
        
        # Fallback configuration
        config_group = QGroupBox("Fallback Configuration")
        config_layout = QVBoxLayout(config_group)
        
        self.enable_fallback = QCheckBox("Enable fallback for this action")
        self.enable_fallback.setChecked(self.current_fallback is not None)
        self.enable_fallback.toggled.connect(self.on_fallback_enabled)
        config_layout.addWidget(self.enable_fallback)
        
        # Fallback sequence selection
        fallback_layout = QHBoxLayout()
        fallback_layout.addWidget(QLabel("Fallback Sequence:"))
        
        self.fallback_file_entry = QLineEdit()
        if self.current_fallback:
            self.fallback_file_entry.setText(self.current_fallback.get('sequence_file', ''))
        fallback_layout.addWidget(self.fallback_file_entry)
        
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self.browse_fallback_file)
        fallback_layout.addWidget(browse_btn)
        
        self.fallback_widgets = QWidget()
        fallback_widgets_layout = QVBoxLayout(self.fallback_widgets)
        fallback_widgets_layout.addLayout(fallback_layout)
        
        # Fallback parameters
        params_layout = QGridLayout()
        
        params_layout.addWidget(QLabel("Retry Attempts:"), 0, 0)
        self.retry_attempts = QSpinBox()
        self.retry_attempts.setRange(1, 10)
        self.retry_attempts.setValue(self.current_fallback.get('retry_attempts', 3) if self.current_fallback else 3)
        params_layout.addWidget(self.retry_attempts, 0, 1)
        
        params_layout.addWidget(QLabel("Retry Delay (s):"), 1, 0)
        self.retry_delay = QDoubleSpinBox()
        self.retry_delay.setRange(0.1, 10.0)
        self.retry_delay.setSingleStep(0.1)
        self.retry_delay.setValue(self.current_fallback.get('retry_delay', 1.0) if self.current_fallback else 1.0)
        params_layout.addWidget(self.retry_delay, 1, 1)
        
        fallback_widgets_layout.addLayout(params_layout)
        config_layout.addWidget(self.fallback_widgets)
        
        layout.addWidget(config_group)
        
        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        ok_btn = QPushButton("OK")
        ok_btn.clicked.connect(self.accept)
        button_layout.addWidget(ok_btn)
        
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)
        
        remove_btn = QPushButton("Remove Fallback")
        remove_btn.clicked.connect(self.remove_fallback)
        button_layout.addWidget(remove_btn)
        
        layout.addLayout(button_layout)
        
        # Update UI state
        self.on_fallback_enabled()
    
    def on_fallback_enabled(self):
        """Handle fallback enabled/disabled"""
        self.fallback_widgets.setEnabled(self.enable_fallback.isChecked())
    
    def browse_fallback_file(self):
        """Browse for fallback sequence file"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Fallback Sequence", "", "JSON Files (*.json)"
        )
        if file_path:
            self.fallback_file_entry.setText(file_path)
    
    def remove_fallback(self):
        """Remove the fallback configuration"""
        self.done(2)  # Custom result code for removal
    
    def get_fallback_config(self):
        """Get the fallback configuration"""
        if not self.enable_fallback.isChecked():
            return None
        
        return {
            'sequence_file': self.fallback_file_entry.text(),
            'retry_attempts': self.retry_attempts.value(),
            'retry_delay': self.retry_delay.value()
        }