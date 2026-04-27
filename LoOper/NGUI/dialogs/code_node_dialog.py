from PyQt5.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, 
    QCheckBox, QPlainTextEdit, QFileDialog, QPushButton,
    QTabWidget, QWidget, QSpinBox, QComboBox, QMessageBox,
    QApplication
)
from PyQt5.QtGui import QFont, QColor
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from .base_dialog import ModernDialog
from ..constants import TEXT_COLOR, LIGHT_GREY, ACCENT_COLOR
from ..nodes_resources.base_node import get_default_api_url
from ..i18n import _
import sys
import os

# Import AI modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
try:
    from AI.consult import OllamaClient
    from AI.model_cache import get_cached_models, get_real_cached_models, get_model_cache
except ImportError:
    OllamaClient = None
    get_cached_models = lambda: []
    get_real_cached_models = lambda timeout=5.0: []
    get_model_cache = lambda: None

# Import llama.cpp model scanner
try:
    from .llm_dialogs import get_llamacpp_models
except ImportError:
    get_llamacpp_models = lambda: []

class CodeGeneratorThread(QThread):
    finished = pyqtSignal(str)
    error = pyqtSignal(str)
    
    def __init__(self, model, prompt, api_url=None):
        super().__init__()
        self.model = model
        self.prompt = prompt
        self.api_url = api_url
        
    def run(self):
        try:
            # Use provided API URL or default
            api_url = self.api_url or get_default_api_url()
            client = OllamaClient(base_url=api_url)
            
            # Construct a system prompt for code generation
            system_prompt = (
                "You are an expert Python developer. "
                "Write a Python script based on the user's request. "
                "The script should be self-contained or use standard libraries. "
                "You have access to 'input_data' and 'args' variables from the previous node. "
                "Assign the final result to a variable named 'result' (or as specified). "
                "Return ONLY the Python code, no markdown formatting, no explanations."
            )
            
            # Note: generate() does not accept 'stream' argument
            response = client.generate(
                model=self.model,
                prompt=self.prompt,
                system=system_prompt
            )
            
            code = response.get('response', '')
            # Strip markdown if present
            if code.startswith("```python"):
                code = code[9:]
            elif code.startswith("```"):
                code = code[3:]
            if code.endswith("```"):
                code = code[:-3]
                
            self.finished.emit(code.strip())
        except Exception as e:
            self.error.emit(str(e))

