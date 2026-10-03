"""Conditional dialog classes for the LoOper application.

This module contains the AdvancedConditionalDialog class for configuring
complex conditional logic including pre-conditions, post-conditions, and
conditional loops.
"""

import logging

from .base_dialog import ModernDialog
from .toggle_switch import ModernToggle
from .code_node_dialog import CodeAgentThread, _extract_code
from .llm_dialogs import get_llamacpp_models
from ..nodes_resources.base_node import get_default_api_url
from PyQt5.QtCore import Qt, QSize, QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QVBoxLayout, QLabel, QLineEdit, QPushButton, QHBoxLayout, 
    QComboBox, QSpinBox, QDoubleSpinBox, QWidget, QFileDialog,
    QListWidgetItem, QTextEdit, QGroupBox, QListWidget, QDialog,
    QFrame, QMessageBox, QStackedWidget, QGridLayout, QSizePolicy, QApplication
)
from PyQt5.QtGui import QPixmap, QFont
from ..constants import (TEXT_COLOR, TEXT_MUTED, ACCENT_COLOR, SIDE_PANEL_BG, LIGHT_GREY,
                         BLOCK_COLOR, DARK_GREY, WELL_BG, CONTROL_BG, HAIRLINE,
                         RADIUS_SM, RADIUS_MD)
from .trigger_dialogs import TriggerConfigDialog, OCRTriggerConfigDialog, ConditionalLoopDialog, WaitConditionDialog, LayoutMatchConfigDialog
from ..i18n import _

logger = logging.getLogger(__name__)


class _WebPickThread(QThread):
    """Runs the blocking element picker off the GUI thread."""

    picked = pyqtSignal(object)

    def run(self):
        try:
            try:
                from ...player.web.picker import pick_element
            except Exception:
                from player.web.picker import pick_element
        except Exception as exc:
            logger.error(f"Element picker unavailable: {exc}")
            self.picked.emit(None)
            return
        try:
            self.picked.emit(pick_element())
        except Exception as exc:
            logger.error(f"Element picker failed: {exc}")
            self.picked.emit(None)


class _ShrinkStack(QStackedWidget):
    """QStackedWidget that takes the CURRENT page's size, not the tallest one.

    A plain QStackedWidget reports the max sizeHint over every page, so the
    short 'Element located' page inherits the tall 'LLM page' height and the
    Web Conditional box shows a block of dead space.  Reporting only the
    current page keeps the box tight when switching condition types.
    """

    def sizeHint(self):
        w = self.currentWidget()
        return w.sizeHint() if w is not None else super().sizeHint()

    def minimumSizeHint(self):
        w = self.currentWidget()
        return w.minimumSizeHint() if w is not None else super().minimumSizeHint()


