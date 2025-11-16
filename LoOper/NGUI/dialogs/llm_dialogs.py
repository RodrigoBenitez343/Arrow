"""LLM-related dialog classes for the LoOper application.

This module contains the LLMPropertiesDialog class for configuring
LLM (Large Language Model) node properties including model selection,
prompt configuration, generation parameters, and vision capabilities.
"""

import sys
import os
import json
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QLineEdit, QPushButton, QHBoxLayout, 
    QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox, QWidget, QFileDialog,
    QTabWidget, QListWidgetItem, QTextEdit, QGroupBox, QListWidget,
    QDialogButtonBox, QGridLayout, QRadioButton, QButtonGroup
)
from PyQt5.QtCore import Qt
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
        self.resize(600, 500)  # Increased height for tabbed interface
        self.setMinimumSize(600, 500)
        
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
        cache = get_model_cache()
        if api_url:
            cache.refresh_models(api_url)
        else:
            from .utils import get_default_api_url
            cache.refresh_models(get_default_api_url())
        models = get_cached_models(timeout=5.0)
        return models
    
    def refresh_models(self):
        api_url = self.api_url_entry.text() or get_default_api_url()
        current_model = self.model_combo.currentText()
        cache = get_model_cache()
        cache.refresh_models(api_url)
        available_models = get_cached_models(timeout=5.0)
        
        self.model_combo.clear()
        self.model_combo.addItems(available_models)
        
        # Restore selection if it still exists in the new list
        if current_model in available_models:
            self.model_combo.setCurrentText(current_model)
    
    def setup_ui(self):
        """Setup the UI for the dialog with tabbed interface"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        
        # Create tab widget
        self.tab_widget = QTabWidget()
        self.tab_widget.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #555;
                background-color: #2b2b2b;
            }
            QTabBar::tab {
                background-color: #3c3c3c;
                color: #ffffff;
                padding: 8px 16px;
                margin-right: 2px;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
            }
            QTabBar::tab:selected {
                background-color: #4a4a4a;
                border-bottom: 2px solid #0078d4;
            }
            QTabBar::tab:hover {
                background-color: #404040;
            }
        """)
        
        # Create tabs
        self.create_basic_tab()
        self.create_prompt_tab()
        self.create_advanced_tab()
        self.create_features_tab()
        
        layout.addWidget(self.tab_widget)
        
        # Dialog buttons
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)
    
    def create_basic_tab(self):
        """Create the Basic Configuration tab"""
        basic_tab = QWidget()
        layout = QVBoxLayout(basic_tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        
        # Model Configuration Group
        model_group = QGroupBox("Model Configuration")
        model_layout = QGridLayout(model_group)
        model_layout.setContentsMargins(8, 12, 8, 8)
        model_layout.setSpacing(6)
        
        # Model selection
        model_layout.addWidget(QLabel("Model:"), 0, 0)
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        
        # Get API URL from current config or use default
        api_url = self.current_config.get('api_url', get_default_api_url())
        
        # Force model refresh to ensure we get real models, not placeholders
        cache = get_model_cache()
        if not cache.is_loaded():
            print(f"Models not loaded, refreshing from {api_url}")
            cache.refresh_models(api_url)
            
        available_models = self.get_available_models(api_url)
        
        # If we still get fallback models, show a warning
        if available_models == ["llama3.2", "llama3.1", "codellama", "mistral", "phi3"]:
            print(f"Warning: Using fallback models. Check API connection to {api_url}")
        
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
        
        # Generation Parameters Group
        params_group = QGroupBox("Generation Parameters")
        params_layout = QGridLayout(params_group)
        params_layout.setContentsMargins(8, 12, 8, 8)
        params_layout.setSpacing(6)
        
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
        
        layout.addWidget(params_group)
        
        # Template Selection Group
        template_group = QGroupBox("Quick Templates")
        template_layout = QVBoxLayout(template_group)
        template_layout.setContentsMargins(8, 12, 8, 8)
        template_layout.setSpacing(6)
        
        # Template selection combo
        template_layout.addWidget(QLabel("Apply Template:"))
        self.template_combo = QComboBox()
        self.template_combo.addItems([
            "Custom (no template)",
            "Information Extraction (OCR + LLM)",
            "Custom Instructions",
            "Direct Prompt",
            "ComoRAG Analysis",
            "Data Processing Chain",
            "Text Analysis",
            "Content Summarization"
        ])
        self.template_combo.currentTextChanged.connect(self.apply_template)
        template_layout.addWidget(self.template_combo)
        
        # Template description
        self.template_description = QLabel()
        self.template_description.setWordWrap(True)
        self.template_description.setStyleSheet("color: #888; font-size: 10px; margin-top: 3px;")
        template_layout.addWidget(self.template_description)
        
        layout.addWidget(template_group)
        
        layout.addStretch()
        self.tab_widget.addTab(basic_tab, "Basic")
    
    def create_prompt_tab(self):
        """Create the Prompt Configuration tab"""
        prompt_tab = QWidget()
        layout = QVBoxLayout(prompt_tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        
        # Prompt Configuration Group
        prompt_group = QGroupBox("Prompt Configuration")
        prompt_layout = QVBoxLayout(prompt_group)
        prompt_layout.setContentsMargins(8, 12, 8, 8)
        prompt_layout.setSpacing(6)
        
        # System message
        prompt_layout.addWidget(QLabel("System Message (optional):"))
        self.system_message_edit = QTextEdit()
        self.system_message_edit.setMaximumHeight(100)
        self.system_message_edit.setPlaceholderText("Enter system instructions for the AI model...")
        prompt_layout.addWidget(self.system_message_edit)
        
        # User prompt
        prompt_layout.addWidget(QLabel("Prompt:"))
        self.prompt_edit = QTextEdit()
        self.prompt_edit.setMaximumHeight(120)
        self.prompt_edit.setPlaceholderText("Enter your prompt here...")
        prompt_layout.addWidget(self.prompt_edit)
        
        layout.addWidget(prompt_group)
        
        # Input Source Configuration Group
        input_source_group = QGroupBox("Input Source")
        input_source_layout = QVBoxLayout(input_source_group)
        input_source_layout.setContentsMargins(8, 12, 8, 8)
        input_source_layout.setSpacing(6)
        
        # Input source radio buttons
        self.input_source_group = QButtonGroup(self)
        self.input_none_radio = QRadioButton("No input (direct prompt)")
        self.input_none_radio.setChecked(True)
        self.input_source_group.addButton(self.input_none_radio, 0)
        
        self.input_ocr_radio = QRadioButton("Extract text from screen (OCR)")
        self.input_source_group.addButton(self.input_ocr_radio, 1)
        
        self.input_previous_radio = QRadioButton("Use output from previous node")
        self.input_source_group.addButton(self.input_previous_radio, 2)
        
        self.input_clipboard_radio = QRadioButton("Use clipboard content")
        self.input_source_group.addButton(self.input_clipboard_radio, 3)
        
        input_source_layout.addWidget(self.input_none_radio)
        input_source_layout.addWidget(self.input_ocr_radio)
        input_source_layout.addWidget(self.input_previous_radio)
        input_source_layout.addWidget(self.input_clipboard_radio)
        
        # OCR Configuration (visible when OCR is selected)
        self.ocr_config_widget = QWidget()
        ocr_config_layout = QGridLayout(self.ocr_config_widget)
        ocr_config_layout.setContentsMargins(0, 8, 0, 0)
        ocr_config_layout.setSpacing(6)
        
        # OCR confidence threshold
        ocr_config_layout.addWidget(QLabel("OCR Confidence:"), 0, 0)
        self.ocr_confidence_spin = QDoubleSpinBox()
        self.ocr_confidence_spin.setRange(0.1, 1.0)
        self.ocr_confidence_spin.setSingleStep(0.1)
        self.ocr_confidence_spin.setValue(0.7)
        self.ocr_confidence_spin.setToolTip("Minimum confidence for OCR text extraction")
        ocr_config_layout.addWidget(self.ocr_confidence_spin, 0, 1)
        
        # OCR text preprocessing
        ocr_config_layout.addWidget(QLabel("Text Preprocessing:"), 1, 0)
        self.ocr_preprocessing_combo = QComboBox()
        self.ocr_preprocessing_combo.addItems(["None", "Remove extra spaces", "Clean formatting", "Extract structured data"])
        ocr_config_layout.addWidget(self.ocr_preprocessing_combo, 1, 1)
        
        input_source_layout.addWidget(self.ocr_config_widget)
        
        layout.addWidget(input_source_group)
        
        layout.addStretch()
        self.tab_widget.addTab(prompt_tab, "Prompt")
    
    def create_advanced_tab(self):
        """Create the Advanced Configuration tab"""
        advanced_tab = QWidget()
        layout = QVBoxLayout(advanced_tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        
        # Output Configuration Group
        output_group = QGroupBox("Output Configuration")
        output_layout = QGridLayout(output_group)
        output_layout.setContentsMargins(8, 12, 8, 8)
        output_layout.setSpacing(6)
        
        # Write text option
        output_layout.addWidget(QLabel("Write Text:"), 0, 0)
        self.write_text_checkbox = QCheckBox("Automatically write generated text")
        self.write_text_checkbox.setChecked(True)
        output_layout.addWidget(self.write_text_checkbox, 0, 1)
        
        # Typing speed parameters (only visible when write_text is checked)
        output_layout.addWidget(QLabel("Typing Batch Size:"), 1, 0)
        self.typing_batch_size_spin = QSpinBox()
        self.typing_batch_size_spin.setRange(1, 100)
        self.typing_batch_size_spin.setValue(20)
        self.typing_batch_size_spin.setToolTip("Number of characters to type at once (higher = faster typing)")
        output_layout.addWidget(self.typing_batch_size_spin, 1, 1)
        
        output_layout.addWidget(QLabel("Typing Batch Delay:"), 2, 0)
        self.typing_batch_delay_spin = QDoubleSpinBox()
        self.typing_batch_delay_spin.setRange(0.01, 1.0)
        self.typing_batch_delay_spin.setSingleStep(0.01)
        self.typing_batch_delay_spin.setValue(0.05)
        self.typing_batch_delay_spin.setToolTip("Delay between typing batches in seconds (lower = faster typing)")
        output_layout.addWidget(self.typing_batch_delay_spin, 2, 1)
        
        # Connect write_text checkbox to enable/disable typing speed controls
        self.write_text_checkbox.toggled.connect(self.typing_batch_size_spin.setEnabled)
        self.write_text_checkbox.toggled.connect(self.typing_batch_delay_spin.setEnabled)
        
        layout.addWidget(output_group)
        
        # Async Processing Group
        async_group = QGroupBox("Async Processing")
        async_layout = QGridLayout(async_group)
        async_layout.setContentsMargins(8, 12, 8, 8)
        async_layout.setSpacing(6)
        
        # Use async checkbox
        async_layout.addWidget(QLabel("Enable Async:"), 0, 0)
        self.use_async_checkbox = QCheckBox("Process requests asynchronously")
        self.use_async_checkbox.setChecked(True)
        self.use_async_checkbox.setToolTip("Enable asynchronous processing for better performance")
        async_layout.addWidget(self.use_async_checkbox, 0, 1)
        
        # Add help text for async
        async_help_label = QLabel("Async processing allows the workflow to continue while waiting for LLM responses.")
        async_help_label.setWordWrap(True)
        async_help_label.setStyleSheet("color: #888; font-size: 10px; margin-top: 3px;")
        async_layout.addWidget(async_help_label, 1, 0, 1, 2)
        
        layout.addWidget(async_group)
        
        layout.addStretch()
        self.tab_widget.addTab(advanced_tab, "Advanced")
    
    def create_features_tab(self):
        """Create the Features Configuration tab"""
        features_tab = QWidget()
        layout = QVBoxLayout(features_tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        
        # Vision Configuration Group
        vision_group = QGroupBox("Vision Configuration")
        vision_layout = QGridLayout(vision_group)
        vision_layout.setContentsMargins(8, 12, 8, 8)
        vision_layout.setSpacing(6)
        
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
        help_label = QLabel("Note: Vision models can analyze screenshots along with text prompts.")
        help_label.setWordWrap(True)
        help_label.setStyleSheet("color: #888; font-size: 10px; margin-top: 3px;")
        vision_layout.addWidget(help_label, 2, 0, 1, 2)
        
        layout.addWidget(vision_group)
        
        # ComoRAG Configuration Group
        comorag_group = QGroupBox("Context-Aware Features (ComoRAG)")
        comorag_layout = QGridLayout(comorag_group)
        comorag_layout.setContentsMargins(8, 12, 8, 8)
        comorag_layout.setSpacing(6)
        
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
        comorag_help_label = QLabel("ComoRAG enhances LLM responses with context-aware reasoning and connects to OverWatch API.")
        comorag_help_label.setWordWrap(True)
        comorag_help_label.setStyleSheet("color: #888; font-size: 10px; margin-top: 3px;")
        comorag_layout.addWidget(comorag_help_label, 3, 0, 1, 2)
        
        layout.addWidget(comorag_group)
        
        layout.addStretch()
        self.tab_widget.addTab(features_tab, "Features")
        
        # Connect signals for UI elements
        self.setup_signal_connections()
    
    def setup_signal_connections(self):
        """Setup signal connections for UI elements"""
        # Input source changes
        self.input_source_group.buttonClicked.connect(self.update_input_source_visibility)
        self.input_source_group.buttonClicked.connect(self.update_template_description)
        
        # Template selection
        self.template_combo.currentTextChanged.connect(self.apply_template)
        self.template_combo.currentTextChanged.connect(self.update_template_description)
    
    def create_templates_tab(self):
        """Create the Templates Configuration tab"""
        templates_tab = QWidget()
        layout = QVBoxLayout(templates_tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        
        # Template Selection Group
        template_group = QGroupBox("Quick Templates")
        template_layout = QVBoxLayout(template_group)
        template_layout.setContentsMargins(8, 12, 8, 8)
        template_layout.setSpacing(6)
        
        # Template selection combo
        template_layout.addWidget(QLabel("Apply Template:"))
        self.template_combo = QComboBox()
        self.template_combo.addItems([
            "Custom (no template)",
            "Information Extraction (OCR + LLM)",
            "Custom Instructions",
            "Direct Prompt",
            "ComoRAG Analysis",
            "Data Processing Chain",
            "Text Analysis",
            "Content Summarization"
        ])
        self.template_combo.currentTextChanged.connect(self.apply_template)
        template_layout.addWidget(self.template_combo)
        
        # Template description
        self.template_description = QLabel()
        self.template_description.setWordWrap(True)
        self.template_description.setStyleSheet("color: #888; font-size: 10px; margin-top: 3px;")
        template_layout.addWidget(self.template_description)
        
        layout.addWidget(template_group)
        
        layout.addStretch()
        self.tab_widget.addTab(templates_tab, "Templates")
        
        # Add dialog buttons after all tabs are created
        self.create_dialog_buttons()
    
    def create_dialog_buttons(self):
        """Create dialog buttons at the bottom"""
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        
        # Add buttons to main layout
        self.main_layout.addWidget(button_box)
    
    def load_current_config(self):
        """Load current configuration into the dialog"""
        if self.current_config:
            # Basic tab settings
            self.model_combo.setCurrentText(self.current_config.get('model', 'llama3.2'))
            self.api_url_entry.setText(self.current_config.get('api_url', get_default_api_url()))
            self.temperature_spin.setValue(self.current_config.get('temperature', 0.7))
            self.max_tokens_spin.setValue(self.current_config.get('max_tokens', 150))
            self.output_variable_entry.setText(self.current_config.get('output_variable', 'llm_output'))
            
            # Prompt tab settings
            self.system_message_edit.setPlainText(self.current_config.get('system_message', ''))
            self.prompt_edit.setPlainText(self.current_config.get('prompt', 'Enter your prompt here...'))
            
            # Input source configuration
            input_source = self.current_config.get('input_source', 0)
            button = self.input_source_group.button(input_source)
            if button:
                button.setChecked(True)
            self.ocr_confidence_spin.setValue(self.current_config.get('ocr_confidence', 0.7))
            
            ocr_preprocessing = self.current_config.get('ocr_preprocessing', 'None')
            index = self.ocr_preprocessing_combo.findText(ocr_preprocessing)
            if index >= 0:
                self.ocr_preprocessing_combo.setCurrentIndex(index)
            
            # Advanced tab settings
            self.write_text_checkbox.setChecked(self.current_config.get('write_text', True))
            self.typing_batch_size_spin.setValue(self.current_config.get('typing_batch_size', 20))
            self.typing_batch_delay_spin.setValue(self.current_config.get('typing_batch_delay', 0.05))
            self.use_async_checkbox.setChecked(self.current_config.get('use_async', True))
            
            # Enable/disable typing speed controls based on write_text setting
            write_text_enabled = self.current_config.get('write_text', True)
            self.typing_batch_size_spin.setEnabled(write_text_enabled)
            self.typing_batch_delay_spin.setEnabled(write_text_enabled)
            
            # Features tab settings
            self.use_vision_checkbox.setChecked(self.current_config.get('use_vision', False))
            self.screenshot_enabled_checkbox.setChecked(self.current_config.get('screenshot_enabled', True))
            self.use_comorag_checkbox.setChecked(self.current_config.get('use_comorag', True))
            self.max_cycles_spin.setValue(self.current_config.get('max_cycles', 5))
            self.memory_size_spin.setValue(self.current_config.get('memory_size', 1000))
            
            # Load template
            template_applied = self.current_config.get('template_applied', 'Custom (no template)')
            index = self.template_combo.findText(template_applied)
            if index >= 0:
                self.template_combo.setCurrentIndex(index)
            
            # Update visibility based on input source
        self.update_input_source_visibility()
    
    def apply_template(self, template_name):
        """Apply a predefined template configuration"""
        templates = {
            "Information Extraction (OCR + LLM)": {
                "system_message": "You are an AI assistant that extracts specific information from text. Follow the user's instructions carefully and provide only the requested information.",
                "prompt": "Extract the following information from the provided text: [SPECIFY WHAT TO EXTRACT]. Return only the extracted information, nothing else.",
                "temperature": 0.1,
                "max_tokens": 500,
                "input_source": 1,  # OCR
                "ocr_confidence": 0.8,
                "ocr_preprocessing": "Enhance contrast",
                "output_variable": "extracted_info"
            },
            "Custom Instructions": {
                "system_message": "You are a helpful AI assistant. Follow the user's instructions carefully.",
                "prompt": "[ENTER YOUR CUSTOM INSTRUCTIONS HERE]",
                "temperature": 0.7,
                "max_tokens": 1000,
                "input_source": 0,  # No input
                "output_variable": "llm_output"
            },
            "ComoRAG Analysis": {
                "system_message": "You are an AI assistant with memory and context awareness. Use the provided context to give informed responses.",
                "prompt": "Analyze the following information and provide insights based on the context: [DESCRIBE ANALYSIS GOAL]",
                "temperature": 0.3,
                "max_tokens": 800,
                "input_source": 2,  # Previous node output
                "use_comorag": True,
                "max_cycles": 3,
                "memory_size": 2000,
                "output_variable": "analysis_result"
            },
            "Data Processing Chain": {
                "system_message": "You are a data processing assistant. Process the input data according to the specified requirements.",
                "prompt": "Process the input data as follows: [DESCRIBE PROCESSING STEPS]",
                "temperature": 0.2,
                "max_tokens": 1500,
                "input_source": 2,  # Previous node output
                "output_variable": "processed_data"
            },
            "Text Analysis": {
                "system_message": "You are a text analysis expert. Analyze the provided text thoroughly.",
                "prompt": "Analyze the following text and provide: 1) Main themes, 2) Key points, 3) Sentiment, 4) Actionable insights: [DESCRIBE ANALYSIS FOCUS]",
                "temperature": 0.4,
                "max_tokens": 1200,
                "input_source": 3,  # Clipboard
                "output_variable": "text_analysis"
            },
            "Content Summarization": {
                "system_message": "You are a content summarization expert. Create concise, accurate summaries.",
                "prompt": "Summarize the following content in [SPECIFY LENGTH] focusing on [SPECIFY FOCUS AREAS]: ",
                "temperature": 0.3,
                "max_tokens": 800,
                "input_source": 1,  # OCR
                "ocr_confidence": 0.7,
                "output_variable": "summary"
            }
        }
        
        if template_name in templates:
            self.apply_template_config(templates[template_name])
    
    def apply_template_config(self, config):
        """Apply template configuration to dialog"""
        if 'system_message' in config:
            self.system_message_edit.setPlainText(config['system_message'])
        if 'prompt' in config:
            self.prompt_edit.setPlainText(config['prompt'])
        if 'temperature' in config:
            self.temperature_spin.setValue(config['temperature'])
        if 'max_tokens' in config:
            self.max_tokens_spin.setValue(config['max_tokens'])
        if 'output_variable' in config:
            self.output_variable_entry.setText(config['output_variable'])
        if 'input_source' in config:
            button = self.input_source_group.button(config['input_source'])
            if button:
                button.setChecked(True)
        if 'ocr_confidence' in config:
            self.ocr_confidence_spin.setValue(config['ocr_confidence'])
        if 'ocr_preprocessing' in config:
            index = self.ocr_preprocessing_combo.findText(config['ocr_preprocessing'])
            if index >= 0:
                self.ocr_preprocessing_combo.setCurrentIndex(index)
        if 'use_comorag' in config:
            self.use_comorag_checkbox.setChecked(config['use_comorag'])
        if 'max_cycles' in config:
            self.max_cycles_spin.setValue(config['max_cycles'])
        if 'memory_size' in config:
            self.memory_size_spin.setValue(config['memory_size'])
        
        self.update_input_source_visibility()
    
    def update_input_source_visibility(self):
        """Update UI visibility based on selected input source"""
        selected_button = self.input_source_group.checkedButton()
        if not selected_button:
            return
        
        input_source = self.input_source_group.id(selected_button)
        
        # Show/hide OCR configuration
        ocr_visible = (input_source == 1)  # OCR
        self.ocr_confidence_label.setVisible(ocr_visible)
        self.ocr_confidence_spin.setVisible(ocr_visible)
        self.ocr_preprocessing_label.setVisible(ocr_visible)
        self.ocr_preprocessing_combo.setVisible(ocr_visible)
        
        # Update prompt placeholder based on input source
        if input_source == 0:  # No input
            self.prompt_edit.setPlaceholderText("Enter your prompt here...")
        elif input_source == 1:  # OCR
            self.prompt_edit.setPlaceholderText("Describe what to extract/analyze from the screen text...")
        elif input_source == 2:  # Previous node output
            self.prompt_edit.setPlaceholderText("Process the previous node's output...")
        elif input_source == 3:  # Clipboard
            self.prompt_edit.setPlaceholderText("Process the clipboard content...")
    
    def update_template_description(self):
        """Update template description based on current selection"""
        template_name = self.template_combo.currentText()
        descriptions = {
            "Custom (no template)": "Use custom configuration without predefined template.",
            "Information Extraction (OCR + LLM)": "Extract specific information from screen text using OCR + LLM.",
            "Custom Instructions": "Define your own system message and prompt for the LLM.",
            "Direct Prompt": "Simple direct prompt without additional configuration.",
            "ComoRAG Analysis": "Use ComoRAG for context-aware analysis with memory and reasoning.",
            "Data Processing Chain": "Process data from previous nodes in a multi-step workflow.",
            "Text Analysis": "Analyze text content from clipboard with detailed insights.",
            "Content Summarization": "Summarize content extracted from screen using OCR."
        }
        self.template_description.setText(descriptions.get(template_name, ""))
      
    def get_config(self):
        """Get the LLM configuration from the dialog"""
        config = {
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
        
        # Add input source configuration
        selected_button = self.input_source_group.checkedButton()
        if selected_button:
            config['input_source'] = self.input_source_group.id(selected_button)
        else:
            config['input_source'] = 0
            
        config['ocr_confidence'] = self.ocr_confidence_spin.value()
        config['ocr_preprocessing'] = self.ocr_preprocessing_combo.currentText()
        config['template_applied'] = self.template_combo.currentText()
        
        return config
    
    def apply_template(self, template_name):
        """Apply a predefined template to configure the LLM node"""
        templates = {
            "Information Extraction (OCR + LLM)": {
                "description": "Extract specific information from screen text using OCR and LLM",
                "input_source": 1,  # OCR
                "system_message": "You are an expert at extracting specific information from text. Extract the requested information accurately and concisely.",
                "prompt": "Extract the following information from the text: [SPECIFY WHAT TO EXTRACT]",
                "temperature": 0.3,
                "max_tokens": 200,
                "use_comorag": False,
                "ocr_confidence": 0.7,
                "ocr_preprocessing": "Clean formatting"
            },
            "Custom Instructions": {
                "description": "Provide custom instructions for the LLM to follow",
                "input_source": 0,  # No input
                "system_message": "You are a helpful assistant that follows instructions precisely.",
                "prompt": "[ENTER YOUR CUSTOM INSTRUCTIONS HERE]",
                "temperature": 0.7,
                "max_tokens": 500,
                "use_comorag": False
            },
            "Direct Prompt": {
                "description": "Simple direct prompt without any special configuration",
                "input_source": 0,  # No input
                "system_message": "",
                "prompt": "[ENTER YOUR PROMPT HERE]",
                "temperature": 0.7,
                "max_tokens": 150,
                "use_comorag": False
            },
            "ComoRAG Analysis": {
                "description": "Use ComoRAG for context-aware analysis and reasoning",
                "input_source": 0,  # No input
                "system_message": "You are an intelligent assistant with access to context-aware reasoning. Analyze the request and provide comprehensive insights.",
                "prompt": "[ENTER YOUR ANALYSIS REQUEST]",
                "temperature": 0.5,
                "max_tokens": 1000,
                "use_comorag": True,
                "max_cycles": 5,
                "memory_size": 1000
            },
            "Data Processing Chain": {
                "description": "Process data from previous node for multi-step automation",
                "input_source": 2,  # Previous node
                "system_message": "You are a data processing node in an automation chain. Process the input data according to the specified requirements.",
                "prompt": "Process the input data to: [SPECIFY PROCESSING REQUIREMENTS]",
                "temperature": 0.3,
                "max_tokens": 300,
                "use_comorag": False
            },
            "Text Analysis": {
                "description": "Analyze text content with OCR input",
                "input_source": 1,  # OCR
                "system_message": "You are a text analysis expert. Analyze the provided text and extract insights, patterns, or requested information.",
                "prompt": "Analyze the text and provide: [SPECIFY ANALYSIS REQUIREMENTS]",
                "temperature": 0.4,
                "max_tokens": 400,
                "use_comorag": False,
                "ocr_confidence": 0.6,
                "ocr_preprocessing": "Remove extra spaces"
            },
            "Content Summarization": {
                "description": "Summarize content from screen or previous node",
                "input_source": 1,  # OCR (can be changed to 2 for previous node)
                "system_message": "You are an expert at creating concise, accurate summaries. Summarize the content clearly and effectively.",
                "prompt": "Summarize the key points from the content. Focus on the most important information.",
                "temperature": 0.3,
                "max_tokens": 250,
                "use_comorag": False,
                "ocr_confidence": 0.6,
                "ocr_preprocessing": "Clean formatting"
            }
        }
        
        # Update description
        if template_name in templates:
            template = templates[template_name]
            self.template_description.setText(template["description"])
            
            # Only apply template if it's not the "Custom" option
            if template_name != "Custom (no template)":
                self.apply_template_config(template)
        else:
            self.template_description.setText("")
    
    def apply_template_config(self, template):
        """Apply the template configuration to the dialog"""
        # Input source
        if "input_source" in template:
            input_source_id = template["input_source"]
            button = self.input_source_group.button(input_source_id)
            if button:
                button.setChecked(True)
                self.update_input_source_visibility()
        
        # System message
        if "system_message" in template:
            self.system_message_edit.setPlainText(template["system_message"])
        
        # Prompt
        if "prompt" in template:
            self.prompt_edit.setPlainText(template["prompt"])
        
        # Temperature
        if "temperature" in template:
            self.temperature_spin.setValue(template["temperature"])
        
        # Max tokens
        if "max_tokens" in template:
            self.max_tokens_spin.setValue(template["max_tokens"])
        
        # ComoRAG settings
        if "use_comorag" in template:
            self.use_comorag_checkbox.setChecked(template["use_comorag"])
        
        if "max_cycles" in template:
            self.max_cycles_spin.setValue(template["max_cycles"])
        
        if "memory_size" in template:
            self.memory_size_spin.setValue(template["memory_size"])
        
        # OCR settings
        if "ocr_confidence" in template:
            self.ocr_confidence_spin.setValue(template["ocr_confidence"])
        
        if "ocr_preprocessing" in template:
            index = self.ocr_preprocessing_combo.findText(template["ocr_preprocessing"])
            if index >= 0:
                self.ocr_preprocessing_combo.setCurrentIndex(index)
    
    def update_input_source_visibility(self):
        """Update visibility of input source configuration widgets"""
        input_source_id = self.input_source_group.checkedId()
        
        # Show/hide OCR configuration
        self.ocr_config_widget.setVisible(input_source_id == 1)  # OCR
        
        # Update prompt placeholder based on input source
        if input_source_id == 1:  # OCR
            if "Extract" not in self.prompt_edit.toPlainText():
                self.prompt_edit.setPlaceholderText("Enter instructions for processing the extracted text...")
        elif input_source_id == 2:  # Previous node
            self.prompt_edit.setPlaceholderText("Enter instructions for processing the input from previous node...")
        elif input_source_id == 3:  # Clipboard
            self.prompt_edit.setPlaceholderText("Enter instructions for processing the clipboard content...")
        else:  # No input
            self.prompt_edit.setPlaceholderText("Enter your prompt here...")