class CodeNodeDialog(ModernDialog):
    def __init__(self, parent=None, config=None):
        super().__init__(parent, title=_("Code Node Configuration"), help_topic="code-node-dialog")
        self.resize(700, 600)
        
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
        
        # Tab 1: AI Generator
        self.generator_tab = QWidget()
        self.setup_generator_tab()
        self.tabs.addTab(self.generator_tab, _("AI Generator"))

        # Tab 2: Code Editor
        self.editor_tab = QWidget()
        self.setup_editor_tab()
        self.tabs.addTab(self.editor_tab, _("Code Editor"))
        
        # Tab 3: Settings
        self.settings_tab = QWidget()
        self.setup_settings_tab()
        self.tabs.addTab(self.settings_tab, _("Settings"))
        
        self.content_layout.addWidget(self.tabs)
        self.add_buttons()
        
        # Load engine selection from config
        use_llamacpp = self.config.get('use_llamacpp', False)
        if isinstance(use_llamacpp, str):
            use_llamacpp = use_llamacpp.lower() in ('true', '1', 'yes', 'on')
        
        if use_llamacpp:
            self.engine_combo.setCurrentText("llama.cpp")
        else:
            self.engine_combo.setCurrentText("Ollama")
        
    def add_buttons(self):
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        cancel_btn = QPushButton(_("Cancel"))
        cancel_btn.clicked.connect(self.reject)
        cancel_btn.setStyleSheet(self.get_button_style())
        
        save_btn = QPushButton(_("Save"))
        save_btn.clicked.connect(self.accept)
        save_btn.setStyleSheet(self.get_button_style().replace(LIGHT_GREY, ACCENT_COLOR).replace(TEXT_COLOR, "#121212")) # Accent style
        
        button_layout.addWidget(cancel_btn)
        button_layout.addWidget(save_btn)
        
        self.content_layout.addLayout(button_layout)

    def refresh_models(self):
        """Refresh the list of available models from the API"""
        try:
            api_url = get_default_api_url()
            current_model = self.model_combo.currentText()
            
            # Refresh cache
            cache = get_model_cache()
            if cache:
                cache.refresh_models(api_url)
                
            # Get real models
            models = get_real_cached_models(timeout=5.0)
            
            if models:
                self.model_combo.clear()
                self.model_combo.addItems(models)
                
                # Restore selection if it exists
                if current_model in models:
                    self.model_combo.setCurrentText(current_model)
                else:
                    self.model_combo.setCurrentText(models[0])
            else:
                # If no real models found, don't clear, just show message
                QMessageBox.information(self, _("No Models Found"), _("Could not detect any downloaded models from Ollama API."))
                
        except Exception as e:
            QMessageBox.warning(self, _("Refresh Failed"), _("Failed to refresh models: {error}").format(error=str(e)))
    
    def on_engine_changed(self, engine):
        """Handle engine selection change"""
        use_llamacpp = (engine == "llama.cpp")
        
        # Show/hide appropriate model dropdowns
        self.model_combo.setVisible(not use_llamacpp)
        self.model_label.setVisible(not use_llamacpp)
        
        self.llamacpp_model_combo.setVisible(use_llamacpp)
        self.llamacpp_model_label.setVisible(use_llamacpp)

    def setup_generator_tab(self):
        layout = QVBoxLayout(self.generator_tab)
        layout.setSpacing(15)

        # Engine Selection
        engine_layout = QHBoxLayout()
        engine_label = QLabel(_("Engine:"))
        engine_label.setStyleSheet(f"color: {TEXT_COLOR}; font-weight: bold;")
        engine_layout.addWidget(engine_label)
        
        self.engine_combo = QComboBox()
        self.engine_combo.addItems(["Ollama", "llama.cpp"])
        self.engine_combo.setStyleSheet(self.get_input_style())
        self.engine_combo.currentTextChanged.connect(self.on_engine_changed)
        engine_layout.addWidget(self.engine_combo)
        engine_layout.addStretch()
        layout.addLayout(engine_layout)
        
        # Model Selection
        model_layout = QVBoxLayout()
        self.model_label = QLabel(_("Ollama Model:"))
        self.model_label.setStyleSheet(f"color: {TEXT_COLOR}; font-weight: bold;")
        
        model_select_layout = QHBoxLayout()
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.setStyleSheet(self.get_input_style())
        
        # Refresh models button
        refresh_btn = QPushButton(_("Refresh"))
        refresh_btn.setStyleSheet(self.get_button_style())
        refresh_btn.clicked.connect(self.refresh_models)
        
        model_select_layout.addWidget(self.model_combo)
        model_select_layout.addWidget(refresh_btn)
        
        # Load Ollama models
        try:
            # Try to get real cached models first (downloaded ones)
            models = get_real_cached_models(timeout=2.0)
            if not models:
                # Fallback to cached list (which might have defaults)
                models = get_cached_models()
                
            if models:
                self.model_combo.addItems(models)
            else:
                self.model_combo.addItem("llama3.2:latest")
        except Exception:
            self.model_combo.addItem("llama3.2:latest")
            
        current_model = self.config.get('gen_model', 'llama3.2:latest')
        self.model_combo.setCurrentText(current_model)
        
        model_layout.addWidget(self.model_label)
        model_layout.addLayout(model_select_layout)
        
        # llama.cpp Model Selection (initially hidden)
        llamacpp_model_layout = QVBoxLayout()
        self.llamacpp_model_label = QLabel(_("GGUF Model:"))
        self.llamacpp_model_label.setStyleSheet(f"color: {TEXT_COLOR}; font-weight: bold;")
        
        self.llamacpp_model_combo = QComboBox()
        self.llamacpp_model_combo.setEditable(True)
        self.llamacpp_model_combo.setStyleSheet(self.get_input_style())
        
        # Load GGUF models
        gguf_models = get_llamacpp_models()
        if gguf_models:
            self.llamacpp_model_combo.addItems(gguf_models)
        else:
            self.llamacpp_model_combo.setPlaceholderText("No GGUF models found in AI/models/")
        
        # Set current model if exists
        current_llamacpp = self.config.get('llamacpp_model_path', '')
        if current_llamacpp:
            self.llamacpp_model_combo.setCurrentText(current_llamacpp)
        
        llamacpp_model_layout.addWidget(self.llamacpp_model_label)
        llamacpp_model_layout.addWidget(self.llamacpp_model_combo)
        
        # Initially hide llama.cpp model selection
        self.llamacpp_model_label.setVisible(False)
        self.llamacpp_model_combo.setVisible(False)
        
        layout.addLayout(model_layout)
        layout.addLayout(llamacpp_model_layout)
        
        # Prompt
        prompt_layout = QVBoxLayout()
        prompt_label = QLabel(_("Prompt / Instructions:"))
        prompt_label.setStyleSheet(f"color: {TEXT_COLOR}; font-weight: bold;")
        
        self.prompt_edit = QPlainTextEdit()
        self.prompt_edit.setPlaceholderText(_("Describe what the code should do..."))
        self.prompt_edit.setPlainText(self.config.get('gen_prompt', ''))
        self.prompt_edit.setStyleSheet(f"""
            QPlainTextEdit {{
                background-color: #252526;
                color: {TEXT_COLOR};
                border: 1px solid {LIGHT_GREY};
                border-radius: 4px;
                padding: 5px;
            }}
        """)
        
        prompt_layout.addWidget(prompt_label)
        prompt_layout.addWidget(self.prompt_edit)
        layout.addLayout(prompt_layout)
        
        # Generate Button
        self.gen_btn = QPushButton(_("Generate Code"))
        self.gen_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {ACCENT_COLOR};
                color: #121212;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: #00CC99;
            }}
            QPushButton:disabled {{
                background-color: #555555;
                color: #888888;
            }}
        """)
        self.gen_btn.clicked.connect(self.generate_code)
        layout.addWidget(self.gen_btn)
        
        # Status Label
        self.status_label = QLabel("")
        self.status_label.setStyleSheet(f"color: {TEXT_COLOR}; font-style: italic;")
        self.status_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.status_label)
        
    def generate_code(self):
        prompt = self.prompt_edit.toPlainText()
        if not prompt:
            QMessageBox.warning(self, _("Missing Prompt"), _("Please enter a prompt describing the code you want to generate."))
            return
            
        model = self.model_combo.currentText()
        
        self.gen_btn.setEnabled(False)
        self.gen_btn.setText(_("Generating..."))
        self.status_label.setText(_("Generating code... please wait."))
        
        # Start generation thread
        self.gen_thread = CodeGeneratorThread(model, prompt)
        self.gen_thread.finished.connect(self.on_generation_finished)
        self.gen_thread.error.connect(self.on_generation_error)
        self.gen_thread.start()
        
    def on_generation_finished(self, code):
        self.code_edit.setPlainText(code)
        self.gen_btn.setEnabled(True)
        self.gen_btn.setText(_("Generate Code"))
        self.status_label.setText(_("Code generated successfully! Switch to 'Code Editor' tab to view/edit."))
        # Optional: Switch tab automatically
        self.tabs.setCurrentWidget(self.editor_tab)
        
    def on_generation_error(self, error_msg):
        self.gen_btn.setEnabled(True)
        self.gen_btn.setText(_("Generate Code"))
        self.status_label.setText(_("Generation failed."))
        QMessageBox.critical(self, _("Generation Error"), _("Failed to generate code:\n{error}").format(error=error_msg))

    def setup_editor_tab(self):
        layout = QVBoxLayout(self.editor_tab)
        
        # Instructions
        info_label = QLabel(_("Write Python code to execute. 'input_data' and 'args' variables are available."))
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
        
        # Description / Purpose (used as context key)
        desc_layout = QVBoxLayout()
        desc_label = QLabel(_("Description / Purpose:"))
        desc_label.setStyleSheet(f"color: {TEXT_COLOR}; font-weight: bold;")
        desc_info = QLabel(_("What this code does (used as context storage key when connected to Context node)"))
        desc_info.setStyleSheet(f"color: {TEXT_COLOR}; font-size: 11px; margin-bottom: 5px;")
        
        self.description_edit = QLineEdit(self.config.get('description', ''))
        self.description_edit.setPlaceholderText(_("e.g., 'parse_csv_output', 'fetch_api_data'"))
        self.description_edit.setStyleSheet(self.get_input_style())
        
        desc_layout.addWidget(desc_label)
        desc_layout.addWidget(desc_info)
        desc_layout.addWidget(self.description_edit)
        layout.addLayout(desc_layout)
        
        # File Path (Optional override)
        file_layout = QVBoxLayout()
        file_label = QLabel(_("External File Path (Overrides internal code if set):"))
        file_label.setStyleSheet(f"color: {TEXT_COLOR}; font-weight: bold;")
        
        path_layout = QHBoxLayout()
        self.file_path_edit = QLineEdit(self.config.get('file_path', ''))
        self.file_path_edit.setPlaceholderText("/path/to/script.py")
        self.file_path_edit.setStyleSheet(self.get_input_style())
        
        browse_btn = QPushButton(_("Browse..."))
        browse_btn.setStyleSheet(self.get_button_style())
        browse_btn.clicked.connect(self.browse_file)
        
        path_layout.addWidget(self.file_path_edit)
        path_layout.addWidget(browse_btn)
        
        file_layout.addWidget(file_label)
        file_layout.addLayout(path_layout)
        layout.addLayout(file_layout)
        
        # Output Variable
        var_layout = QVBoxLayout()
        var_label = QLabel(_("Output Variable Name:"))
        var_label.setStyleSheet(f"color: {TEXT_COLOR}; font-weight: bold;")
        var_info = QLabel(_("The variable name in your code that contains the result to output."))
        var_info.setStyleSheet(f"color: {TEXT_COLOR}; font-size: 11px; margin-bottom: 5px;")
        
        self.output_var_edit = QLineEdit(self.config.get('output_variable', 'result'))
        self.output_var_edit.setStyleSheet(self.get_input_style())
        
        var_layout.addWidget(var_label)
        var_layout.addWidget(var_info)
        var_layout.addWidget(self.output_var_edit)
        layout.addLayout(var_layout)
        
        # Execution Options
        opts_layout = QVBoxLayout()
        opts_label = QLabel(_("Execution Options:"))
        opts_label.setStyleSheet(f"color: {TEXT_COLOR}; font-weight: bold;")
        
        self.exec_check = QCheckBox(_("Execute on Input Trigger"))
        self.exec_check.setChecked(self.config.get('execute_on_input', True))
        self.exec_check.setStyleSheet(f"color: {TEXT_COLOR};")
        
        # Timeout
        timeout_layout = QHBoxLayout()
        timeout_label = QLabel(_("Timeout (seconds):"))
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
            self, _("Select Python Script"), "", _("Python Files (*.py);;All Files (*)")
        )
        if file_path:
            self.file_path_edit.setText(file_path)
            
    def get_input_style(self):
        return f"""
            QLineEdit, QSpinBox, QComboBox {{
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
            QComboBox::drop-down {{
                border: none;
                background-color: {LIGHT_GREY};
                width: 20px;
                border-top-right-radius: 4px;
                border-bottom-right-radius: 4px;
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
        # Determine which engine is selected
        use_llamacpp = (self.engine_combo.currentText() == "llama.cpp")
        
        return {
            'code': self.code_edit.toPlainText(),
            'file_path': self.file_path_edit.text(),
            'execute_on_input': self.exec_check.isChecked(),
            'output_variable': self.output_var_edit.text(),
            'timeout': self.timeout_spin.value(),
            'use_llamacpp': use_llamacpp,
            'gen_model': self.model_combo.currentText() if not use_llamacpp else 'llamacpp',
            'llamacpp_model_path': self.llamacpp_model_combo.currentText() if use_llamacpp else '',
            'gen_prompt': self.prompt_edit.toPlainText(),
            'description': self.description_edit.text()
        }
