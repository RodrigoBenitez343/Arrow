"""LLM-related dialog classes for the LoOper application.

This module contains the LLMPropertiesDialog class for configuring
LLM (Large Language Model) node properties including model selection,
prompt configuration, generation parameters, and vision capabilities.
"""

import sys
import os
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QLineEdit, QPushButton, QHBoxLayout, 
    QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox, QWidget, QFileDialog,
    QTabWidget, QListWidgetItem, QTextEdit, QGroupBox, QListWidget,
    QDialogButtonBox, QGridLayout
)
from ..constants import TEXT_COLOR
from .utils import get_default_api_url

# Add the parent directory to sys.path to import AI modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from AI.consult import OllamaClient
from AI.model_cache import get_cached_models, get_model_cache


class LLMPropertiesDialog(QDialog):
    """Dialog for configuring LLM node properties"""
    
    def __init__(self, parent, current_config=None):
        super().__init__(parent)
        self.current_config = current_config or {}
        
        self.setWindowTitle("LLM Node Configuration")
        self.setModal(True)
        self.resize(600, 500)
        self.setMinimumSize(500, 400)
        
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
        """)
        
        self.setup_ui()
        self.load_current_config()
    
    def get_available_models(self, api_url=None):
        """Get available models from cache (non-blocking)"""
        # Use cached models to avoid blocking the UI
        models = get_cached_models(timeout=0.5)  # Short timeout since models should be preloaded
        print(f"Retrieved {len(models)} models from cache: {models}")
        return models
    
    def refresh_models(self):
        """Refresh the model list by forcing a cache refresh"""
        api_url = self.api_url_entry.text() or get_default_api_url()
        
        # Store current selection
        current_model = self.model_combo.currentText()
        
        # Check if we need to refresh or if we can use existing cache
        cache = get_model_cache()
        if cache.is_loaded():
            # Use existing cached models if already loaded
            available_models = get_cached_models(timeout=0.1)
            print(f"Using cached models: {available_models}")
        else:
            # Force refresh the model cache asynchronously
            cache.refresh_models(api_url)
            # Wait longer for the refresh to complete
            available_models = get_cached_models(timeout=5.0)
            print(f"Refreshed models from {api_url}: {available_models}")
        
        # Clear and repopulate the combo box
        self.model_combo.clear()
        self.model_combo.addItems(available_models)
        
        # Restore selection if it still exists in the new list
        if current_model in available_models:
            self.model_combo.setCurrentText(current_model)
    
    def setup_ui(self):
        """Setup the UI for the dialog"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(15)
        
        # Model Configuration Group
        model_group = QGroupBox("Model Configuration")
        model_layout = QGridLayout(model_group)
        model_layout.setContentsMargins(10, 15, 10, 10)
        model_layout.setSpacing(10)
        
        # Model selection
        model_layout.addWidget(QLabel("Model:"), 0, 0)
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        
        # Get API URL from current config or use default
        api_url = self.current_config.get('api_url', get_default_api_url())
        available_models = self.get_available_models(api_url)
        self.model_combo.addItems(available_models)
        
        model_layout.addWidget(self.model_combo, 0, 1)
        
        # API URL
        model_layout.addWidget(QLabel("API URL:"), 1, 0)
        api_url_layout = QHBoxLayout()
        self.api_url_entry = QLineEdit()
        self.api_url_entry.setPlaceholderText(get_default_api_url())
        api_url_layout.addWidget(self.api_url_entry)
        
        # Refresh models button
        refresh_btn = QPushButton("Refresh Models")
        refresh_btn.clicked.connect(self.refresh_models)
        api_url_layout.addWidget(refresh_btn)
        
        model_layout.addLayout(api_url_layout, 1, 1)
        
        layout.addWidget(model_group)
        
        # Prompt Configuration Group
        prompt_group = QGroupBox("Prompt Configuration")
        prompt_layout = QVBoxLayout(prompt_group)
        prompt_layout.setContentsMargins(10, 15, 10, 10)
        prompt_layout.setSpacing(10)
        
        # System message
        prompt_layout.addWidget(QLabel("System Message (optional):"))
        self.system_message_edit = QTextEdit()
        self.system_message_edit.setMaximumHeight(80)
        self.system_message_edit.setPlaceholderText("Enter system instructions for the AI model...")
        prompt_layout.addWidget(self.system_message_edit)
        
        # User prompt
        prompt_layout.addWidget(QLabel("Prompt:"))
        self.prompt_edit = QTextEdit()
        self.prompt_edit.setMaximumHeight(120)
        self.prompt_edit.setPlaceholderText("Enter your prompt here...")
        prompt_layout.addWidget(self.prompt_edit)
        
        layout.addWidget(prompt_group)
        
        # Parameters Group
        params_group = QGroupBox("Generation Parameters")
        params_layout = QGridLayout(params_group)
        params_layout.setContentsMargins(10, 15, 10, 10)
        params_layout.setSpacing(10)
        
        # Temperature
        params_layout.addWidget(QLabel("Temperature:"), 0, 0)
        self.temperature_spin = QDoubleSpinBox()
        self.temperature_spin.setRange(0.0, 2.0)
        self.temperature_spin.setSingleStep(0.1)
        self.temperature_spin.setValue(0.7)
        params_layout.addWidget(self.temperature_spin, 0, 1)
        
        # Max tokens
        params_layout.addWidget(QLabel("Max Tokens:"), 1, 0)
        self.max_tokens_spin = QSpinBox()
        self.max_tokens_spin.setRange(1, 4096)
        self.max_tokens_spin.setValue(150)
        params_layout.addWidget(self.max_tokens_spin, 1, 1)
        
        # Output variable
        params_layout.addWidget(QLabel("Output Variable:"), 2, 0)
        self.output_variable_entry = QLineEdit()
        self.output_variable_entry.setPlaceholderText("llm_output")
        params_layout.addWidget(self.output_variable_entry, 2, 1)
        
        # Write text option
        params_layout.addWidget(QLabel("Write Text:"), 3, 0)
        self.write_text_checkbox = QCheckBox("Automatically write generated text")
        self.write_text_checkbox.setChecked(True)
        params_layout.addWidget(self.write_text_checkbox, 3, 1)
        
        # Typing speed parameters (only visible when write_text is checked)
        params_layout.addWidget(QLabel("Typing Batch Size:"), 4, 0)
        self.typing_batch_size_spin = QSpinBox()
        self.typing_batch_size_spin.setRange(1, 100)
        self.typing_batch_size_spin.setValue(20)
        self.typing_batch_size_spin.setToolTip("Number of characters to type at once (higher = faster typing)")
        params_layout.addWidget(self.typing_batch_size_spin, 4, 1)
        
        params_layout.addWidget(QLabel("Typing Batch Delay:"), 5, 0)
        self.typing_batch_delay_spin = QDoubleSpinBox()
        self.typing_batch_delay_spin.setRange(0.01, 1.0)
        self.typing_batch_delay_spin.setSingleStep(0.01)
        self.typing_batch_delay_spin.setValue(0.05)
        self.typing_batch_delay_spin.setToolTip("Delay between typing batches in seconds (lower = faster typing)")
        params_layout.addWidget(self.typing_batch_delay_spin, 5, 1)
        
        # Connect write_text checkbox to enable/disable typing speed controls
        self.write_text_checkbox.toggled.connect(self.typing_batch_size_spin.setEnabled)
        self.write_text_checkbox.toggled.connect(self.typing_batch_delay_spin.setEnabled)
        
        layout.addWidget(params_group)
        
        # Vision Configuration Group
        vision_group = QGroupBox("Vision Configuration")
        vision_layout = QGridLayout(vision_group)
        vision_layout.setContentsMargins(10, 15, 10, 10)
        vision_layout.setSpacing(10)
        
        # Use vision checkbox
        vision_layout.addWidget(QLabel("Use Vision:"), 0, 0)
        self.use_vision_checkbox = QCheckBox("Enable vision capabilities for this model")
        self.use_vision_checkbox.setChecked(False)
        vision_layout.addWidget(self.use_vision_checkbox, 0, 1)
        
        # Screenshot enabled checkbox
        vision_layout.addWidget(QLabel("Screenshot:"), 1, 0)
        self.screenshot_enabled_checkbox = QCheckBox("Take screenshot automatically for vision models")
        self.screenshot_enabled_checkbox.setChecked(True)
        vision_layout.addWidget(self.screenshot_enabled_checkbox, 1, 1)
        
        # Add help text
        help_label = QLabel("Note: Vision models can analyze screenshots along with text prompts. Enable 'Use Vision' to force vision mode, or leave unchecked for automatic detection.")
        help_label.setWordWrap(True)
        help_label.setStyleSheet("color: #888; font-size: 11px; margin-top: 5px;")
        vision_layout.addWidget(help_label, 2, 0, 1, 2)
        
        layout.addWidget(vision_group)
        
        # ComoRAG Configuration Group
        comorag_group = QGroupBox("Context-Aware Features (ComoRAG)")
        comorag_layout = QGridLayout(comorag_group)
        comorag_layout.setContentsMargins(10, 15, 10, 10)
        comorag_layout.setSpacing(10)
        
        # Enable ComoRAG checkbox
        comorag_layout.addWidget(QLabel("Enable ComoRAG:"), 0, 0)
        self.use_comorag_checkbox = QCheckBox("Use context-aware reasoning with OverWatch")
        self.use_comorag_checkbox.setChecked(True)
        comorag_layout.addWidget(self.use_comorag_checkbox, 0, 1)
        
        # Max reasoning cycles
        comorag_layout.addWidget(QLabel("Max Cycles:"), 1, 0)
        self.max_cycles_spin = QSpinBox()
        self.max_cycles_spin.setRange(1, 10)
        self.max_cycles_spin.setValue(5)
        self.max_cycles_spin.setToolTip("Maximum number of reasoning cycles for ComoRAG")
        comorag_layout.addWidget(self.max_cycles_spin, 1, 1)
        
        # Memory size
        comorag_layout.addWidget(QLabel("Memory Size:"), 2, 0)
        self.memory_size_spin = QSpinBox()
        self.memory_size_spin.setRange(100, 5000)
        self.memory_size_spin.setSingleStep(100)
        self.memory_size_spin.setValue(1000)
        self.memory_size_spin.setToolTip("Memory size for ComoRAG context retention")
        comorag_layout.addWidget(self.memory_size_spin, 2, 1)
        
        # Add help text for ComoRAG
        comorag_help_label = QLabel("ComoRAG enhances LLM responses by providing context-aware reasoning through OverWatch. It automatically includes screenshots, OCR text, and workflow state for better automation decisions.")
        comorag_help_label.setWordWrap(True)
        comorag_help_label.setStyleSheet("color: #888; font-size: 11px; margin-top: 5px;")
        comorag_layout.addWidget(comorag_help_label, 3, 0, 1, 2)
        
        layout.addWidget(comorag_group)
        
        # Dialog buttons
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)
    
    def load_current_config(self):
        """Load current configuration into the dialog"""
        if self.current_config:
            self.model_combo.setCurrentText(self.current_config.get('model', 'llama3.2'))
            self.api_url_entry.setText(self.current_config.get('api_url', get_default_api_url()))
            self.system_message_edit.setPlainText(self.current_config.get('system_message', ''))
            self.prompt_edit.setPlainText(self.current_config.get('prompt', 'Enter your prompt here...'))
            self.temperature_spin.setValue(self.current_config.get('temperature', 0.7))
            self.max_tokens_spin.setValue(self.current_config.get('max_tokens', 150))
            self.output_variable_entry.setText(self.current_config.get('output_variable', 'llm_output'))
            self.write_text_checkbox.setChecked(self.current_config.get('write_text', True))
            self.use_vision_checkbox.setChecked(self.current_config.get('use_vision', False))
            self.screenshot_enabled_checkbox.setChecked(self.current_config.get('screenshot_enabled', True))
            
            # Load typing speed parameters
            self.typing_batch_size_spin.setValue(self.current_config.get('typing_batch_size', 20))
            self.typing_batch_delay_spin.setValue(self.current_config.get('typing_batch_delay', 0.05))
            
            # Enable/disable typing speed controls based on write_text setting
            write_text_enabled = self.current_config.get('write_text', True)
            self.typing_batch_size_spin.setEnabled(write_text_enabled)
            self.typing_batch_delay_spin.setEnabled(write_text_enabled)
            
            # Load ComoRAG configuration
            self.use_comorag_checkbox.setChecked(self.current_config.get('use_comorag', True))
            self.max_cycles_spin.setValue(self.current_config.get('max_cycles', 5))
            self.memory_size_spin.setValue(self.current_config.get('memory_size', 1000))
    
    def get_config(self):
        """Get the LLM configuration from the dialog"""
        return {
            'model': self.model_combo.currentText(),
            'api_url': self.api_url_entry.text() or get_default_api_url(),
            'system_message': self.system_message_edit.toPlainText(),
            'prompt': self.prompt_edit.toPlainText(),
            'temperature': self.temperature_spin.value(),
            'max_tokens': self.max_tokens_spin.value(),
            'output_variable': self.output_variable_entry.text() or 'llm_output',
            'write_text': self.write_text_checkbox.isChecked(),
            'use_vision': self.use_vision_checkbox.isChecked(),
            'screenshot_enabled': self.screenshot_enabled_checkbox.isChecked(),
            
            # Typing speed parameters
            'typing_batch_size': self.typing_batch_size_spin.value(),
            'typing_batch_delay': self.typing_batch_delay_spin.value(),
            
            # ComoRAG configuration
            'use_comorag': self.use_comorag_checkbox.isChecked(),
            'max_cycles': self.max_cycles_spin.value(),
            'memory_size': self.memory_size_spin.value()
        }