"""Conditional dialog classes for the LoOper application.

This module contains the AdvancedConditionalDialog class for configuring
complex conditional logic including pre-conditions, post-conditions, and
conditional loops.
"""

from .base_dialog import ModernDialog
from PyQt5.QtWidgets import (
    QVBoxLayout, QLabel, QLineEdit, QPushButton, QHBoxLayout, 
    QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox, QWidget, QFileDialog,
    QListWidgetItem, QTextEdit, QGroupBox, QListWidget
)
from ..constants import TEXT_COLOR
from .trigger_dialogs import TriggerConfigDialog, OCRTriggerConfigDialog, ConditionalLoopDialog
from ..i18n import _


class AdvancedConditionalDialog(ModernDialog):
    """Dialog for configuring advanced conditional fallbacks and automation logic"""
    
    def __init__(self, parent, sequence_idx, chain_config, current_config=None):
        title = _("Advanced Conditions - Sequence {num}").format(num=sequence_idx + 1)
        super().__init__(parent, title=title)
        self.sequence_idx = sequence_idx
        self.chain_config = chain_config
        self.current_config = current_config or {}
        
        self.setModal(True)
        self.resize(800, 700)  # Increased size for better visibility
        self.setMinimumSize(750, 650)  # Set minimum size to prevent content clipping
        
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
        parent_layout.addWidget(presence_group)

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
        parent_layout.addWidget(ocr_group)

        # Pre-sequence conditional loops
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
        parent_layout.addWidget(pre_loops_group)

        # Load existing data for the supported options
        self.load_pre_conditions()
        self.load_conditional_loops()
    
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
    
    def add_presence_trigger(self):
        """Add a presence trigger"""
        dialog = TriggerConfigDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            config = dialog.get_config()
            item = QListWidgetItem(_("Image: {path} (Confidence: {conf})").format(path=config['image_path'], conf=config['confidence']))
            item.setData(32, config)  # Store config in item data
            self.presence_triggers_list.addItem(item)
    
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
                timeout = 10
            
            item = QListWidgetItem(_("Image: {path} (Timeout: {sec}s)").format(path=config['image_path'], sec=timeout))
            item.setData(32, config)
            self.absence_triggers_list.addItem(item)
    
    def add_ocr_trigger(self):
        """Add an OCR trigger"""
        dialog = OCRTriggerConfigDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            config = dialog.get_config()
            item = QListWidgetItem(_("Text: '{text}' (Confidence: {conf})").format(text=config['target_text'], conf=config['confidence']))
            item.setData(32, config)
            self.ocr_triggers_list.addItem(item)
    
    def add_wait_condition(self):
        """Add a wait condition"""
        dialog = WaitConditionDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            config = dialog.get_config()
            condition_type = config['type']
            timeout = config.get('timeout', 30)
            ctype_label = _(condition_type)
            item = QListWidgetItem(_("Wait for {ctype}: {path} (Timeout: {sec}s)").format(ctype=ctype_label, path=config['image_path'], sec=timeout))
            item.setData(32, config)
            self.wait_conditions_list.addItem(item)
    
    def add_pre_conditional_loop(self):
        """Add a pre-sequence conditional loop"""
        dialog = ConditionalLoopDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            config = dialog.get_config()
            loop_type = config['type']
            max_iter = config['max_iterations']
            item = QListWidgetItem(_("{loop} loop: {seq} (Max: {max})").format(loop=loop_type, seq=config['sequence_file'], max=max_iter))
            item.setData(32, config)
            self.pre_loops_list.addItem(item)
    
    def add_post_conditional_loop(self):
        """Add a post-sequence conditional loop"""
        dialog = ConditionalLoopDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            config = dialog.get_config()
            loop_type = config['type']
            max_iter = config['max_iterations']
            item = QListWidgetItem(_("{loop} loop: {seq} (Max: {max})").format(loop=loop_type, seq=config['sequence_file'], max=max_iter))
            item.setData(32, config)
            self.post_loops_list.addItem(item)
    
    def remove_selected_item(self, list_widget):
        """Remove selected item from list widget"""
        current_row = list_widget.currentRow()
        if current_row >= 0:
            list_widget.takeItem(current_row)
    
    def load_pre_conditions(self):
        """Load existing pre-conditions"""
        presence_triggers = self.current_config.get('presence_triggers', [])
        for trigger in presence_triggers:
            item = QListWidgetItem(_("Image: {path} (Confidence: {conf})").format(path=trigger['image_path'], conf=trigger['confidence']))
            item.setData(32, trigger)
            self.presence_triggers_list.addItem(item)

        ocr_triggers = self.current_config.get('ocr_triggers', [])
        for trigger in ocr_triggers:
            item = QListWidgetItem(_("Text: '{text}' (Confidence: {conf})").format(text=trigger['target_text'], conf=trigger['confidence']))
            item.setData(32, trigger)
            self.ocr_triggers_list.addItem(item)
    
    def load_post_conditions(self):
        """Load existing post-conditions"""
        wait_conditions = self.current_config.get('wait_conditions', [])
        for condition in wait_conditions:
            condition_type = condition['type']
            timeout = condition.get('timeout', 30)
            ctype_label = _(condition_type)
            item = QListWidgetItem(_("Wait for {ctype}: {path} (Timeout: {sec}s)").format(ctype=ctype_label, path=condition['image_path'], sec=timeout))
            item.setData(32, condition)
            self.wait_conditions_list.addItem(item)
    
    def load_conditional_loops(self):
        """Load existing conditional loops"""
        pre_loops = self.current_config.get('pre_conditional_loops', [])
        for loop_config in pre_loops:
            loop_type = loop_config['type']
            max_iter = loop_config['max_iterations']
            item = QListWidgetItem(_("{loop} loop: {seq} (Max: {max})").format(loop=loop_type, seq=loop_config['sequence_file'], max=max_iter))
            item.setData(32, loop_config)
            self.pre_loops_list.addItem(item)
    
    def get_config(self):
        """Get the complete configuration from the dialog"""
        config = {}
        
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
        
        # Pre-conditional loops
        pre_loops = []
        for i in range(self.pre_loops_list.count()):
            item = self.pre_loops_list.item(i)
            pre_loops.append(item.data(32))
        if pre_loops:
            config['pre_conditional_loops'] = pre_loops
        
        return config