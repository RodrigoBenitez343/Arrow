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
    QComboBox, QSpinBox, QDoubleSpinBox, QWidget, QFileDialog,
    QTabWidget, QListWidgetItem, QTextEdit, QGroupBox, QListWidget,
    QDialogButtonBox, QGridLayout, QRadioButton, QButtonGroup, QInputDialog
)
from PyQt5.QtCore import Qt
from ..constants import TEXT_COLOR
from .utils import get_default_api_url
from ..i18n import _
from .base_dialog import ModernDialog
from .toggle_switch import ModernToggle

# Add the parent directory to sys.path to import AI modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from AI.consult import OllamaClient
from AI.model_cache import get_cached_models, get_model_cache, get_real_cached_models


class LLMPropertiesDialog(ModernDialog):
    """Dialog for configuring LLM node properties"""
    
    def __init__(self, parent, current_config=None):
        super().__init__(parent, title=_("LLM Node Configuration"))
        self.current_config = current_config or {}
        
        self.setModal(True)
        self.resize(600, 500)  # Increased height for tabbed interface
        self.setMinimumSize(600, 500)
        
        # Style logic moved to ModernDialog, but we can keep specific overrides if needed
        # or rely on ModernDialog's theme.
        
        self.setup_ui()
        self.load_current_config()
    
    def get_available_models(self):
        # Return models from cache only; do not trigger API calls here
        return get_real_cached_models(timeout=5.0)
    
    def refresh_models(self):
        api_url = self.api_url_entry.text() or get_default_api_url()
        current_model = self.model_combo.currentText()
        cache = get_model_cache()
        cache.refresh_models(api_url)
        available_models = get_real_cached_models(timeout=5.0)

        self.model_combo.clear()
        self.model_combo.addItems(available_models)

        # Restore selection if it still exists in the new list
        if current_model in available_models:
            self.model_combo.setCurrentText(current_model)
    
    def setup_ui(self):
        # Use content_layout from ModernDialog instead of creating a new layout on self
        layout = self.content_layout
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        self.tab_widget = QTabWidget()
        # Stylesheet removed as ModernDialog handles it
        self.create_basic_tab()
        self.create_prompt_tab()
        self.create_advanced_tab()
        self.create_features_tab()
        self.create_rag_tab()
        self.create_tools_tab()
        layout.addWidget(self.tab_widget)
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        ok_btn = button_box.button(QDialogButtonBox.Ok)
        cancel_btn = button_box.button(QDialogButtonBox.Cancel)
        if ok_btn:
            ok_btn.setText(_("OK"))
        if cancel_btn:
            cancel_btn.setText(_("Cancel"))
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
        model_group = QGroupBox(_("Model Configuration"))
        model_layout = QGridLayout(model_group)
        model_layout.setContentsMargins(8, 12, 8, 8)
        model_layout.setSpacing(6)
        
        # Model selection
        model_layout.addWidget(QLabel(_("Model:")), 0, 0)
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        
        # Populate model list from cache only (no API calls on dialog open)
        available_models = self.get_available_models()
        self.model_combo.addItems(available_models)
        
        model_layout.addWidget(self.model_combo, 0, 1)
        
        # API URL
        model_layout.addWidget(QLabel(_("API URL:")), 1, 0)
        api_url_layout = QHBoxLayout()
        self.api_url_entry = QLineEdit()
        self.api_url_entry.setPlaceholderText(get_default_api_url())
        api_url_layout.addWidget(self.api_url_entry)
        
        # Refresh models button
        refresh_btn = QPushButton(_("Refresh Models"))
        refresh_btn.clicked.connect(self.refresh_models)
        api_url_layout.addWidget(refresh_btn)
        
        model_layout.addLayout(api_url_layout, 1, 1)
        
        layout.addWidget(model_group)
        
        # Generation Parameters Group
        params_group = QGroupBox(_("Generation Parameters"))
        params_layout = QGridLayout(params_group)
        params_layout.setContentsMargins(8, 12, 8, 8)
        params_layout.setSpacing(6)
        
        # Temperature
        params_layout.addWidget(QLabel(_("Temperature:")), 0, 0)
        self.temperature_spin = QDoubleSpinBox()
        self.temperature_spin.setRange(0.0, 2.0)
        self.temperature_spin.setSingleStep(0.1)
        self.temperature_spin.setValue(0.7)
        params_layout.addWidget(self.temperature_spin, 0, 1)
        
        # Max tokens
        params_layout.addWidget(QLabel(_("Max Tokens:")), 1, 0)
        self.max_tokens_spin = QSpinBox()
        self.max_tokens_spin.setRange(1, 4096)
        self.max_tokens_spin.setValue(4096)
        params_layout.addWidget(self.max_tokens_spin, 1, 1)
        
        # Output variable
        params_layout.addWidget(QLabel(_("Output Variable:")), 2, 0)
        self.output_variable_entry = QLineEdit()
        self.output_variable_entry.setPlaceholderText("llm_output")
        params_layout.addWidget(self.output_variable_entry, 2, 1)
        
        layout.addWidget(params_group)
        
        # Template Selection Group
        template_group = QGroupBox(_("Quick Templates"))
        template_layout = QVBoxLayout(template_group)
        template_layout.setContentsMargins(8, 12, 8, 8)
        template_layout.setSpacing(6)
        
        # Template selection combo
        template_layout.addWidget(QLabel(_("Apply Template:")))
        self.template_combo = QComboBox()
        self.template_combo.addItems([
            "Custom (no template)",
            "Information Extraction (OCR + LLM)",
            "Custom Instructions",
            "Direct Prompt",
            # "ComoRAG Analysis",
            "Data Processing Chain",
            "Text Analysis",
            "Content Summarization",
            "Tool Selection (LLM Tools)",
            "Python Code Generation"
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
        self.tab_widget.addTab(basic_tab, _("Basic"))
    
    def create_prompt_tab(self):
        """Create the Prompt Configuration tab"""
        prompt_tab = QWidget()
        layout = QVBoxLayout(prompt_tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        
        # Prompt Configuration Group
        prompt_group = QGroupBox(_("Prompt Configuration"))
        prompt_layout = QVBoxLayout(prompt_group)
        prompt_layout.setContentsMargins(8, 12, 8, 8)
        prompt_layout.setSpacing(6)
        
        # System message
        prompt_layout.addWidget(QLabel(_("System Message (optional):")))
        self.system_message_edit = QTextEdit()
        self.system_message_edit.setMaximumHeight(100)
        self.system_message_edit.setPlaceholderText(_("Enter system instructions for the AI model..."))
        prompt_layout.addWidget(self.system_message_edit)
        
        # User prompt
        prompt_layout.addWidget(QLabel(_("Prompt:")))
        self.prompt_edit = QTextEdit()
        self.prompt_edit.setMaximumHeight(120)
        self.prompt_edit.setPlaceholderText(_("Enter your prompt here..."))
        prompt_layout.addWidget(self.prompt_edit)
        
        layout.addWidget(prompt_group)
        
        # Input Source Configuration Group
        input_source_group = QGroupBox(_("Input Source"))
        input_source_layout = QVBoxLayout(input_source_group)
        input_source_layout.setContentsMargins(8, 12, 8, 8)
        input_source_layout.setSpacing(6)
        
        # Input source radio buttons
        self.input_source_group = QButtonGroup(self)
        self.input_none_radio = QRadioButton(_("No input (direct prompt)"))
        self.input_none_radio.setChecked(True)
        self.input_source_group.addButton(self.input_none_radio, 0)
        
        self.input_ocr_radio = QRadioButton(_("Extract text from screen (OCR)"))
        self.input_source_group.addButton(self.input_ocr_radio, 1)
        
        self.input_previous_radio = QRadioButton(_("Use output from previous node"))
        self.input_source_group.addButton(self.input_previous_radio, 2)
        
        self.input_clipboard_radio = QRadioButton(_("Use clipboard content"))
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
        ocr_config_layout.addWidget(QLabel(_("OCR Confidence:")), 0, 0)
        self.ocr_confidence_spin = QDoubleSpinBox()
        self.ocr_confidence_spin.setRange(0.1, 1.0)
        self.ocr_confidence_spin.setSingleStep(0.1)
        self.ocr_confidence_spin.setValue(0.7)
        self.ocr_confidence_spin.setToolTip(_("Minimum confidence for OCR text extraction"))
        ocr_config_layout.addWidget(self.ocr_confidence_spin, 0, 1)
        
        # OCR text preprocessing
        ocr_config_layout.addWidget(QLabel(_("Text Preprocessing:")), 1, 0)
        self.ocr_preprocessing_combo = QComboBox()
        self.ocr_preprocessing_combo.addItems([_("None"), _("Remove extra spaces"), _("Clean formatting"), _("Extract structured data")])
        ocr_config_layout.addWidget(self.ocr_preprocessing_combo, 1, 1)
        
        input_source_layout.addWidget(self.ocr_config_widget)
        
        layout.addWidget(input_source_group)
        
        layout.addStretch()
        self.tab_widget.addTab(prompt_tab, _("Prompt"))
    
    def create_advanced_tab(self):
        """Create the Advanced Configuration tab"""
        advanced_tab = QWidget()
        layout = QVBoxLayout(advanced_tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        
        # Output Configuration Group
        output_group = QGroupBox(_("Output Configuration"))
        output_layout = QGridLayout(output_group)
        output_layout.setContentsMargins(8, 12, 8, 8)
        output_layout.setSpacing(6)
        
        # Write text option
        output_layout.addWidget(QLabel(_("Write Text:")), 0, 0)
        self.write_text_checkbox = ModernToggle(text=_("Automatically write generated text"))
        self.write_text_checkbox.setChecked(True)
        output_layout.addWidget(self.write_text_checkbox, 0, 1)
        
        # Typing speed parameters (only visible when write_text is checked)
        output_layout.addWidget(QLabel(_("Typing Batch Size:")), 1, 0)
        self.typing_batch_size_spin = QSpinBox()
        self.typing_batch_size_spin.setRange(1, 100)
        self.typing_batch_size_spin.setValue(20)
        self.typing_batch_size_spin.setToolTip(_("Number of characters to type at once (higher = faster typing)"))
        output_layout.addWidget(self.typing_batch_size_spin, 1, 1)
        
        output_layout.addWidget(QLabel(_("Typing Batch Delay:")), 2, 0)
        self.typing_batch_delay_spin = QDoubleSpinBox()
        self.typing_batch_delay_spin.setRange(0.01, 1.0)
        self.typing_batch_delay_spin.setSingleStep(0.01)
        self.typing_batch_delay_spin.setValue(0.05)
        self.typing_batch_delay_spin.setToolTip(_("Delay between typing batches in seconds (lower = faster typing)"))
        output_layout.addWidget(self.typing_batch_delay_spin, 2, 1)
        
        # Connect write_text checkbox to enable/disable typing speed controls
        self.write_text_checkbox.toggled.connect(self.typing_batch_size_spin.setEnabled)
        self.write_text_checkbox.toggled.connect(self.typing_batch_delay_spin.setEnabled)
        
        layout.addWidget(output_group)
        
        # Async Processing Group
        async_group = QGroupBox(_("Async Processing"))
        async_layout = QGridLayout(async_group)
        async_layout.setContentsMargins(8, 12, 8, 8)
        async_layout.setSpacing(6)
        
        # Use async checkbox
        async_layout.addWidget(QLabel(_("Enable Async:")), 0, 0)
        self.use_async_checkbox = ModernToggle(text=_("Process requests asynchronously"))
        self.use_async_checkbox.setChecked(True)
        self.use_async_checkbox.setToolTip(_("Enable asynchronous processing for better performance"))
        async_layout.addWidget(self.use_async_checkbox, 0, 1)
        
        # Add help text for async
        async_help_label = QLabel(_("Async processing allows the workflow to continue while waiting for LLM responses."))
        async_help_label.setWordWrap(True)
        async_help_label.setStyleSheet("color: #888; font-size: 10px; margin-top: 3px;")
        async_layout.addWidget(async_help_label, 1, 0, 1, 2)
        
        layout.addWidget(async_group)
        
        layout.addStretch()
        self.tab_widget.addTab(advanced_tab, _("Advanced"))
    
    def create_features_tab(self):
        """Create the Features Configuration tab"""
        features_tab = QWidget()
        layout = QVBoxLayout(features_tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        
        # Vision Configuration Group
        vision_group = QGroupBox(_("Vision Configuration"))
        vision_layout = QGridLayout(vision_group)
        vision_layout.setContentsMargins(8, 12, 8, 8)
        vision_layout.setSpacing(6)
        
        # Use vision checkbox
        vision_layout.addWidget(QLabel(_("Use Vision:")), 0, 0)
        self.use_vision_checkbox = ModernToggle(text=_("Enable vision capabilities for this model"))
        self.use_vision_checkbox.setChecked(False)
        vision_layout.addWidget(self.use_vision_checkbox, 0, 1)
        
        # Vision Model selection
        vision_layout.addWidget(QLabel(_("Vision Model:")), 1, 0)
        self.vision_model_combo = QComboBox()
        self.vision_model_combo.setEditable(True)
        self.vision_model_combo.addItems(self.get_available_models())
        vision_layout.addWidget(self.vision_model_combo, 1, 1)
        
        # Screenshot enabled checkbox
        vision_layout.addWidget(QLabel(_("Screenshot:")), 2, 0)
        self.screenshot_enabled_checkbox = ModernToggle(text=_("Take screenshot automatically for vision models"))
        self.screenshot_enabled_checkbox.setChecked(True)
        vision_layout.addWidget(self.screenshot_enabled_checkbox, 2, 1)

        # Use advanced vision checkbox
        vision_layout.addWidget(QLabel(_("Use Advanced Vision:")), 3, 0)
        self.use_advanced_vision_checkbox = ModernToggle(text=_("Enable YOLO object detection + OCR"))
        self.use_advanced_vision_checkbox.setChecked(False)
        vision_layout.addWidget(self.use_advanced_vision_checkbox, 3, 1)
        
        # Add help text
        help_label = QLabel(_("Note: Vision models can analyze screenshots. Advanced vision uses YOLO+OCR."))
        help_label.setWordWrap(True)
        help_label.setStyleSheet("color: #888; font-size: 10px; margin-top: 3px;")
        vision_layout.addWidget(help_label, 4, 0, 1, 2)
        
        layout.addWidget(vision_group)
        
        # ComoRAG Configuration Group - HIDDEN as per user request
        # comorag_group = QGroupBox("Context-Aware Features (ComoRAG)")
        # comorag_layout = QGridLayout(comorag_group)
        # comorag_layout.setContentsMargins(8, 12, 8, 8)
        # comorag_layout.setSpacing(6)
        
        # # Enable ComoRAG checkbox
        # comorag_layout.addWidget(QLabel("Enable ComoRAG:"), 0, 0)
        # self.use_comorag_checkbox = ModernToggle(text="Use context-aware reasoning with OverWatch")
        # self.use_comorag_checkbox.setChecked(False)
        # comorag_layout.addWidget(self.use_comorag_checkbox, 0, 1)
        
        # # Max reasoning cycles
        # comorag_layout.addWidget(QLabel("Max Cycles:"), 1, 0)
        # self.max_cycles_spin = QSpinBox()
        # self.max_cycles_spin.setRange(1, 10)
        # self.max_cycles_spin.setValue(5)
        # self.max_cycles_spin.setToolTip("Maximum number of reasoning cycles for ComoRAG")
        # comorag_layout.addWidget(self.max_cycles_spin, 1, 1)
        
        # # Memory size
        # comorag_layout.addWidget(QLabel("Memory Size:"), 2, 0)
        # self.memory_size_spin = QSpinBox()
        # self.memory_size_spin.setRange(100, 5000)
        # self.memory_size_spin.setSingleStep(100)
        # self.memory_size_spin.setValue(1000)
        # self.memory_size_spin.setToolTip("Memory size for ComoRAG context retention")
        # comorag_layout.addWidget(self.memory_size_spin, 2, 1)
        
        # # Add help text for ComoRAG
        # comorag_help_label = QLabel("ComoRAG enhances LLM responses with context-aware reasoning and connects to OverWatch API.")
        # comorag_help_label.setWordWrap(True)
        # comorag_help_label.setStyleSheet("color: #888; font-size: 10px; margin-top: 3px;")
        # comorag_layout.addWidget(comorag_help_label, 3, 0, 1, 2)
        
        # layout.addWidget(comorag_group)
        
        # Initialize hidden widgets to prevent AttributeError
        self.use_comorag_checkbox = ModernToggle(text="Use context-aware reasoning with OverWatch")
        self.use_comorag_checkbox.setChecked(False)
        self.use_comorag_checkbox.setVisible(False)
        
        self.max_cycles_spin = QSpinBox()
        self.max_cycles_spin.setValue(5)
        self.max_cycles_spin.setVisible(False)
        
        self.memory_size_spin = QSpinBox()
        self.memory_size_spin.setValue(1000)
        self.memory_size_spin.setVisible(False)
        
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
            # "ComoRAG Analysis",
            "Data Processing Chain",
            "Text Analysis",
            "Content Summarization",
            "Tool Selection (LLM Tools)",
            "Python Code Generation"
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
            self.vision_model_combo.setCurrentText(self.current_config.get('vision_model', 'minicpm-v:latest'))
            self.use_advanced_vision_checkbox.setChecked(self.current_config.get('use_advanced_vision', False))
            self.screenshot_enabled_checkbox.setChecked(self.current_config.get('screenshot_enabled', True))
            self.use_comorag_checkbox.setChecked(self.current_config.get('use_comorag', True))
            self.max_cycles_spin.setValue(self.current_config.get('max_cycles', 5))
            self.memory_size_spin.setValue(self.current_config.get('memory_size', 1000))
            self.use_direct_rag_checkbox.setChecked(self.current_config.get('use_direct_rag', False))
            self.rag_embedding_model_entry.setText(self.current_config.get('rag_embedding_model', 'nomic-embed-text'))
            self.rag_chunk_size_spin.setValue(self.current_config.get('rag_chunk_size', 500))
            self.rag_overlap_spin.setValue(self.current_config.get('rag_overlap', 100))
            self.rag_top_k_spin.setValue(self.current_config.get('rag_top_k', 3))
            self.rag_include_raw_input_checkbox.setChecked(self.current_config.get('rag_include_raw_input', False))
            self.rag_max_chars_spin.setValue(self.current_config.get('rag_max_chars', 1500))
            docs = self.current_config.get('rag_documents', []) or []
            try:
                import json as _json
                if isinstance(docs, str):
                    docs = _json.loads(docs)
            except Exception:
                docs = []
            for p in docs:
                self.rag_documents_list.addItem(p)
            
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
                "max_tokens": 4096,
                "input_source": 1,  # OCR
                "ocr_confidence": 0.8,
                "ocr_preprocessing": "Enhance contrast",
                "output_variable": "extracted_info"
            },
            "Custom Instructions": {
                "system_message": "You are a helpful AI assistant. Follow the user's instructions carefully.",
                "prompt": "[ENTER YOUR CUSTOM INSTRUCTIONS HERE]",
                "temperature": 0.7,
                "max_tokens": 4096,
                "input_source": 0,  # No input
                "output_variable": "llm_output"
            },
            # "ComoRAG Analysis": {
            #     "system_message": "You are an AI assistant with memory and context awareness. Use the provided context to give informed responses.",
            #     "prompt": "Analyze the following information and provide insights based on the context: [DESCRIBE ANALYSIS GOAL]",
            #     "temperature": 0.3,
            #     "max_tokens": 4096,
            #     "input_source": 2,  # Previous node output
            #     "use_comorag": True,
            #     "max_cycles": 3,
            #     "memory_size": 2000,
            #     "output_variable": "analysis_result"
            # },
            "Data Processing Chain": {
                "system_message": "You are a data processing assistant. Process the input data according to the specified requirements.",
                "prompt": "Process the input data as follows: [DESCRIBE PROCESSING STEPS]",
                "temperature": 0.2,
                "max_tokens": 4096,
                "input_source": 2,  # Previous node output
                "output_variable": "processed_data"
            },
            "Text Analysis": {
                "system_message": "You are a text analysis expert. Analyze the provided text thoroughly.",
                "prompt": "Analyze the following text and provide: 1) Main themes, 2) Key points, 3) Sentiment, 4) Actionable insights: [DESCRIBE ANALYSIS FOCUS]",
                "temperature": 0.4,
                "max_tokens": 4096,
                "input_source": 3,  # Clipboard
                "output_variable": "text_analysis"
            },
            "Content Summarization": {
                "system_message": "You are a content summarization expert. Create concise, accurate summaries.",
                "prompt": "Summarize the following content in [SPECIFY LENGTH] focusing on [SPECIFY FOCUS AREAS]: ",
                "temperature": 0.3,
                "max_tokens": 4096,
                "input_source": 1,  # OCR
                "ocr_confidence": 0.7,
                "output_variable": "summary"
            },
            "Tool Selection (LLM Tools)": {
                "system_message": "You select tools to fulfill the user's request. Choose exactly one tool that best matches the request using the available descriptions. When a tool is needed, respond ONLY with 'USE_TOOL:<id>' and nothing else.",
                "prompt": "Decide which connected tool best solves the user's request and respond ONLY with 'USE_TOOL:<tool_id>'.",
                "temperature": 0.1,
                "max_tokens": 150,
                "input_source": 0,
                "output_variable": "llm_output"
            },
            "Python Code Generation": {
                "system_message": "You are a Python code generator. Your ONLY task is to output executable Python code.\\n1. Output the entire script in a SINGLE markdown code block (```python ... ```).\\n2. Do NOT split the code into multiple blocks.\\n3. Do NOT add any explanations, comments, steps, or text outside the code block.\\n4. Ensure all imports and logic are contained within that one block.",
                "prompt": "Write a complete, single-file Python script to: [TASK_DESCRIPTION]\\n\\nIMPORTANT: Provide ONLY the raw python code inside a single ```python``` block. No other text.",
                "temperature": 0.2,
                "max_tokens": 4096,
                "input_source": 0,
                "output_variable": "llm_output"
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
        self.ocr_config_widget.setVisible(ocr_visible)
        # Show/hide previous input mode controls
        self.previous_mode_widget.setVisible(input_source == 2)
        
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
            # "ComoRAG Analysis": "Use ComoRAG for context-aware analysis with memory and reasoning.",
            "Data Processing Chain": "Process data from previous nodes in a multi-step workflow.",
            "Text Analysis": "Analyze text content from clipboard with detailed insights.",
            "Content Summarization": "Summarize content extracted from screen using OCR.",
            "Tool Selection (LLM Tools)": "Configure base system prompt for tool use. Respond only with 'USE_TOOL:<id>'.",
            "Python Code Generation": "Generate executable Python code in a single block."
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
            'use_async': self.use_async_checkbox.isChecked(),
            'use_vision': self.use_vision_checkbox.isChecked(),
            'vision_model': self.vision_model_combo.currentText(),
            'use_advanced_vision': self.use_advanced_vision_checkbox.isChecked(),
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
            source_id = self.input_source_group.id(selected_button)
            mapping = {0: 'none', 1: 'ocr', 2: 'previous', 3: 'clipboard'}
            config['input_source'] = mapping.get(source_id, 'none')
        else:
            config['input_source'] = 'none'
            
        config['ocr_confidence'] = self.ocr_confidence_spin.value()
        config['ocr_preprocessing'] = self.ocr_preprocessing_combo.currentText()
        config['template_applied'] = self.template_combo.currentText()
        
        # Determine RAG usage directly from RAG tab toggle
        use_direct_rag_value = self.use_direct_rag_checkbox.isChecked()
        config.update({
            'use_direct_rag': use_direct_rag_value,
            'rag_embedding_model': self.rag_embedding_model_entry.text() or 'nomic-embed-text',
            'rag_chunk_size': self.rag_chunk_size_spin.value(),
            'rag_overlap': self.rag_overlap_spin.value(),
            'rag_top_k': self.rag_top_k_spin.value(),
            'rag_include_raw_input': self.rag_include_raw_input_checkbox.isChecked(),
            'rag_max_chars': self.rag_max_chars_spin.value(),
            'rag_documents': [self.rag_documents_list.item(i).text() for i in range(self.rag_documents_list.count())]
        })
        td = {}
        try:
            for i in range(self.tools_list.count()):
                txt = self.tools_list.item(i).text()
                if ':' in txt:
                    k, v = txt.split(':', 1)
                    td[k.strip()] = v.strip()
        except Exception:
            td = {}
        config['tool_descriptions'] = td
        return config

    def create_rag_tab(self):
        rag_tab = QWidget()
        layout = QVBoxLayout(rag_tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        params_group = QGroupBox("RAG Configuration")
        params_layout = QGridLayout(params_group)
        params_layout.setContentsMargins(8, 12, 8, 8)
        params_layout.setSpacing(6)
        params_layout.addWidget(QLabel("Enable RAG:"), 0, 0)
        self.use_direct_rag_checkbox = ModernToggle(text="Use embeddings to add context")
        self.use_direct_rag_checkbox.setChecked(True)
        params_layout.addWidget(self.use_direct_rag_checkbox, 0, 1)
        params_layout.addWidget(QLabel("Embedding Model:"), 1, 0)
        self.rag_embedding_model_entry = QLineEdit()
        self.rag_embedding_model_entry.setText("nomic-embed-text")
        params_layout.addWidget(self.rag_embedding_model_entry, 1, 1)
        params_layout.addWidget(QLabel("Chunk Size:"), 2, 0)
        self.rag_chunk_size_spin = QSpinBox()
        self.rag_chunk_size_spin.setRange(50, 5000)
        self.rag_chunk_size_spin.setValue(500)
        params_layout.addWidget(self.rag_chunk_size_spin, 2, 1)
        params_layout.addWidget(QLabel("Overlap:"), 3, 0)
        self.rag_overlap_spin = QSpinBox()
        self.rag_overlap_spin.setRange(0, 1000)
        self.rag_overlap_spin.setValue(100)
        params_layout.addWidget(self.rag_overlap_spin, 3, 1)
        params_layout.addWidget(QLabel("Top K:"), 4, 0)
        self.rag_top_k_spin = QSpinBox()
        self.rag_top_k_spin.setRange(1, 20)
        self.rag_top_k_spin.setValue(3)
        params_layout.addWidget(self.rag_top_k_spin, 4, 1)
        params_layout.addWidget(QLabel("Include Raw Input:"), 5, 0)
        self.rag_include_raw_input_checkbox = ModernToggle(text="Also include raw previous input")
        params_layout.addWidget(self.rag_include_raw_input_checkbox, 5, 1)
        params_layout.addWidget(QLabel("Max Context Chars:"), 6, 0)
        self.rag_max_chars_spin = QSpinBox()
        self.rag_max_chars_spin.setRange(100, 10000)
        self.rag_max_chars_spin.setValue(1500)
        params_layout.addWidget(self.rag_max_chars_spin, 6, 1)
        layout.addWidget(params_group)
        docs_group = QGroupBox("Documents")
        docs_layout = QVBoxLayout(docs_group)
        self.rag_documents_list = QListWidget()
        docs_layout.addWidget(self.rag_documents_list)
        btns = QHBoxLayout()
        add_btn = QPushButton("Add Document")
        rem_btn = QPushButton("Remove Selected")
        add_btn.clicked.connect(self.add_document)
        rem_btn.clicked.connect(self.remove_selected_document)
        btns.addWidget(add_btn)
        btns.addWidget(rem_btn)
        docs_layout.addLayout(btns)
        layout.addWidget(docs_group)
        layout.addStretch()
        self.tab_widget.addTab(rag_tab, "RAG")

    def create_tools_tab(self):
        tools_tab = QWidget()
        layout = QVBoxLayout(tools_tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        group = QGroupBox(_("Tools Configuration"))
        gl = QGridLayout(group)
        gl.setContentsMargins(8, 12, 8, 8)
        gl.setSpacing(6)
        self.tools_list = QListWidget()
        gl.addWidget(QLabel(_("Connected Tools (id: description)")), 0, 0, 1, 2)
        gl.addWidget(self.tools_list, 1, 0, 1, 2)
        gl.addWidget(QLabel(_("Tool ID")), 2, 0)
        self.tool_id_entry = QLineEdit()
        gl.addWidget(self.tool_id_entry, 2, 1)
        gl.addWidget(QLabel(_("Description")), 3, 0)
        self.tool_desc_entry = QLineEdit()
        gl.addWidget(self.tool_desc_entry, 3, 1)
        btn_row = QHBoxLayout()
        add_btn = QPushButton(_("Add/Update"))
        rem_btn = QPushButton(_("Remove Selected"))
        btn_row.addWidget(add_btn)
        btn_row.addWidget(rem_btn)
        gl.addLayout(btn_row, 4, 0, 1, 2)
        layout.addWidget(group)
        layout.addStretch()
        self.tab_widget.addTab(tools_tab, _("Tools"))
        def on_add():
            try:
                tid = self.tool_id_entry.text().strip()
                desc = self.tool_desc_entry.text().strip()
                if not tid:
                    return
                row_text = f"{tid}: {desc}" if desc else tid
                found = None
                for i in range(self.tools_list.count()):
                    if self.tools_list.item(i).text().startswith(tid+":") or self.tools_list.item(i).text() == tid:
                        found = i
                        break
                if found is not None:
                    self.tools_list.item(found).setText(row_text)
                else:
                    self.tools_list.addItem(row_text)
            except Exception:
                pass
        def on_remove():
            try:
                row = self.tools_list.currentRow()
                if row >= 0:
                    self.tools_list.takeItem(row)
            except Exception:
                pass
        def on_select():
            try:
                row = self.tools_list.currentRow()
                if row >= 0:
                    txt = self.tools_list.item(row).text()
                    if ':' in txt:
                        tid, desc = txt.split(':', 1)
                        self.tool_id_entry.setText(tid.strip())
                        self.tool_desc_entry.setText(desc.strip())
                    else:
                        self.tool_id_entry.setText(txt.strip())
                        self.tool_desc_entry.setText("")
            except Exception:
                pass
        def on_double_click(item):
            try:
                txt = item.text()
                if ':' in txt:
                    tid, desc = txt.split(':', 1)
                else:
                    tid, desc = txt, ''
                new_desc, ok = QInputDialog.getText(self, _("Edit Description"), _("Description for {id}").format(id=tid.strip()), text=desc.strip())
                if ok:
                    new_txt = f"{tid.strip()}: {new_desc.strip()}"
                    item.setText(new_txt)
                    self.tool_id_entry.setText(tid.strip())
                    self.tool_desc_entry.setText(new_desc.strip())
            except Exception:
                pass
        try:
            self.tool_id_entry.setReadOnly(True)
        except Exception:
            pass
        try:
            self.tools_list.setSelectionMode(QListWidget.SingleSelection)
        except Exception:
            pass
        add_btn.clicked.connect(on_add)
        rem_btn.clicked.connect(on_remove)
        self.tools_list.itemSelectionChanged.connect(on_select)
        self.tools_list.itemDoubleClicked.connect(on_double_click)

    def load_tool_descriptions(self, mapping):
        try:
            self.tools_list.clear()
            if isinstance(mapping, dict):
                for k, v in mapping.items():
                    self.tools_list.addItem(f"{k}: {v}")
        except Exception:
            pass

    def add_document(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Document",
            os.path.expanduser("~"),
            "All Files (*.*);;Text Files (*.txt *.md *.csv *.json);;Word Documents (*.docx);;PDF Files (*.pdf)"
        )
        if path:
            self.rag_documents_list.addItem(path)

    def remove_selected_document(self):
        items = self.rag_documents_list.selectedItems()
        for it in items:
            row = self.rag_documents_list.row(it)
            self.rag_documents_list.takeItem(row)
    
    def apply_template(self, template_name):
        """Apply a predefined template to configure the LLM node"""
        templates = {
            "Information Extraction (OCR + LLM)": {
                "description": "Extract specific information from screen text using OCR and LLM",
                "input_source": 1,  # OCR
                "system_message": "You are an expert at extracting specific information from text. Extract the requested information accurately and concisely.",
                "prompt": "Extract the following information from the text: [SPECIFY WHAT TO EXTRACT]",
                "temperature": 0.3,
                "max_tokens": 4096,
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
                "max_tokens": 4096,
                "use_comorag": False
            },
            "Direct Prompt": {
                "description": "Simple direct prompt without any special configuration",
                "input_source": 0,  # No input
                "system_message": "",
                "prompt": "[ENTER YOUR PROMPT HERE]",
                "temperature": 0.7,
                "max_tokens": 4096,
                "use_comorag": False
            },
            "ComoRAG Analysis": {
                "description": "Use ComoRAG for context-aware analysis and reasoning",
                "input_source": 0,  # No input
                "system_message": "You are an intelligent assistant with access to context-aware reasoning. Analyze the request and provide comprehensive insights.",
                "prompt": "[ENTER YOUR ANALYSIS REQUEST]",
                "temperature": 0.5,
                "max_tokens": 4096,
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
                "max_tokens": 4096,
                "use_comorag": False
            },
            "Text Analysis": {
                "description": "Analyze text content with OCR input",
                "input_source": 1,  # OCR
                "system_message": "You are a text analysis expert. Analyze the provided text and extract insights, patterns, or requested information.",
                "prompt": "Analyze the text and provide: [SPECIFY ANALYSIS REQUIREMENTS]",
                "temperature": 0.4,
                "max_tokens": 4096,
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
                "max_tokens": 4096,
                "use_comorag": False,
                "ocr_confidence": 0.6,
                "ocr_preprocessing": "Clean formatting"
            },
            "Tool Selection (LLM Tools)": {
                "description": "Configure base system prompt for tool use. Respond only with 'USE_TOOL:<id>'.",
                "input_source": 0,
                "system_message": "You select tools to fulfill the user's request. Choose exactly one tool that best matches the request using the available descriptions. When a tool is needed, respond ONLY with 'USE_TOOL:<id>' and nothing else.",
                "prompt": "Decide which connected tool best solves the user's request and respond ONLY with 'USE_TOOL:<tool_id>'.",
                "temperature": 0.1,
                "max_tokens": 150,
                "use_comorag": False
            },
            "Python Code Generation": {
                "description": "Generate executable Python code in a single block.",
                "input_source": 0,
                "system_message": "You are a Python code generator. Your ONLY task is to output executable Python code.\\n1. Output the entire script in a SINGLE markdown code block (```python ... ```).\\n2. Do NOT split the code into multiple blocks.\\n3. Do NOT add any explanations, comments, steps, or text outside the code block.\\n4. Ensure all imports and logic are contained within that one block.",
                "prompt": "Write a complete, single-file Python script to: [TASK_DESCRIPTION]\\n\\nIMPORTANT: Provide ONLY the raw python code inside a single ```python``` block. No other text.",
                "temperature": 0.2,
                "max_tokens": 4096,
                "use_comorag": False
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
        # Show/hide previous input mode controls
        if hasattr(self, 'previous_mode_widget'):
            self.previous_mode_widget.setVisible(input_source_id == 2)
        
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
