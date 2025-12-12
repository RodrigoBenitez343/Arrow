from PyQt5.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, 
    QCheckBox, QPlainTextEdit, QFileDialog, QPushButton,
    QTabWidget, QWidget, QSpinBox
)
from PyQt5.QtGui import QFont, QColor
from PyQt5.QtCore import Qt
from .base_dialog import ModernDialog
from ..constants import TEXT_COLOR, LIGHT_GREY, ACCENT_COLOR

class CodeNodeDialog(ModernDialog):
    def __init__(self, parent=None, config=None):
        super().__init__(parent, title="Code Node Configuration")
        self.resize(600, 500)
        
        self.config = config or {}
        
        # Setup Tabs
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(f"""
            QTabWidget::pane {{
                border: 1px solid {LIGHT_GREY};
                background: transparent;
            }}
            QTabBar::tab {{
                background: transparent;
                color: {TEXT_COLOR};
                padding: 8px 12px;
                border: 1px solid transparent;
            }}
            QTabBar::tab:selected {{
                border-bottom: 2px solid {ACCENT_COLOR};
                font-weight: bold;
            }}
        """)
        
        # Tab 1: Code Editor
        self.editor_tab = QWidget()
        self.setup_editor_tab()
        self.tabs.addTab(self.editor_tab, "Code Editor")
        
        # Tab 2: Settings
        self.settings_tab = QWidget()
        self.setup_settings_tab()
        self.tabs.addTab(self.settings_tab, "Settings")
        
        self.content_layout.addWidget(self.tabs)
        self.add_buttons()
        
    def add_buttons(self):
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        cancel_btn.setStyleSheet(self.get_button_style())
        
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self.accept)
        save_btn.setStyleSheet(self.get_button_style().replace(LIGHT_GREY, ACCENT_COLOR).replace(TEXT_COLOR, "#121212")) # Accent style
        
        button_layout.addWidget(cancel_btn)
        button_layout.addWidget(save_btn)
        
        self.content_layout.addLayout(button_layout)

    def setup_editor_tab(self):
        layout = QVBoxLayout(self.editor_tab)
        
        # Instructions
        info_label = QLabel("Write Python code to execute. 'input_data' and 'args' variables are available.")
        info_label.setStyleSheet(f"color: {TEXT_COLOR}; font-style: italic;")
        layout.addWidget(info_label)
        
        # Code Editor
        self.code_edit = QPlainTextEdit()
        self.code_edit.setFont(QFont("Consolas", 10))
        self.code_edit.setStyleSheet(f"""
            QPlainTextEdit {{
                background-color: #1E1E1E;
                color: #D4D4D4;
                border: 1px solid {LIGHT_GREY};
                border-radius: 4px;
            }}
        """)
        self.code_edit.setPlainText(self.config.get('code', ''))
        layout.addWidget(self.code_edit)
        
    def setup_settings_tab(self):
        layout = QVBoxLayout(self.settings_tab)
        layout.setSpacing(15)
        
        # File Path (Optional override)
        file_layout = QVBoxLayout()
        file_label = QLabel("External File Path (Overrides internal code if set):")
        file_label.setStyleSheet(f"color: {TEXT_COLOR}; font-weight: bold;")
        
        path_layout = QHBoxLayout()
        self.file_path_edit = QLineEdit(self.config.get('file_path', ''))
        self.file_path_edit.setPlaceholderText("/path/to/script.py")
        self.file_path_edit.setStyleSheet(self.get_input_style())
        
        browse_btn = QPushButton("Browse")
        browse_btn.setStyleSheet(self.get_button_style())
        browse_btn.clicked.connect(self.browse_file)
        
        path_layout.addWidget(self.file_path_edit)
        path_layout.addWidget(browse_btn)
        
        file_layout.addWidget(file_label)
        file_layout.addLayout(path_layout)
        layout.addLayout(file_layout)
        
        # Output Variable
        var_layout = QVBoxLayout()
        var_label = QLabel("Output Variable Name:")
        var_label.setStyleSheet(f"color: {TEXT_COLOR}; font-weight: bold;")
        var_info = QLabel("The variable name in your code that contains the result to output.")
        var_info.setStyleSheet(f"color: {TEXT_COLOR}; font-size: 11px; margin-bottom: 5px;")
        
        self.output_var_edit = QLineEdit(self.config.get('output_variable', 'result'))
        self.output_var_edit.setStyleSheet(self.get_input_style())
        
        var_layout.addWidget(var_label)
        var_layout.addWidget(var_info)
        var_layout.addWidget(self.output_var_edit)
        layout.addLayout(var_layout)
        
        # Execution Options
        opts_layout = QVBoxLayout()
        opts_label = QLabel("Execution Options:")
        opts_label.setStyleSheet(f"color: {TEXT_COLOR}; font-weight: bold;")
        
        self.exec_check = QCheckBox("Execute on Input Trigger")
        self.exec_check.setChecked(self.config.get('execute_on_input', True))
        self.exec_check.setStyleSheet(f"color: {TEXT_COLOR};")
        
        # Timeout
        timeout_layout = QHBoxLayout()
        timeout_label = QLabel("Timeout (seconds):")
        timeout_label.setStyleSheet(f"color: {TEXT_COLOR};")
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(1, 3600)
        self.timeout_spin.setValue(self.config.get('timeout', 30))
        self.timeout_spin.setStyleSheet(self.get_input_style())
        
        timeout_layout.addWidget(timeout_label)
        timeout_layout.addWidget(self.timeout_spin)
        timeout_layout.addStretch()
        
        opts_layout.addWidget(opts_label)
        opts_layout.addWidget(self.exec_check)
        opts_layout.addLayout(timeout_layout)
        layout.addLayout(opts_layout)
        
        layout.addStretch()
        
    def browse_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Python Script", "", "Python Files (*.py);;All Files (*)"
        )
        if file_path:
            self.file_path_edit.setText(file_path)
            
    def get_input_style(self):
        return f"""
            QLineEdit, QSpinBox {{
                background-color: #252526;
                color: {TEXT_COLOR};
                border: 1px solid {LIGHT_GREY};
                padding: 5px;
                border-radius: 4px;
            }}
            QSpinBox::up-button, QSpinBox::down-button {{
                background-color: {LIGHT_GREY};
                border: none;
            }}
        """
        
    def get_button_style(self):
        return f"""
            QPushButton {{
                background-color: {LIGHT_GREY};
                color: {TEXT_COLOR};
                border: none;
                padding: 6px 12px;
                border-radius: 4px;
            }}
            QPushButton:hover {{
                background-color: #3E3E3E;
            }}
        """
        
    def get_config(self):
        return {
            'code': self.code_edit.toPlainText(),
            'file_path': self.file_path_edit.text(),
            'execute_on_input': self.exec_check.isChecked(),
            'output_variable': self.output_var_edit.text(),
            'timeout': self.timeout_spin.value()
        }
