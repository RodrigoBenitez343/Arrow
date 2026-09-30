import sys
from PyQt5.QtWidgets import (
    QVBoxLayout, QLabel, QLineEdit, QHBoxLayout, 
    QSpinBox, QPushButton, QFileDialog
)
from .base_dialog import ModernDialog
from .toggle_switch import ModernToggle

class ContainerNodeDialog(ModernDialog):
    """Dialog for configuring Container (VM) nodes."""
    
    def __init__(self, parent=None, config=None):
        self.config = config or {}
        super().__init__(parent, "Container (VM) Node Configuration", help_topic="container-node")
        self.resize(600, 450)
        
    def setup_ui(self):
        """Set up the UI for the dialog."""
        layout = QVBoxLayout(self.content_area)
        layout.setSpacing(15)
        
        # ISO path entry
        iso_layout = QVBoxLayout()
        iso_layout.addWidget(QLabel("ISO File Path (e.g., Slitaz ISO):"))
        iso_input_layout = QHBoxLayout()
        self.iso_entry = QLineEdit()
        self.iso_entry.setText(self.config.get('iso_path', ''))
        self.iso_entry.setPlaceholderText("Select or enter path to bootable ISO")
        iso_input_layout.addWidget(self.iso_entry)
        
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse_iso)
        iso_input_layout.addWidget(browse_btn)
        
        iso_layout.addLayout(iso_input_layout)
        layout.addLayout(iso_layout)
        
        # Resource settings
        resources_layout = QHBoxLayout()
        
        mem_layout = QVBoxLayout()
        mem_layout.addWidget(QLabel("Memory (MB):"))
        self.mem_spin = QSpinBox()
        self.mem_spin.setRange(256, 16384)
        self.mem_spin.setSingleStep(256)
        self.mem_spin.setValue(int(self.config.get('memory_mb', 1024)))
        mem_layout.addWidget(self.mem_spin)
        resources_layout.addLayout(mem_layout)
        
        cpu_layout = QVBoxLayout()
        cpu_layout.addWidget(QLabel("CPU Cores:"))
        self.cpu_spin = QSpinBox()
        self.cpu_spin.setRange(1, 16)
        self.cpu_spin.setValue(int(self.config.get('cpu_cores', 1)))
        cpu_layout.addWidget(self.cpu_spin)
        resources_layout.addLayout(cpu_layout)
        
        layout.addLayout(resources_layout)
        
        # Execution settings
        settings_layout = QVBoxLayout()
        
        # Hide Window option
        hide_layout = QHBoxLayout()
        self.hide_checkbox = ModernToggle(text="Hide VM Window (Background execution)")
        self.hide_checkbox.setChecked(self.config.get('hide_window', True))
        hide_layout.addWidget(self.hide_checkbox)
        settings_layout.addLayout(hide_layout)
        
        # Execute on input
        exec_layout = QHBoxLayout()
        self.exec_checkbox = ModernToggle(text="Execute automatically on input")
        self.exec_checkbox.setChecked(self.config.get('execute_on_input', True))
        exec_layout.addWidget(self.exec_checkbox)
        settings_layout.addLayout(exec_layout)
        
        # Output variable
        out_var_layout = QHBoxLayout()
        out_var_layout.addWidget(QLabel("Output Variable:"))
        self.out_var_entry = QLineEdit()
        self.out_var_entry.setText(self.config.get('output_variable', 'container_result'))
        out_var_layout.addWidget(self.out_var_entry)
        settings_layout.addLayout(out_var_layout)
        
        # Timeout
        timeout_layout = QHBoxLayout()
        timeout_layout.addWidget(QLabel("Timeout (seconds):"))
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(1, 3600)
        self.timeout_spin.setValue(int(self.config.get('timeout', 300)))
        timeout_layout.addWidget(self.timeout_spin)
        settings_layout.addLayout(timeout_layout)
        
        layout.addLayout(settings_layout)
        layout.addStretch()
        
        self.create_dialog_buttons()
        
    def _browse_iso(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Bootable ISO", "", "ISO Files (*.iso);;All Files (*)"
        )
        if file_path:
            self.iso_entry.setText(file_path)
            
    def get_config(self):
        """Get the updated configuration."""
        return {
            'iso_path': self.iso_entry.text(),
            'memory_mb': self.mem_spin.value(),
            'cpu_cores': self.cpu_spin.value(),
            'hide_window': self.hide_checkbox.isChecked(),
            'execute_on_input': self.exec_checkbox.isChecked(),
            'output_variable': self.out_var_entry.text(),
            'timeout': self.timeout_spin.value()
        }