class ConditionItemWidget(QFrame):
    """Custom widget for displaying conditions in the list"""
    def __init__(self, config, condition_type="presence"):
        super().__init__()
        self.config = config
        self.condition_type = condition_type
        self.setup_ui()

    def setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(15)
        
        # Style
        self.setStyleSheet(f"""
            ConditionItemWidget {{
                background-color: {BLOCK_COLOR};
                border: 1px solid {HAIRLINE};
                border-radius: {RADIUS_MD}px;
            }}
            QLabel {{
                color: {TEXT_COLOR};
                border: none;
            }}
        """)
        
        # Preview
        preview_label = QLabel()
        preview_label.setFixedSize(60, 60)
        preview_label.setAlignment(Qt.AlignCenter)
        preview_label.setStyleSheet(f"background-color: {DARK_GREY}; border-radius: {RADIUS_SM}px;")
        
        info_text = ""
        
        if self.condition_type == "presence" or self.condition_type == "absence":
            image_path = self.config.get('image_path', '')
            if image_path:
                pixmap = QPixmap(image_path)
                if not pixmap.isNull():
                    preview_label.setPixmap(pixmap.scaled(50, 50, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                else:
                    preview_label.setText("IMG")
            else:
                preview_label.setText("IMG")
                
            conf = self.config.get('confidence', 0.8)
            timeout = self.config.get('timeout', 0)
            timeout_str = f", { _('Timeout (s):') } {timeout}" if timeout > 0 else ""
            info_text = f"<b>{_('Image Trigger')}</b><br>{image_path}<br>{_('Confidence:')} {conf}{timeout_str}"
            
        elif self.condition_type == "ocr":
            preview_label.setText("OCR")
            text = self.config.get('target_text', '')
            conf = self.config.get('confidence', 0.8)
            text_info = _("Text: '{text}' (Confidence: {conf})").format(text=text, conf=conf)
            info_text = f"<b>{_('OCR Trigger')}</b><br>{text_info}"
            
        elif self.condition_type == "loop":
            preview_label.setText("LOOP")
            loop_type = self.config.get('type', '')
            seq = self.config.get('sequence_file', '')
            # No iteration cap: the loop runs until the condition is no longer met.
            info_text = f"<b>{_('Conditional Loop')}</b><br>{_('Loop Type:')} {loop_type}<br>{_('Sequence File:')} {seq}<br>{_('Runs until the condition is no longer met')}"
            
        elif self.condition_type == "layout_match":
            preview_label.setText("LAYOUT")
            image_path = self.config.get('image_path', '')
            conf = self.config.get('confidence', 0.8)
            tx = self.config.get('target_x')
            ty = self.config.get('target_y')
            tw = self.config.get('target_w')
            th = self.config.get('target_h')
            tol = self.config.get('position_tolerance', 6)
            if tx is not None and ty is not None and tw is not None and th is not None:
                info_text = f"<b>{_('Layout Match')}</b><br>{image_path}<br>{_('Confidence:')} {conf}<br>{_('Captured region:')} x={tx}, y={ty}, w={tw}, h={th}, { _('Position Tolerance (px):') } {tol}"
            else:
                info_text = f"<b>{_('Layout Match')}</b><br>{image_path}<br>{_('Confidence:')} {conf}"

        elif self.condition_type == "wait":
             preview_label.setText("WAIT")
             wtype = self.config.get('type', 'wait_time')
             if wtype == 'wait_time':
                 duration = self.config.get('duration', 5.0)
                 info_text = f"<b>{_('Wait Condition')}</b><br>{_('Wait Duration (s):')} {duration}"
             else:
                 timeout = self.config.get('timeout', 30)
                 info_text = f"<b>{_('Wait Condition')}</b><br>{_('Wait Type:')} {wtype}, { _('Timeout (s):') } {timeout}"

        elif self.condition_type == "llm":
            preview_label.setText("LLM")
            engine = self.config.get('engine', 'ollama')
            model = self.config.get('model', '')
            prompt = self.config.get('prompt', '')
            prompt_preview = prompt[:80] + '...' if len(prompt) > 80 else prompt
            info_text = f"<b>LLM Conditional</b><br>Engine: {engine}<br>Model: {model}<br>Prompt: {prompt_preview}"

        layout.addWidget(preview_label)
        
        info_label = QLabel(info_text)
        info_label.setWordWrap(True)
        layout.addWidget(info_label)
        layout.addStretch()


class AdvancedConditionalDialog(ModernDialog):
    """Dialog for configuring advanced conditional fallbacks and automation logic"""
    
    def __init__(self, parent, sequence_idx, chain_config, current_config=None):
        title = _("Advanced Conditions - Sequence {num}").format(num=sequence_idx + 1)
        super().__init__(parent, title=title, help_topic="conditional-dialog")
        self.sequence_idx = sequence_idx
        self.chain_config = chain_config
        self.current_config = current_config or {}
        # Prefill for the description field (what this check does).
        self._initial_description = str(self.current_config.get('description') or '')
        
        self.setModal(True)
        self._auto_fit = False
        self.resize(800, 700)  # Increased size for better visibility
        self.setMinimumSize(750, 650)  # Set minimum size to prevent content clipping
        # Size to restore when Web Mode is switched back off (it shrinks the
        # dialog while the tall desktop condition groups are hidden).
        self._pre_web_size = None
        
        # Style logic moved to ModernDialog
        
        self.setup_ui()
    
    def setup_ui(self):
        """Setup the UI for the advanced conditional dialog"""
        # Use content_layout from ModernDialog
        layout = self.content_layout
        layout.setContentsMargins(15, 15, 15, 15)  # Add proper margins
        layout.setSpacing(10)  # Add spacing between elements
        
        # Unified dialog without tabs: presence triggers, OCR triggers, and pre-sequence loops
        self.setup_unified_conditions(layout)
        
        # Buttons
        button_layout = QHBoxLayout()
        button_layout.setSpacing(10)  # Add spacing between buttons
        button_layout.addStretch()
        
        save_btn = QPushButton(_("Save"))
        save_btn.setMinimumSize(80, 30)  # Set minimum button size
        save_btn.clicked.connect(self.accept)
        button_layout.addWidget(save_btn)
        
        cancel_btn = QPushButton(_("Cancel"))
        cancel_btn.setMinimumSize(80, 30)  # Set minimum button size
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)
        
        layout.addLayout(button_layout)

    def setup_unified_conditions(self, parent_layout: QVBoxLayout):
        """Setup a unified layout with the supported options (no tabs)."""
        # Desktop condition groups are hidden while Web Mode is on.
        self._desktop_condition_groups = []

        # What this check DOES — the orchestrator reads it when the node is
        # used as a mini brain (label + structural hint are the fallback).
        _desc_row = QHBoxLayout()
        _desc_row.setSpacing(8)
        _desc_label = QLabel(_("Description (what this check does):"))
        _desc_label.setStyleSheet(f"color: {TEXT_COLOR};")
        self.description_input = QLineEdit(self._initial_description)
        self.description_input.setPlaceholderText(
            _("e.g. Is the 'Apply' button visible?"))
        _desc_row.addWidget(_desc_label)
        _desc_row.addWidget(self.description_input)
        parent_layout.addLayout(_desc_row)

        # Web Mode toggle: route on the chain's live browser instead of the
        # desktop visual conditions (see _build_web_condition_group).
        web_mode_row = QHBoxLayout()
        web_mode_row.setSpacing(8)
        web_mode_label = QLabel(_("Web Mode (route on the chain's browser)"))
        web_mode_label.setStyleSheet(f"color: {TEXT_COLOR};")
        self.web_mode_toggle = ModernToggle()
        web_mode_row.addWidget(web_mode_label)
        web_mode_row.addWidget(self.web_mode_toggle)
        web_mode_row.addStretch()
        parent_layout.addLayout(web_mode_row)

        self.web_group = self._build_web_condition_group()
        parent_layout.addWidget(self.web_group)

        try:
            self.web_mode_toggle.toggled.connect(self._on_web_mode_toggled)
        except Exception:
            try:
                self.web_mode_toggle.stateChanged.connect(self._on_web_mode_toggled)
            except Exception:
                pass

        # Code condition
        code_group = QGroupBox(_("Code Condition (must set 'result' to True/False for routing)"))
        code_layout = QVBoxLayout(code_group)
        code_layout.setContentsMargins(10, 15, 10, 10)
        code_layout.setSpacing(8)

        code_enable_row = QHBoxLayout()
        code_enable_row.setSpacing(8)
        code_enable_label = QLabel(_("Enable"))
        code_enable_label.setStyleSheet(f"color: {TEXT_COLOR};")
        self.code_enabled_toggle = ModernToggle()
        code_enable_row.addWidget(code_enable_label)
        code_enable_row.addWidget(self.code_enabled_toggle)
        code_enable_row.addStretch()
        code_layout.addLayout(code_enable_row)

        # Info label showing available variables
        info_label = QLabel()
        info_label.setWordWrap(True)
        info_label.setStyleSheet(f"""
            QLabel {{
                color: {TEXT_MUTED};
                background-color: {WELL_BG};
                border: 1px solid {HAIRLINE};
                border-radius: {RADIUS_SM}px;
                padding: 6px;
                font-size: 11px;
            }}
        """)
        info_label.setText(_(
            "Available variables in your code:\n"
            "  \u2022 input_data  - output from the previous node (LLM result, code output, etc.)\n"
            "  \u2022 args        - dict of values from 'args' port connections\n"
            "  \u2022 variables   - all shared workflow variables (read/write)\n"
            "  \u2022 get_variable(name, default) - retrieve any variable by name\n"
            "  \u2022 set_variable(name, value)   - store a value as a variable\n"
            "Set result = True to follow the 'true' branch, or False for the 'false' branch."
        ))
        code_layout.addWidget(info_label)

        self.code_edit = QTextEdit()
        self.code_edit.setMinimumHeight(160)
        self.code_edit.setStyleSheet(f"""
            QTextEdit {{
                background-color: {WELL_BG};
                color: {TEXT_COLOR};
                border: 1px solid {HAIRLINE};
                border-radius: {RADIUS_SM}px;
                padding: 6px;
            }}
        """)
        code_layout.addWidget(self.code_edit)

        code_controls = QHBoxLayout()
        code_controls.setSpacing(8)
        timeout_label = QLabel(_("Timeout (s):"))
        timeout_label.setStyleSheet(f"color: {TEXT_COLOR};")
        self.code_timeout_spin = QDoubleSpinBox()
        self.code_timeout_spin.setRange(0.0, 3600.0)
        self.code_timeout_spin.setDecimals(1)
        self.code_timeout_spin.setSingleStep(1.0)
        self.code_timeout_spin.setValue(5.0)
        self.code_timeout_spin.setStyleSheet(f"""
            QDoubleSpinBox {{
                background-color: {CONTROL_BG};
                color: {TEXT_COLOR};
                border: 1px solid {HAIRLINE};
                border-radius: {RADIUS_SM}px;
                padding: 4px;
            }}
        """)
        code_controls.addWidget(timeout_label)
        code_controls.addWidget(self.code_timeout_spin)
        code_controls.addStretch()
        code_layout.addLayout(code_controls)

        # ── AI Code Generation bar ──
        gen_sep = QFrame()
        gen_sep.setFrameShape(QFrame.HLine)
        gen_sep.setStyleSheet(f"color: {HAIRLINE};")
        code_layout.addWidget(gen_sep)

        gen_header = QLabel(_("Generate code with AI:"))
        gen_header.setStyleSheet(f"color: {ACCENT_COLOR}; font-size: 11px; font-weight: 600;")
        code_layout.addWidget(gen_header)

        gen_row = QHBoxLayout()
        gen_row.setSpacing(6)

        # Engine selector
        self.code_gen_engine = QComboBox()
        self.code_gen_engine.addItems(["Ollama", "llama.cpp"])
        self.code_gen_engine.setFixedHeight(26)
        self.code_gen_engine.setFixedWidth(85)
        self.code_gen_engine.currentTextChanged.connect(self._on_code_gen_engine_changed)
        gen_row.addWidget(self.code_gen_engine)

        # Model selector (Ollama)
        self.code_gen_model = QComboBox()
        self.code_gen_model.setEditable(True)
        self.code_gen_model.setFixedHeight(26)
        self.code_gen_model.setMinimumWidth(130)
        gen_row.addWidget(self.code_gen_model)

        # LLama.cpp model selector (hidden by default)
        self.code_gen_gguf = QComboBox()
        self.code_gen_gguf.setEditable(True)
        self.code_gen_gguf.setFixedHeight(26)
        self.code_gen_gguf.setMinimumWidth(130)
        self.code_gen_gguf.setVisible(False)
        gen_row.addWidget(self.code_gen_gguf)

        # Prompt input
        self.code_gen_prompt = QLineEdit()
        self.code_gen_prompt.setPlaceholderText(_("Describe the conditional code to generate…"))
        self.code_gen_prompt.setFixedHeight(26)
        self.code_gen_prompt.returnPressed.connect(self._on_code_generate)
        gen_row.addWidget(self.code_gen_prompt, 1)

        # Generate button
        self.code_gen_btn = QPushButton(_("Generate"))
        self.code_gen_btn.setFixedHeight(26)
        self.code_gen_btn.setFixedWidth(80)
        self.code_gen_btn.setCursor(Qt.PointingHandCursor)
        self.code_gen_btn.clicked.connect(self._on_code_generate)
        gen_row.addWidget(self.code_gen_btn)

        code_layout.addLayout(gen_row)

        parent_layout.addWidget(code_group)
        self._desktop_condition_groups.append(code_group)

        # LLM Conditional
        llm_group = QGroupBox(_("LLM Conditional (evaluate context with AI)"))
        llm_layout = QVBoxLayout(llm_group)
        llm_layout.setContentsMargins(10, 15, 10, 10)
        llm_layout.setSpacing(8)

        llm_enable_row = QHBoxLayout()
        llm_enable_row.setSpacing(8)
        llm_enable_label = QLabel(_("Enable"))
        llm_enable_label.setStyleSheet(f"color: {TEXT_COLOR};")
        self.llm_enabled_toggle = ModernToggle()
        llm_enable_row.addWidget(llm_enable_label)
        llm_enable_row.addWidget(self.llm_enabled_toggle)
        llm_enable_row.addStretch()
        llm_layout.addLayout(llm_enable_row)

        # Info label for LLM conditional
        llm_info_label = QLabel()
        llm_info_label.setWordWrap(True)
        llm_info_label.setStyleSheet(f"""
            QLabel {{
                color: {TEXT_MUTED};
                background-color: {WELL_BG};
                border: 1px solid {HAIRLINE};
                border-radius: {RADIUS_SM}px;
                padding: 6px;
                font-size: 11px;
            }}
        """)
        llm_info_label.setText(_(
            "The LLM will receive the output from the upstream node as context.\n"
            "It MUST respond with ONLY the word \"True\" or \"False\".\n"
            "Use a small, fast model for best latency (e.g. llama3.2:1b, qwen2.5:0.5b)."
        ))
        llm_layout.addWidget(llm_info_label)

        # Engine selector row
        llm_engine_row = QHBoxLayout()
        llm_engine_row.setSpacing(8)
        llm_engine_label = QLabel(_("Engine:"))
        llm_engine_label.setStyleSheet(f"color: {TEXT_COLOR};")
        self.llm_engine_combo = QComboBox()
        self.llm_engine_combo.addItems(["Ollama", "llama.cpp"])
        self.llm_engine_combo.setFixedHeight(26)
        self.llm_engine_combo.setFixedWidth(85)
        self.llm_engine_combo.currentTextChanged.connect(self._on_llm_engine_changed)
        llm_engine_row.addWidget(llm_engine_label)
        llm_engine_row.addWidget(self.llm_engine_combo)

        # Ollama model selector
        self.llm_ollama_model = QComboBox()
        self.llm_ollama_model.setEditable(True)
        self.llm_ollama_model.setFixedHeight(26)
        self.llm_ollama_model.setMinimumWidth(130)
        llm_engine_row.addWidget(self.llm_ollama_model)

        # llama.cpp GGUF model selector (hidden by default)
        self.llm_gguf_model = QComboBox()
        self.llm_gguf_model.setEditable(True)
        self.llm_gguf_model.setFixedHeight(26)
        self.llm_gguf_model.setMinimumWidth(130)
        self.llm_gguf_model.setVisible(False)
        llm_engine_row.addWidget(self.llm_gguf_model)

        llm_engine_row.addStretch()
        llm_layout.addLayout(llm_engine_row)

        # Prompt text area
        llm_prompt_label = QLabel(_("Prompt:"))
        llm_prompt_label.setStyleSheet(f"color: {TEXT_COLOR};")
        llm_layout.addWidget(llm_prompt_label)
        self.llm_prompt_edit = QTextEdit()
        self.llm_prompt_edit.setMinimumHeight(100)
        self.llm_prompt_edit.setStyleSheet(f"""
            QTextEdit {{
                background-color: {WELL_BG};
                color: {TEXT_COLOR};
                border: 1px solid {HAIRLINE};
                border-radius: {RADIUS_SM}px;
                padding: 6px;
            }}
        """)
        llm_layout.addWidget(self.llm_prompt_edit)

        # Timeout row
        llm_controls = QHBoxLayout()
        llm_controls.setSpacing(8)
        llm_timeout_label = QLabel(_("Timeout (s):"))
        llm_timeout_label.setStyleSheet(f"color: {TEXT_COLOR};")
        self.llm_timeout_spin = QDoubleSpinBox()
        self.llm_timeout_spin.setRange(0.5, 300.0)
        self.llm_timeout_spin.setDecimals(1)
        self.llm_timeout_spin.setSingleStep(1.0)
        self.llm_timeout_spin.setValue(10.0)
        self.llm_timeout_spin.setStyleSheet(f"""
            QDoubleSpinBox {{
                background-color: {CONTROL_BG};
                color: {TEXT_COLOR};
                border: 1px solid {HAIRLINE};
                border-radius: {RADIUS_SM}px;
                padding: 4px;
            }}
        """)
        llm_controls.addWidget(llm_timeout_label)
        llm_controls.addWidget(self.llm_timeout_spin)
        llm_controls.addStretch()
        llm_layout.addLayout(llm_controls)

        # Vision / screenshot toggle (for VL models)
        llm_vision_row = QHBoxLayout()
        llm_vision_row.setSpacing(8)
        llm_vision_label = QLabel(_("Use screenshot (vision model)"))
        llm_vision_label.setStyleSheet(f"color: {TEXT_COLOR};")
        self.llm_use_vision_toggle = ModernToggle()
        llm_vision_row.addWidget(llm_vision_label)
        llm_vision_row.addWidget(self.llm_use_vision_toggle)
        llm_vision_row.addStretch()
        llm_layout.addLayout(llm_vision_row)

        parent_layout.addWidget(llm_group)
        self._desktop_condition_groups.append(llm_group)

        # Presence triggers
        presence_group = QGroupBox(_("Presence Triggers (must be present to execute)"))
        presence_layout = QVBoxLayout(presence_group)
        presence_layout.setContentsMargins(10, 15, 10, 10)
        presence_layout.setSpacing(8)

        self.presence_triggers_list = QListWidget()
        self.presence_triggers_list.setMinimumHeight(120)
        self.presence_triggers_list.itemDoubleClicked.connect(self.edit_presence_trigger)
        presence_layout.addWidget(self.presence_triggers_list)

        presence_buttons = QHBoxLayout()
        presence_buttons.setSpacing(8)
        add_presence_btn = QPushButton(_("Add Presence Trigger"))
        add_presence_btn.setMinimumHeight(30)
        add_presence_btn.clicked.connect(self.add_presence_trigger)
        presence_buttons.addWidget(add_presence_btn)

        remove_presence_btn = QPushButton(_("Remove Selected"))
        remove_presence_btn.setMinimumHeight(30)
        remove_presence_btn.clicked.connect(lambda: self.remove_selected_item(self.presence_triggers_list))
        presence_buttons.addWidget(remove_presence_btn)
        presence_buttons.addStretch()

        presence_layout.addLayout(presence_buttons)
        parent_layout.addWidget(presence_group)
        self._desktop_condition_groups.append(presence_group)

        layout_match_group = QGroupBox(_("Layout Match Conditionals"))
        layout_match_layout = QVBoxLayout(layout_match_group)
        layout_match_layout.setContentsMargins(10, 15, 10, 10)
        layout_match_layout.setSpacing(8)

        self.layout_match_list = QListWidget()
        self.layout_match_list.setMinimumHeight(120)
        self.layout_match_list.itemDoubleClicked.connect(self.edit_layout_match)
        layout_match_layout.addWidget(self.layout_match_list)

        layout_match_buttons = QHBoxLayout()
        layout_match_buttons.setSpacing(8)
        add_layout_match_btn = QPushButton(_("Add Layout Match"))
        add_layout_match_btn.setMinimumHeight(30)
        add_layout_match_btn.clicked.connect(self.add_layout_match)
        layout_match_buttons.addWidget(add_layout_match_btn)

        remove_layout_match_btn = QPushButton(_("Remove Selected"))
        remove_layout_match_btn.setMinimumHeight(30)
        remove_layout_match_btn.clicked.connect(lambda: self.remove_selected_item(self.layout_match_list))
        layout_match_buttons.addWidget(remove_layout_match_btn)
        layout_match_buttons.addStretch()

        layout_match_layout.addLayout(layout_match_buttons)
        parent_layout.addWidget(layout_match_group)
        self._desktop_condition_groups.append(layout_match_group)

        # OCR triggers
        ocr_group = QGroupBox(_("OCR Triggers (text must be present to execute)"))
        ocr_layout = QVBoxLayout(ocr_group)
        ocr_layout.setContentsMargins(10, 15, 10, 10)
        ocr_layout.setSpacing(8)

        self.ocr_triggers_list = QListWidget()
        self.ocr_triggers_list.setMinimumHeight(120)
        self.ocr_triggers_list.itemDoubleClicked.connect(self.edit_ocr_trigger)
        ocr_layout.addWidget(self.ocr_triggers_list)

        ocr_buttons = QHBoxLayout()
        ocr_buttons.setSpacing(8)
        add_ocr_btn = QPushButton(_("Add OCR Trigger"))
        add_ocr_btn.setMinimumHeight(30)
        add_ocr_btn.clicked.connect(self.add_ocr_trigger)
        ocr_buttons.addWidget(add_ocr_btn)

        remove_ocr_btn = QPushButton(_("Remove Selected"))
        remove_ocr_btn.setMinimumHeight(30)
        remove_ocr_btn.clicked.connect(lambda: self.remove_selected_item(self.ocr_triggers_list))
        ocr_buttons.addWidget(remove_ocr_btn)
        ocr_buttons.addStretch()

        ocr_layout.addLayout(ocr_buttons)
        parent_layout.addWidget(ocr_group)
        self._desktop_condition_groups.append(ocr_group)

        # Pre-sequence conditional loops
        pre_loops_group = QGroupBox(_("Pre-Sequence Conditional Loops"))
        pre_loops_layout = QVBoxLayout(pre_loops_group)
        pre_loops_layout.setContentsMargins(10, 15, 10, 10)
        pre_loops_layout.setSpacing(8)

        self.pre_loops_list = QListWidget()
        self.pre_loops_list.setMinimumHeight(120)
        self.pre_loops_list.itemDoubleClicked.connect(self.edit_pre_loop)
        pre_loops_layout.addWidget(self.pre_loops_list)

        pre_loops_buttons = QHBoxLayout()
        pre_loops_buttons.setSpacing(8)
        add_pre_loop_btn = QPushButton(_("Add Pre-Loop"))
        add_pre_loop_btn.setMinimumHeight(30)
        add_pre_loop_btn.clicked.connect(self.add_pre_conditional_loop)
        pre_loops_buttons.addWidget(add_pre_loop_btn)

        remove_pre_loop_btn = QPushButton(_("Remove Selected"))
        remove_pre_loop_btn.setMinimumHeight(30)
        remove_pre_loop_btn.clicked.connect(lambda: self.remove_selected_item(self.pre_loops_list))
        pre_loops_buttons.addWidget(remove_pre_loop_btn)
        pre_loops_buttons.addStretch()

        pre_loops_layout.addLayout(pre_loops_buttons)
        parent_layout.addWidget(pre_loops_group)
        self._desktop_condition_groups.append(pre_loops_group)

        # Load existing data for the supported options
        self.load_code_condition()
        self.load_llm_condition()
        self.load_pre_conditions()
        self.load_conditional_loops()
        self.load_web_condition()
        self._on_web_mode_toggled(
            bool(self.web_mode_toggle.isChecked()) if hasattr(self, 'web_mode_toggle') else False
        )

    # ------------------------------------------------------------------ #
    #  Web Mode conditional
    # ------------------------------------------------------------------ #
    def _build_web_condition_group(self):
        group = QGroupBox(_("Web Conditional (runs on the chain's shared browser)"))
        layout = QVBoxLayout(group)
        layout.setContentsMargins(10, 15, 10, 10)
        layout.setSpacing(8)

        info = QLabel(_(
            "Routes on the live browser after a web sequence node - place this "
            "conditional AFTER a web sequence that opened the page.\n"
            "Element / text targets are captured with the browser picker."
        ))
        info.setWordWrap(True)
        info.setStyleSheet(
            f"color:{TEXT_MUTED}; background-color:{BLOCK_COLOR}; border:1px solid {HAIRLINE};"
            f"border-radius:{RADIUS_SM}px; padding:6px; font-size:11px;"
        )
        layout.addWidget(info)

        type_row = QHBoxLayout()
        type_row.setSpacing(8)
        type_label = QLabel(_("Condition:"))
        type_label.setStyleSheet(f"color: {TEXT_COLOR};")
        type_row.addWidget(type_label)
        self.web_type_combo = QComboBox()
        self.web_type_combo.addItems([
            _("Element located"),
            _("Text present"),
            _("Browser JS"),
            _("LLM (page text)"),
            _("Layout match (scroll to location)"),
        ])
        self.web_type_combo.currentIndexChanged.connect(self._on_web_type_changed)
        type_row.addWidget(self.web_type_combo, 1)
        layout.addLayout(type_row)

        # Locators are stored as JSON strings; the line edits show a summary.
        self.web_element_locator = ''
        self.web_text_locator = ''
        self.web_layout_locator = ''

        # _ShrinkStack sizes to the visible page so short condition types do
        # not inherit the tallest page's height (see class docstring).
        self.web_stack = _ShrinkStack()
        self.web_stack.addWidget(self._web_element_page())
        self.web_stack.addWidget(self._web_text_page())
        self.web_stack.addWidget(self._web_js_page())
        self.web_stack.addWidget(self._web_llm_page())
        self.web_stack.addWidget(self._web_layout_page())
        self.web_stack.currentChanged.connect(lambda *_: self._fit_web_stack())
        layout.addWidget(self.web_stack)

        # Never let the condition box absorb the dialog's spare vertical space.
        group.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

        timeout_row = QHBoxLayout()
        timeout_row.setSpacing(8)
        timeout_label = QLabel(_("Timeout (s):"))
        timeout_label.setStyleSheet(f"color: {TEXT_COLOR};")
        timeout_row.addWidget(timeout_label)
        self.web_timeout_spin = QDoubleSpinBox()
        self.web_timeout_spin.setRange(0.5, 300.0)
        self.web_timeout_spin.setDecimals(1)
        self.web_timeout_spin.setValue(10.0)
        timeout_row.addWidget(self.web_timeout_spin)
        timeout_row.addStretch()
        layout.addLayout(timeout_row)
        return group

    def _web_locator_row(self, label, attr_name, pick_target):
        row = QHBoxLayout()
        row.setSpacing(8)
        lbl = QLabel(_(label))
        lbl.setStyleSheet(f"color: {TEXT_COLOR};")
        row.addWidget(lbl)
        edit = QLineEdit()
        edit.setReadOnly(True)
        edit.setPlaceholderText(_("(none - pick an element)"))
        setattr(self, attr_name, edit)
        row.addWidget(edit, 1)
        btn = QPushButton(_("Pick element"))
        btn.clicked.connect(lambda: self._pick_web_element(pick_target))
        row.addWidget(btn)
        return row

    def _web_element_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(QLabel(_("True when the picked element is present (and visible).")))
        layout.addLayout(self._web_locator_row("Element:", 'web_element_locator_edit', 'element'))
        return page

    def _web_text_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        src_row = QHBoxLayout()
        src_row.setSpacing(8)
        src_label = QLabel(_("Text source:"))
        src_label.setStyleSheet(f"color: {TEXT_COLOR};")
        self.web_text_source_combo = QComboBox()
        self.web_text_source_combo.addItems([_("Whole page"), _("Picked element")])
        self.web_text_source_combo.currentIndexChanged.connect(self._on_web_text_source_changed)
        src_row.addWidget(src_label)
        src_row.addWidget(self.web_text_source_combo, 1)
        layout.addLayout(src_row)

        layout.addLayout(self._web_locator_row("Element:", 'web_text_locator_edit', 'text'))

        text_row = QHBoxLayout()
        text_row.setSpacing(8)
        text_label = QLabel(_("Target text:"))
        text_label.setStyleSheet(f"color: {TEXT_COLOR};")
        self.web_target_text_edit = QLineEdit()
        self.web_target_text_edit.setPlaceholderText(_("text that must be present"))
        capture_btn = QPushButton(_("Capture text"))
        capture_btn.clicked.connect(self._capture_web_text)
        text_row.addWidget(text_label)
        text_row.addWidget(self.web_target_text_edit, 1)
        text_row.addWidget(capture_btn)
        layout.addLayout(text_row)

        case_row = QHBoxLayout()
        case_row.setSpacing(8)
        case_label = QLabel(_("Case sensitive"))
        case_label.setStyleSheet(f"color: {TEXT_COLOR};")
        self.web_case_sensitive_toggle = ModernToggle()
        case_row.addWidget(case_label)
        case_row.addWidget(self.web_case_sensitive_toggle)
        case_row.addStretch()
        layout.addLayout(case_row)
        return page

    def _web_js_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(QLabel(_("Runs in the page; a truthy result routes the TRUE branch.")))
        self.web_js_edit = QTextEdit()
        self.web_js_edit.setMinimumHeight(110)
        self.web_js_edit.setPlainText(
            "// Return a truthy value to follow the TRUE branch.\n"
            "// Example: return document.querySelectorAll('.job-card').length > 0;\n"
            "return !!document.querySelector('main');"
        )
        layout.addWidget(self.web_js_edit)
        return page

    def _web_llm_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(QLabel(_("The LLM reads the page's visible text + element presence.")))

        engine_row = QHBoxLayout()
        engine_row.setSpacing(8)
        engine_label = QLabel(_("Engine:"))
        engine_label.setStyleSheet(f"color: {TEXT_COLOR};")
        self.web_llm_engine_combo = QComboBox()
        self.web_llm_engine_combo.addItems(["Ollama", "llama.cpp"])
        self.web_llm_engine_combo.currentTextChanged.connect(self._on_web_llm_engine_changed)
        self.web_llm_model_combo = QComboBox()
        self.web_llm_model_combo.setEditable(True)
        self.web_llm_model_combo.setMinimumWidth(140)
        engine_row.addWidget(engine_label)
        engine_row.addWidget(self.web_llm_engine_combo)
        engine_row.addWidget(self.web_llm_model_combo, 1)
        layout.addLayout(engine_row)

        self.web_llm_prompt_edit = QTextEdit()
        self.web_llm_prompt_edit.setMinimumHeight(90)
        self.web_llm_prompt_edit.setPlainText(
            'Respond with EXACTLY one word: "True" or "False".\n'
            'Condition to evaluate from the page:\n[describe the condition here]'
        )
        layout.addWidget(self.web_llm_prompt_edit)
        self._init_web_llm_models()
        return page

    def _web_layout_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(QLabel(_("Scroll up/down until the element sits at the target window box.")))
        layout.addLayout(self._web_locator_row("Element:", 'web_layout_locator_edit', 'layout'))

        grid = QGridLayout()
        grid.setSpacing(6)
        self.web_layout_spins = {}
        for col, key in enumerate(("x", "y", "w", "h")):
            grid.addWidget(QLabel(key.upper()), 0, col)
            spin = QSpinBox()
            spin.setRange(-100000, 100000)
            self.web_layout_spins[key] = spin
            grid.addWidget(spin, 1, col)
        layout.addLayout(grid)

        opt_row = QHBoxLayout()
        opt_row.setSpacing(8)
        dir_label = QLabel(_("Scroll:"))
        dir_label.setStyleSheet(f"color: {TEXT_COLOR};")
        self.web_layout_direction_combo = QComboBox()
        self.web_layout_direction_combo.addItems([_("Down"), _("Up")])
        att_label = QLabel(_("Attempts:"))
        att_label.setStyleSheet(f"color: {TEXT_COLOR};")
        self.web_layout_attempts_spin = QSpinBox()
        self.web_layout_attempts_spin.setRange(1, 100000)
        self.web_layout_attempts_spin.setValue(20)
        tol_label = QLabel(_("Tolerance (px):"))
        tol_label.setStyleSheet(f"color: {TEXT_COLOR};")
        self.web_layout_tolerance_spin = QSpinBox()
        self.web_layout_tolerance_spin.setRange(0, 500)
        self.web_layout_tolerance_spin.setValue(12)
        for widget in (dir_label, self.web_layout_direction_combo, att_label,
                       self.web_layout_attempts_spin, tol_label, self.web_layout_tolerance_spin):
            opt_row.addWidget(widget)
        opt_row.addStretch()
        layout.addLayout(opt_row)
        return page

    def _on_web_mode_toggled(self, enabled):
        try:
            enabled = bool(enabled)
        except Exception:
            enabled = False
        try:
            self.web_group.setVisible(enabled)
        except Exception:
            pass
        for grp in getattr(self, '_desktop_condition_groups', []):
            try:
                grp.setVisible(not enabled)
            except Exception:
                pass
        # Web mode hides the tall desktop groups, so allow the dialog to shrink
        # to the compact web form; desktop mode keeps its fixed scrollable
        # frame and restores the pre-web size when switching back.
        try:
            if enabled:
                if getattr(self, '_pre_web_size', None) is None:
                    self._pre_web_size = self.size()
                self._refit_dialog()
            else:
                self.setMinimumSize(750, 650)
                if getattr(self, '_pre_web_size', None) is not None:
                    self.resize(self._pre_web_size)
                    self._pre_web_size = None
        except Exception:
            pass

    def _refit_dialog(self):
        """Web mode: size the dialog to the current condition's content.

        The desktop frame stays a fixed 800x700 scroll area; only the compact
        web form auto-fits, so switching condition types never leaves a block
        of dead space and never forces a horizontal scrollbar.
        """
        try:
            if not self.web_group.isVisible():
                return
            hint = self.content_layout.sizeHint()
            # Chrome = everything around the viewport (title bar, grip, borders).
            chrome = self.size() - self.scroll_area.viewport().size()
            w = max(520, hint.width() + max(chrome.width(), 34))
            h = max(320, hint.height() + max(chrome.height(), 90))
            scr = QApplication.primaryScreen()
            if scr is not None:
                avail = scr.availableGeometry()
                w = min(w, avail.width() - 60)
                h = min(h, avail.height() - 60)
            self.setMinimumSize(w, 320)
            self.resize(w, h)
        except Exception:
            pass

    def _fit_web_stack(self):
        """Re-fit after the web condition type changes (stack size changed)."""
        try:
            self.web_stack.updateGeometry()
        except Exception:
            pass
        self._refit_dialog()

    def showEvent(self, event):
        super().showEvent(event)
        # _on_web_mode_toggled fires during construction (before show), when
        # isVisible() is still False, so an already-configured web conditional
        # would otherwise open full-size.  Re-fit once actually visible.
        try:
            if self.web_group.isVisible():
                self._refit_dialog()
        except Exception:
            pass

    def _on_web_type_changed(self, index):
        try:
            self.web_stack.setCurrentIndex(int(index))
        except Exception:
            pass

    def _on_web_text_source_changed(self, index):
        element_mode = int(index) == 1
        if hasattr(self, 'web_text_locator_edit'):
            self.web_text_locator_edit.setEnabled(element_mode)

    def _on_web_llm_engine_changed(self, engine):
        try:
            self._init_web_llm_models()
        except Exception:
            pass

    def _init_web_llm_models(self):
        use_llamacpp = (self.web_llm_engine_combo.currentText() == "llama.cpp")
        cached = getattr(self, '_web_llm_model_cache', None)
        if cached is None:
            try:
                from AI.model_cache import get_cached_models
                ollama_models = get_cached_models() or []
            except Exception:
                ollama_models = []
            try:
                gguf_models = get_llamacpp_models() or []
            except Exception:
                gguf_models = []
            cached = {'ollama': list(ollama_models), 'llamacpp': list(gguf_models)}
            self._web_llm_model_cache = cached
        models = cached['llamacpp'] if use_llamacpp else cached['ollama']
        current = self.web_llm_model_combo.currentText()
        self.web_llm_model_combo.clear()
        if models:
            self.web_llm_model_combo.addItems(models)
            if current:
                self.web_llm_model_combo.setCurrentText(current)

    # ------------------------------------------------------------------ #
    #  Web element capture (browser picker)
    # ------------------------------------------------------------------ #
    def _pick_web_element(self, target):
        """Arm the browser picker on a worker thread and store the result."""
        self._web_pick_target = target
        try:
            self._web_modal_backup = self.isModal()
            self.setModal(False)
            # Minimize (NOT hide): hiding a dialog that is inside exec_() makes
            # exec_() return Rejected, so the whole edit would be thrown away as
            # "cancelled" and the web condition just picked would be lost.
            self.showMinimized()
        except Exception:
            pass
        self._web_pick_thread = _WebPickThread(self)
        self._web_pick_thread.picked.connect(self._on_web_element_picked)
        self._web_pick_thread.finished.connect(self._on_web_pick_finished)
        self._web_pick_thread.start()

    def _on_web_pick_finished(self):
        try:
            self.setModal(bool(getattr(self, '_web_modal_backup', True)))
            # showNormal restores a window minimized by _pick_web_element.
            self.showNormal()
            self.raise_()
            self.activateWindow()
        except Exception:
            pass

    @staticmethod
    def _locator_summary(locator):
        if not isinstance(locator, dict):
            return ''
        parts = [str(locator.get('tag') or 'element')]
        if locator.get('id'):
            parts.append('#' + str(locator['id']))
        text = str(locator.get('text') or '').strip()
        if text:
            parts.append('"' + text[:40] + '"')
        return ' '.join(parts)

    @staticmethod
    def _safe_json(value):
        import json as _json
        if isinstance(value, dict):
            return value
        try:
            return _json.loads(value)
        except Exception:
            return None

    def _on_web_element_picked(self, result):
        if not result or (isinstance(result, dict) and result.get('cancelled')):
            return
        import json as _json
        locator = dict(result.get('locator') or {})
        # Where the element lives: a frame-relative css/xpath must be re-applied
        # to the SAME document, never to the top one - a top-document lookalike
        # otherwise satisfies a target that has since gone.
        locator['_frame_path'] = result.get('frame_path') or []
        locator['_cross_origin'] = bool(result.get('cross_origin_frame'))
        # The window the element was picked in (0 = opener, 1 = first popup).  A
        # same-host popup cannot be told apart by URL, so the condition
        # re-enters this recorded window before searching.
        if result.get('_window_ordinal') is not None:
            locator['_window_ordinal'] = int(result['_window_ordinal'])
        locator_json = _json.dumps(locator)
        target = getattr(self, '_web_pick_target', 'element')
        summary = self._locator_summary(locator)
        if target == 'element':
            self.web_element_locator = locator_json
            self.web_element_locator_edit.setText(summary)
        elif target == 'text':
            self.web_text_locator = locator_json
            self.web_text_locator_edit.setText(summary)
        elif target == 'layout':
            self.web_layout_locator = locator_json
            self.web_layout_locator_edit.setText(summary)
            rect = result.get('rect') or {}
            for key in ('x', 'y', 'w', 'h'):
                if key in rect and key in self.web_layout_spins:
                    try:
                        self.web_layout_spins[key].setValue(int(rect[key]))
                    except Exception:
                        pass

    def _capture_web_text(self):
        """Read the picked element's (or the page's) visible text into the field."""
        try:
            try:
                from ...player.web.picker import capture_text
            except Exception:
                from player.web.picker import capture_text
        except Exception as exc:
            logger.error(f"Text capture unavailable: {exc}")
            return
        try:
            text = capture_text(self.web_text_locator or None)
        except Exception as exc:
            logger.error(f"Text capture failed: {exc}")
            return
        if text:
            self.web_target_text_edit.setText(str(text)[:2000])

    def load_web_condition(self):
        """Load a saved web_condition block (or defaults) into the web UI."""
        cfg = self.current_config.get('web_condition') or {}
        try:
            self.web_mode_toggle.setChecked(bool(cfg))
        except Exception:
            pass
        type_map = {
            'element_located': 0, 'text_present': 1,
            'browser_js': 2, 'llm': 3, 'layout_match': 4,
        }
        try:
            self.web_type_combo.setCurrentIndex(
                type_map.get(str(cfg.get('condition_type') or 'element_located'), 0)
            )
        except Exception:
            pass
        self.web_element_locator = cfg.get('element_locator') or ''
        self.web_text_locator = cfg.get('text_locator') or ''
        self.web_layout_locator = cfg.get('layout_locator') or ''
        self.web_element_locator_edit.setText(self._locator_summary(self._safe_json(self.web_element_locator)))
        self.web_text_locator_edit.setText(self._locator_summary(self._safe_json(self.web_text_locator)))
        self.web_layout_locator_edit.setText(self._locator_summary(self._safe_json(self.web_layout_locator)))
        try:
            self.web_text_source_combo.setCurrentIndex(1 if str(cfg.get('text_source') or 'page') == 'element' else 0)
        except Exception:
            pass
        self._on_web_text_source_changed(self.web_text_source_combo.currentIndex())
        try:
            self.web_target_text_edit.setText(str(cfg.get('target_text') or ''))
        except Exception:
            pass
        try:
            self.web_case_sensitive_toggle.setChecked(bool(cfg.get('case_sensitive')))
        except Exception:
            pass
        if cfg.get('js'):
            try:
                self.web_js_edit.setPlainText(str(cfg.get('js')))
            except Exception:
                pass
        try:
            self.web_llm_engine_combo.setCurrentText("llama.cpp" if cfg.get('llm_engine') == 'llamacpp' else "Ollama")
        except Exception:
            pass
        self._init_web_llm_models()
        if cfg.get('llm_model'):
            try:
                self.web_llm_model_combo.setCurrentText(str(cfg.get('llm_model')))
            except Exception:
                pass
        if cfg.get('llm_prompt'):
            try:
                self.web_llm_prompt_edit.setPlainText(str(cfg.get('llm_prompt')))
            except Exception:
                pass
        for key in ('x', 'y', 'w', 'h'):
            try:
                self.web_layout_spins[key].setValue(int(cfg.get('layout_' + key, 0) or 0))
            except Exception:
                pass
        try:
            self.web_layout_direction_combo.setCurrentIndex(1 if int(cfg.get('layout_direction', -1)) >= 0 else 0)
        except Exception:
            pass
        try:
            self.web_layout_attempts_spin.setValue(int(cfg.get('layout_attempts', 20) or 20))
        except Exception:
            pass
        try:
            self.web_layout_tolerance_spin.setValue(int(cfg.get('layout_tolerance', 12) or 12))
        except Exception:
            pass
        try:
            self.web_timeout_spin.setValue(float(cfg.get('timeout', 10.0) or 10.0))
        except Exception:
            pass

    def _web_condition_config(self):
        """Build the web_condition dict for get_config from the current UI state."""
        type_keys = ['element_located', 'text_present', 'browser_js', 'llm', 'layout_match']
        try:
            ctype = type_keys[int(self.web_type_combo.currentIndex())]
        except Exception:
            ctype = 'element_located'
        try:
            timeout = float(self.web_timeout_spin.value())
        except Exception:
            timeout = 10.0
        direction = 1 if self.web_layout_direction_combo.currentIndex() == 1 else -1
        return {
            'condition_type': ctype,
            'element_locator': self.web_element_locator or '',
            'text_source': 'element' if self.web_text_source_combo.currentIndex() == 1 else 'page',
            'text_locator': self.web_text_locator or '',
            'target_text': self.web_target_text_edit.text(),
            'case_sensitive': bool(self.web_case_sensitive_toggle.isChecked()),
            'js': self.web_js_edit.toPlainText(),
            'llm_engine': 'llamacpp' if self.web_llm_engine_combo.currentText() == "llama.cpp" else 'ollama',
            'llm_model': self.web_llm_model_combo.currentText(),
            'llm_prompt': self.web_llm_prompt_edit.toPlainText(),
            'llm_timeout': timeout,
            'layout_locator': self.web_layout_locator or '',
            'layout_x': int(self.web_layout_spins['x'].value()),
            'layout_y': int(self.web_layout_spins['y'].value()),
            'layout_w': int(self.web_layout_spins['w'].value()),
            'layout_h': int(self.web_layout_spins['h'].value()),
            'layout_direction': direction,
            'layout_attempts': int(self.web_layout_attempts_spin.value()),
            'layout_tolerance': int(self.web_layout_tolerance_spin.value()),
            'timeout': timeout,
        }

    def load_code_condition(self):
        code_cfg = self.current_config.get('code_condition') or {}
        enabled = bool(code_cfg) and str(code_cfg.get('code', '') or '').strip() != ''
        try:
            self.code_enabled_toggle.setChecked(enabled)
        except Exception:
            pass
        try:
            code_text = code_cfg.get('code', '')
        except Exception:
            code_text = ''
        if code_text:
            self.code_edit.setPlainText(str(code_text))
        else:
            self.code_edit.setPlainText(
                "# Evaluate previous node's output to decide routing\n"
                "# input_data contains the output from the previous node\n"
                "# Examples:\n"
                "#   # Check if LLM output contains specific keywords\n"
                "#   if input_data and 'error' in str(input_data).lower():\n"
                "#       result = False\n"
                "#   else:\n"
                "#       result = True\n"
                "result = True"
            )
        try:
            timeout = float(code_cfg.get('timeout', 5.0) or 5.0)
        except Exception:
            timeout = 5.0
        try:
            self.code_timeout_spin.setValue(timeout)
        except Exception:
            pass
        try:
            self.code_enabled_toggle.toggled.connect(self._on_code_condition_toggled)
        except Exception:
            try:
                self.code_enabled_toggle.stateChanged.connect(self._on_code_condition_toggled)
            except Exception:
                pass
        self._on_code_condition_toggled(enabled)

        # Populate model lists for AI generation
        self._init_code_gen_models()

    def _on_code_condition_toggled(self, enabled):
        try:
            enabled = bool(enabled)
        except Exception:
            enabled = False
        try:
            self.code_edit.setEnabled(enabled)
        except Exception:
            pass
        try:
            self.code_timeout_spin.setEnabled(enabled)
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    #  LLM Conditional
    # ------------------------------------------------------------------ #
    def load_llm_condition(self):
        """Load LLM conditional configuration from current_config."""
        llm_cfg = self.current_config.get('llm_condition') or {}
        enabled = bool(llm_cfg) and str(llm_cfg.get('prompt', '') or '').strip() != ''
        try:
            self.llm_enabled_toggle.setChecked(enabled)
        except Exception:
            pass
        # Engine
        engine = llm_cfg.get('engine', 'ollama')
        try:
            self.llm_engine_combo.setCurrentText("llama.cpp" if engine == 'llamacpp' else "Ollama")
        except Exception:
            pass
        # Populate model lists FIRST, then restore selection
        self._init_llm_models()
        # Model — restore after populating so setCurrentText finds the item
        model = llm_cfg.get('model', '')
        if model:
            if engine == 'llamacpp':
                self.llm_gguf_model.setCurrentText(str(model))
            else:
                self.llm_ollama_model.setCurrentText(str(model))
        # Prompt
        prompt_text = llm_cfg.get('prompt', '')
        if prompt_text:
            self.llm_prompt_edit.setPlainText(str(prompt_text))
        else:
            self.llm_prompt_edit.setPlainText(
                'You are a strict routing classifier.\n'
                'Respond with EXACTLY one word: "True" or "False".\n'
                'No other text, explanation, punctuation, or formatting.\n'
                'Respond "True" only if the context satisfies this condition; otherwise "False".\n'
                '\n'
                'Condition to evaluate: [describe the condition here]'
            )
        # Timeout
        try:
            timeout = float(llm_cfg.get('timeout', 10.0) or 10.0)
        except Exception:
            timeout = 10.0
        try:
            self.llm_timeout_spin.setValue(timeout)
        except Exception:
            pass
        # Toggle connection
        try:
            self.llm_enabled_toggle.toggled.connect(self._on_llm_condition_toggled)
        except Exception:
            try:
                self.llm_enabled_toggle.stateChanged.connect(self._on_llm_condition_toggled)
            except Exception:
                pass
        self._on_llm_condition_toggled(enabled)
        # Vision toggle
        use_vision = str(llm_cfg.get('use_vision', 'false')).strip().lower() in ('true', '1', 'yes')
        try:
            self.llm_use_vision_toggle.setChecked(use_vision)
        except Exception:
            pass

    def _init_llm_models(self):
        """Populate LLM conditional model combo boxes."""
        try:
            from AI.model_cache import get_cached_models
            models = get_cached_models()
        except Exception:
            models = []
        if models:
            self.llm_ollama_model.clear()
            self.llm_ollama_model.addItems(models)
            self.llm_ollama_model.setCurrentText(
                models[0] if models else ''
            )
        gguf_models = get_llamacpp_models()
        if gguf_models:
            self.llm_gguf_model.clear()
            self.llm_gguf_model.addItems(gguf_models)

    def _on_llm_condition_toggled(self, enabled):
        try:
            enabled = bool(enabled)
        except Exception:
            enabled = False
        try:
            self.llm_prompt_edit.setEnabled(enabled)
        except Exception:
            pass
        try:
            self.llm_engine_combo.setEnabled(enabled)
        except Exception:
            pass
        try:
            self.llm_timeout_spin.setEnabled(enabled)
        except Exception:
            pass
        try:
            self.llm_ollama_model.setEnabled(enabled)
        except Exception:
            pass
        try:
            self.llm_gguf_model.setEnabled(enabled)
        except Exception:
            pass
        try:
            self.llm_use_vision_toggle.setEnabled(enabled)
        except Exception:
            pass

    def _on_llm_engine_changed(self, engine):
        """Toggle visibility of model selectors based on engine."""
        use_llamacpp = (engine == "llama.cpp")
        self.llm_ollama_model.setVisible(not use_llamacpp)
        self.llm_gguf_model.setVisible(use_llamacpp)

    # ------------------------------------------------------------------ #
    #  AI Code Generation
    # ------------------------------------------------------------------ #
    CODING_SYSTEM_CONDITIONAL = (
        "You are an expert Python developer. Generate ONLY the Python code for a "
        "conditional routing function. The code must set the variable 'result' to "
        "True or False. Available variables:\n"
        "  - input_data: output from the previous node (LLM result, code output, etc.)\n"
        "  - args: dict of values from 'args' port connections\n"
        "  - variables: all shared workflow variables (read/write)\n"
        "  - get_variable(name, default): retrieve any variable by name\n"
        "  - set_variable(name, value): store a value as a variable\n\n"
        "RESPOND WITH THE CODE INSIDE A ```python CODE BLOCK.\n"
        "Keep it concise — no explanations outside the block."
    )

    def _init_code_gen_models(self):
        """Populate model combo boxes."""
        try:
            from AI.model_cache import get_cached_models
            models = get_cached_models()
        except Exception:
            models = []
        if models:
            self.code_gen_model.clear()
            self.code_gen_model.addItems(models)
            self.code_gen_model.setCurrentText(
                models[0] if models else ''
            )
        # Populate llama.cpp GGUF list
        gguf_models = get_llamacpp_models()
        if gguf_models:
            self.code_gen_gguf.clear()
            self.code_gen_gguf.addItems(gguf_models)

    def _on_code_gen_engine_changed(self, engine):
        """Toggle visibility of model selectors based on engine."""
        use_llamacpp = (engine == "llama.cpp")
        self.code_gen_model.setVisible(not use_llamacpp)
        self.code_gen_gguf.setVisible(use_llamacpp)

    def _on_code_generate(self):
        """Send prompt to AI, extract code, insert into editor."""
        prompt = self.code_gen_prompt.text().strip()
        if not prompt:
            return

        use_llamacpp = (self.code_gen_engine.currentText() == "llama.cpp")
        model = (self.code_gen_gguf.currentText() if use_llamacpp
                 else self.code_gen_model.currentText())

        self.code_gen_btn.setEnabled(False)
        self.code_gen_btn.setText(_("..."))

        self._gen_thread = CodeAgentThread(
            prompt, model, use_llamacpp=use_llamacpp,
            system=self.CODING_SYSTEM_CONDITIONAL
        )
        self._gen_thread.finished.connect(self._on_code_gen_response)
        self._gen_thread.error.connect(self._on_code_gen_error)
        self._gen_thread.start()

    def _on_code_gen_response(self, response_text):
        """Handle AI response: extract code and insert into editor."""
        self.code_gen_btn.setEnabled(True)
        self.code_gen_btn.setText(_("Generate"))

        code = _extract_code(response_text)
        if code:
            self.code_edit.setPlainText(code)
            if not self.code_enabled_toggle.isChecked():
                try:
                    self.code_enabled_toggle.setChecked(True)
                except Exception:
                    pass
        else:
            QMessageBox.information(
                self, _("Generation Result"),
                _("AI response did not contain a code block.\n\nRaw response:\n{resp}").format(
                    resp=response_text[:500]
                )
            )

    def _on_code_gen_error(self, error_msg):
        """Handle generation error."""
        self.code_gen_btn.setEnabled(True)
        self.code_gen_btn.setText(_("Generate"))
        QMessageBox.warning(
            self, _("Generation Error"),
            _("Failed to generate code:\n{err}").format(err=error_msg)
        )

    def setup_pre_conditions_tab(self):
        """Setup the pre-sequence conditions tab"""
        tab = QWidget()
        self.tab_widget.addTab(tab, _("Pre-Conditions"))
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(15)
        
        # Presence triggers
        presence_group = QGroupBox(_("Presence Triggers (must be present to execute)"))
        presence_layout = QVBoxLayout(presence_group)
        presence_layout.setContentsMargins(10, 15, 10, 10)
        presence_layout.setSpacing(8)
        
        self.presence_triggers_list = QListWidget()
        self.presence_triggers_list.setMinimumHeight(120)
        presence_layout.addWidget(self.presence_triggers_list)
        
        presence_buttons = QHBoxLayout()
        presence_buttons.setSpacing(8)
        add_presence_btn = QPushButton(_("Add Presence Trigger"))
        add_presence_btn.setMinimumHeight(30)
        add_presence_btn.clicked.connect(self.add_presence_trigger)
        presence_buttons.addWidget(add_presence_btn)
        
        remove_presence_btn = QPushButton(_("Remove Selected"))
        remove_presence_btn.setMinimumHeight(30)
        remove_presence_btn.clicked.connect(lambda: self.remove_selected_item(self.presence_triggers_list))
        presence_buttons.addWidget(remove_presence_btn)
        presence_buttons.addStretch()
        
        presence_layout.addLayout(presence_buttons)
        layout.addWidget(presence_group)
        
        # Absence triggers
        absence_group = QGroupBox(_("Absence Triggers (must be absent to execute)"))
        absence_layout = QVBoxLayout(absence_group)
        absence_layout.setContentsMargins(10, 15, 10, 10)
        absence_layout.setSpacing(8)
        
        self.absence_triggers_list = QListWidget()
        self.absence_triggers_list.setMinimumHeight(120)
        absence_layout.addWidget(self.absence_triggers_list)
        
        absence_buttons = QHBoxLayout()
        absence_buttons.setSpacing(8)
        add_absence_btn = QPushButton(_("Add Absence Trigger"))
        add_absence_btn.setMinimumHeight(30)
        add_absence_btn.clicked.connect(self.add_absence_trigger)
        absence_buttons.addWidget(add_absence_btn)
        
        remove_absence_btn = QPushButton(_("Remove Selected"))
        remove_absence_btn.setMinimumHeight(30)
        remove_absence_btn.clicked.connect(lambda: self.remove_selected_item(self.absence_triggers_list))
        absence_buttons.addWidget(remove_absence_btn)
        absence_buttons.addStretch()
        
        absence_layout.addLayout(absence_buttons)
        layout.addWidget(absence_group)
        
        # OCR triggers
        ocr_group = QGroupBox(_("OCR Triggers (text must be present to execute)"))
        ocr_layout = QVBoxLayout(ocr_group)
        ocr_layout.setContentsMargins(10, 15, 10, 10)
        ocr_layout.setSpacing(8)
        
        self.ocr_triggers_list = QListWidget()
        self.ocr_triggers_list.setMinimumHeight(120)
        ocr_layout.addWidget(self.ocr_triggers_list)
        
        ocr_buttons = QHBoxLayout()
        ocr_buttons.setSpacing(8)
        add_ocr_btn = QPushButton(_("Add OCR Trigger"))
        add_ocr_btn.setMinimumHeight(30)
        add_ocr_btn.clicked.connect(self.add_ocr_trigger)
        ocr_buttons.addWidget(add_ocr_btn)
        
        remove_ocr_btn = QPushButton(_("Remove Selected"))
        remove_ocr_btn.setMinimumHeight(30)
        remove_ocr_btn.clicked.connect(lambda: self.remove_selected_item(self.ocr_triggers_list))
        ocr_buttons.addWidget(remove_ocr_btn)
        ocr_buttons.addStretch()
        
        ocr_layout.addLayout(ocr_buttons)
        layout.addWidget(ocr_group)
        
        # Load existing data
        self.load_pre_conditions()
    
    def setup_post_conditions_tab(self):
        """Setup the post-sequence conditions tab"""
        tab = QWidget()
        self.tab_widget.addTab(tab, _("Post-Conditions"))
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(15)
        
        # Wait conditions
        wait_group = QGroupBox(_("Wait Conditions (wait after sequence execution)"))
        wait_layout = QVBoxLayout(wait_group)
        wait_layout.setContentsMargins(10, 15, 10, 10)
        wait_layout.setSpacing(8)
        
        self.wait_conditions_list = QListWidget()
        self.wait_conditions_list.setMinimumHeight(200)  # Larger height for this tab
        wait_layout.addWidget(self.wait_conditions_list)
        
        wait_buttons = QHBoxLayout()
        wait_buttons.setSpacing(8)
        add_wait_btn = QPushButton(_("Add Wait Condition"))
        add_wait_btn.setMinimumHeight(30)
        add_wait_btn.clicked.connect(self.add_wait_condition)
        wait_buttons.addWidget(add_wait_btn)
        
        remove_wait_btn = QPushButton(_("Remove Selected"))
        remove_wait_btn.setMinimumHeight(30)
        remove_wait_btn.clicked.connect(lambda: self.remove_selected_item(self.wait_conditions_list))
        wait_buttons.addWidget(remove_wait_btn)
        wait_buttons.addStretch()
        
        wait_layout.addLayout(wait_buttons)
        layout.addWidget(wait_group)
        
        # Add stretch to center content vertically
        layout.addStretch()
        
        # Load existing data
        self.load_post_conditions()
    
    def setup_conditional_loops_tab(self):
        """Setup the conditional loops tab"""
        tab = QWidget()
        self.tab_widget.addTab(tab, _("Conditional Loops"))
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(15)
        
        # Pre-sequence loops
        pre_loops_group = QGroupBox(_("Pre-Sequence Conditional Loops"))
        pre_loops_layout = QVBoxLayout(pre_loops_group)
        pre_loops_layout.setContentsMargins(10, 15, 10, 10)
        pre_loops_layout.setSpacing(8)
        
        self.pre_loops_list = QListWidget()
        self.pre_loops_list.setMinimumHeight(120)
        pre_loops_layout.addWidget(self.pre_loops_list)
        
        pre_loops_buttons = QHBoxLayout()
        pre_loops_buttons.setSpacing(8)
        add_pre_loop_btn = QPushButton(_("Add Pre-Loop"))
        add_pre_loop_btn.setMinimumHeight(30)
        add_pre_loop_btn.clicked.connect(self.add_pre_conditional_loop)
        pre_loops_buttons.addWidget(add_pre_loop_btn)
        
        remove_pre_loop_btn = QPushButton(_("Remove Selected"))
        remove_pre_loop_btn.setMinimumHeight(30)
        remove_pre_loop_btn.clicked.connect(lambda: self.remove_selected_item(self.pre_loops_list))
        pre_loops_buttons.addWidget(remove_pre_loop_btn)
        pre_loops_buttons.addStretch()
        
        pre_loops_layout.addLayout(pre_loops_buttons)
        layout.addWidget(pre_loops_group)
        
        # Post-sequence loops
        post_loops_group = QGroupBox(_("Post-Sequence Conditional Loops"))
        post_loops_layout = QVBoxLayout(post_loops_group)
        post_loops_layout.setContentsMargins(10, 15, 10, 10)
        post_loops_layout.setSpacing(8)
        
        self.post_loops_list = QListWidget()
        self.post_loops_list.setMinimumHeight(120)
        post_loops_layout.addWidget(self.post_loops_list)
        
        post_loops_buttons = QHBoxLayout()
        post_loops_buttons.setSpacing(8)
        add_post_loop_btn = QPushButton(_("Add Post-Loop"))
        add_post_loop_btn.setMinimumHeight(30)
        add_post_loop_btn.clicked.connect(self.add_post_conditional_loop)
        post_loops_buttons.addWidget(add_post_loop_btn)
        
        remove_post_loop_btn = QPushButton(_("Remove Selected"))
        remove_post_loop_btn.setMinimumHeight(30)
        remove_post_loop_btn.clicked.connect(lambda: self.remove_selected_item(self.post_loops_list))
        post_loops_buttons.addWidget(remove_post_loop_btn)
        post_loops_buttons.addStretch()
        
        post_loops_layout.addLayout(post_loops_buttons)
        layout.addWidget(post_loops_group)
        
        # Load existing data
        self.load_conditional_loops()
    
    def create_list_item(self, list_widget, config, condition_type):
        """Create a custom list item with widget"""
        item = QListWidgetItem()
        item.setSizeHint(QSize(0, 80))  # Set appropriate height
        item.setData(32, config)
        list_widget.addItem(item)
        
        widget = ConditionItemWidget(config, condition_type)
        list_widget.setItemWidget(item, widget)
        
    def edit_presence_trigger(self, item):
        """Edit presence trigger"""
        config = item.data(32)
        dialog = TriggerConfigDialog(self, config=config)
        if dialog.exec_() == QDialog.Accepted:
            new_config = dialog.get_config()
            item.setData(32, new_config)
            # Refresh widget
            widget = ConditionItemWidget(new_config, "presence")
            self.presence_triggers_list.setItemWidget(item, widget)

    def edit_layout_match(self, item):
        config = item.data(32)
        dialog = LayoutMatchConfigDialog(self, config=config)
        if dialog.exec_() == QDialog.Accepted:
            new_config = dialog.get_config()
            item.setData(32, new_config)
            widget = ConditionItemWidget(new_config, "layout_match")
            self.layout_match_list.setItemWidget(item, widget)

    def edit_absence_trigger(self, item):
        """Edit absence trigger"""
        config = item.data(32)
        dialog = TriggerConfigDialog(self, is_absence_trigger=True, config=config)
        if dialog.exec_() == QDialog.Accepted:
            new_config = dialog.get_config()
            
            # Validate timeout
            timeout = new_config.get('timeout', 0)
            if timeout <= 0:
                new_config['timeout'] = 10
            
            item.setData(32, new_config)
            widget = ConditionItemWidget(new_config, "absence")
            self.absence_triggers_list.setItemWidget(item, widget)

    def edit_ocr_trigger(self, item):
        """Edit OCR trigger"""
        config = item.data(32)
        dialog = OCRTriggerConfigDialog(self, config=config)
        if dialog.exec_() == QDialog.Accepted:
            new_config = dialog.get_config()
            item.setData(32, new_config)
            widget = ConditionItemWidget(new_config, "ocr")
            self.ocr_triggers_list.setItemWidget(item, widget)

    def edit_wait_condition(self, item):
        """Edit wait condition"""
        config = item.data(32)
        dialog = WaitConditionDialog(self, config=config)
        if dialog.exec_() == QDialog.Accepted:
            new_config = dialog.get_config()
            item.setData(32, new_config)
            widget = ConditionItemWidget(new_config, "wait")
            self.wait_conditions_list.setItemWidget(item, widget)

    def edit_pre_loop(self, item):
        """Edit pre-sequence conditional loop"""
        config = item.data(32)
        dialog = ConditionalLoopDialog(self, config=config)
        if dialog.exec_() == QDialog.Accepted:
            new_config = dialog.get_config()
            item.setData(32, new_config)
            widget = ConditionItemWidget(new_config, "loop")
            self.pre_loops_list.setItemWidget(item, widget)

    def edit_post_loop(self, item):
        """Edit post-sequence conditional loop"""
        config = item.data(32)
        dialog = ConditionalLoopDialog(self, config=config)
        if dialog.exec_() == QDialog.Accepted:
            new_config = dialog.get_config()
            item.setData(32, new_config)
            widget = ConditionItemWidget(new_config, "loop")
            self.post_loops_list.setItemWidget(item, widget)

    def add_presence_trigger(self):
        """Add a presence trigger"""
        dialog = TriggerConfigDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            config = dialog.get_config()
            self.create_list_item(self.presence_triggers_list, config, "presence")

    def add_layout_match(self):
        dialog = LayoutMatchConfigDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            config = dialog.get_config()
            self.create_list_item(self.layout_match_list, config, "layout_match")
    
    def add_absence_trigger(self):
        """Add an absence trigger"""
        dialog = TriggerConfigDialog(self, is_absence_trigger=True)
        if dialog.exec_() == QDialog.Accepted:
            config = dialog.get_config()
            
            # Validate that absence triggers have a timeout > 0
            timeout = config.get('timeout', 0)
            if timeout <= 0:
                # Force a default timeout for absence triggers to prevent workflow freezing
                config['timeout'] = 10
            
            self.create_list_item(self.absence_triggers_list, config, "absence")
    
    def add_ocr_trigger(self):
        """Add an OCR trigger"""
        dialog = OCRTriggerConfigDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            config = dialog.get_config()
            self.create_list_item(self.ocr_triggers_list, config, "ocr")
    
    def add_wait_condition(self):
        """Add a wait condition"""
        dialog = WaitConditionDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            config = dialog.get_config()
            self.create_list_item(self.wait_conditions_list, config, "wait")
    
    def add_pre_conditional_loop(self):
        """Add a pre-sequence conditional loop"""
        dialog = ConditionalLoopDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            config = dialog.get_config()
            self.create_list_item(self.pre_loops_list, config, "loop")
    
    def add_post_conditional_loop(self):
        """Add a post-sequence conditional loop"""
        dialog = ConditionalLoopDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            config = dialog.get_config()
            self.create_list_item(self.post_loops_list, config, "loop")
    
    def remove_selected_item(self, list_widget):
        """Remove selected item from list widget"""
        current_row = list_widget.currentRow()
        if current_row >= 0:
            list_widget.takeItem(current_row)
    
    def load_pre_conditions(self):
        """Load existing pre-conditions"""
        presence_triggers = self.current_config.get('presence_triggers', [])
        for trigger in presence_triggers:
            self.create_list_item(self.presence_triggers_list, trigger, "presence")

        layout_match_conditionals = self.current_config.get('layout_match_conditionals', [])
        for cfg in layout_match_conditionals:
            self.create_list_item(self.layout_match_list, cfg, "layout_match")

        ocr_triggers = self.current_config.get('ocr_triggers', [])
        for trigger in ocr_triggers:
            self.create_list_item(self.ocr_triggers_list, trigger, "ocr")
    
    def load_post_conditions(self):
        """Load existing post-conditions"""
        wait_conditions = self.current_config.get('wait_conditions', [])
        for condition in wait_conditions:
            self.create_list_item(self.wait_conditions_list, condition, "wait")
    
    def load_conditional_loops(self):
        """Load existing conditional loops"""
        pre_loops = self.current_config.get('pre_conditional_loops', [])
        for loop_config in pre_loops:
            self.create_list_item(self.pre_loops_list, loop_config, "loop")
    
    def get_config(self):
        """Get the complete configuration from the dialog"""
        config = {}

        # Human-readable: what this check does (orchestrator routing context).
        try:
            config['description'] = self.description_input.text().strip()
        except Exception:
            pass

        try:
            code_enabled = bool(self.code_enabled_toggle.isChecked()) if hasattr(self, 'code_enabled_toggle') else False
        except Exception:
            code_enabled = False
        if code_enabled:
            code_text = self.code_edit.toPlainText() if hasattr(self, 'code_edit') else ''
            config['code_condition'] = {
                'code': str(code_text),
                'timeout': float(self.code_timeout_spin.value()) if hasattr(self, 'code_timeout_spin') else 5.0
            }
        
        # LLM conditional
        try:
            llm_enabled = bool(self.llm_enabled_toggle.isChecked()) if hasattr(self, 'llm_enabled_toggle') else False
        except Exception:
            llm_enabled = False
        if llm_enabled:
            llm_prompt_text = self.llm_prompt_edit.toPlainText() if hasattr(self, 'llm_prompt_edit') else ''
            use_llamacpp = (self.llm_engine_combo.currentText() == "llama.cpp") if hasattr(self, 'llm_engine_combo') else False
            llm_model = (self.llm_gguf_model.currentText() if use_llamacpp else self.llm_ollama_model.currentText()) if hasattr(self, 'llm_gguf_model') else ''
            config['llm_condition'] = {
                'engine': 'llamacpp' if use_llamacpp else 'ollama',
                'model': str(llm_model),
                'prompt': str(llm_prompt_text),
                'timeout': float(self.llm_timeout_spin.value()) if hasattr(self, 'llm_timeout_spin') else 10.0,
                'use_vision': bool(self.llm_use_vision_toggle.isChecked()) if hasattr(self, 'llm_use_vision_toggle') else False
            }
        
        # Presence triggers
        presence_triggers = []
        for i in range(self.presence_triggers_list.count()):
            item = self.presence_triggers_list.item(i)
            presence_triggers.append(item.data(32))
        if presence_triggers:
            config['presence_triggers'] = presence_triggers
        
        # OCR triggers
        ocr_triggers = []
        for i in range(self.ocr_triggers_list.count()):
            item = self.ocr_triggers_list.item(i)
            ocr_triggers.append(item.data(32))
        if ocr_triggers:
            config['ocr_triggers'] = ocr_triggers

        layout_match_conditionals = []
        for i in range(self.layout_match_list.count()):
            item = self.layout_match_list.item(i)
            layout_match_conditionals.append(item.data(32))
        if layout_match_conditionals:
            config['layout_match_conditionals'] = layout_match_conditionals
        
        # Pre-conditional loops
        pre_loops = []
        for i in range(self.pre_loops_list.count()):
            item = self.pre_loops_list.item(i)
            pre_loops.append(item.data(32))
        if pre_loops:
            config['pre_conditional_loops'] = pre_loops

        # Web-mode condition: when the toggle is on, this drives routing and the
        # desktop groups above are ignored by the runtime (_is_web_conditional).
        try:
            web_enabled = bool(self.web_mode_toggle.isChecked()) if hasattr(self, 'web_mode_toggle') else False
        except Exception:
            web_enabled = False
        if web_enabled:
            config['web_condition'] = self._web_condition_config()

        return config
