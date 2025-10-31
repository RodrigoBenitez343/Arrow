"""Conditional dialog classes for the LoOper application.

This module contains the AdvancedConditionalDialog class for configuring
complex conditional logic including pre-conditions, post-conditions, and
conditional loops.
"""

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QLineEdit, QPushButton, QHBoxLayout, 
    QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox, QWidget, QFileDialog,
    QListWidgetItem, QTextEdit, QGroupBox, QListWidget
)
from ..constants import TEXT_COLOR
from .trigger_dialogs import TriggerConfigDialog, OCRTriggerConfigDialog, ConditionalLoopDialog


class AdvancedConditionalDialog(QDialog):
    """Dialog for configuring advanced conditional fallbacks and automation logic"""
    
    def __init__(self, parent, sequence_idx, chain_config, current_config=None):
        super().__init__(parent)
        self.sequence_idx = sequence_idx
        self.chain_config = chain_config
        self.current_config = current_config or {}
        
        self.setWindowTitle(f"Advanced Conditions - Sequence {sequence_idx + 1}")
        self.setModal(True)
        self.resize(800, 700)  # Increased size for better visibility
        self.setMinimumSize(750, 650)  # Set minimum size to prevent content clipping
        
        # Apply styling for better visibility
        self.setStyleSheet(f"""
            QDialog {{
                background-color: #2b2b2b;
                color: {TEXT_COLOR};
            }}
            QTabWidget::pane {{
                border: 1px solid #555;
                background-color: #2b2b2b;
            }}
            QTabBar::tab {{
                background-color: #3c3c3c;
                color: {TEXT_COLOR};
                padding: 8px 16px;
                margin-right: 2px;
                border: 1px solid #555;
                border-bottom: none;
            }}
            QTabBar::tab:selected {{
                background-color: #4a4a4a;
                border-bottom: 1px solid #4a4a4a;
            }}
            QGroupBox {{
                font-weight: bold;
                border: 2px solid #555;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
                color: {TEXT_COLOR};
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
            }}
            QListWidget {{
                background-color: #3c3c3c;
                border: 1px solid #555;
                color: {TEXT_COLOR};
                min-height: 120px;
            }}
            QPushButton {{
                background-color: #4a4a4a;
                border: 1px solid #666;
                color: {TEXT_COLOR};
                padding: 6px 12px;
                border-radius: 3px;
            }}
            QPushButton:hover {{
                background-color: #5a5a5a;
            }}
            QPushButton:pressed {{
                background-color: #3a3a3a;
            }}
        """)
        
        self.setup_ui()
    
    def setup_ui(self):
        """Setup the UI for the advanced conditional dialog"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)  # Add proper margins
        layout.setSpacing(10)  # Add spacing between elements
        
        # Unified dialog without tabs: presence triggers, OCR triggers, and pre-sequence loops
        self.setup_unified_conditions(layout)
        
        # Buttons
        button_layout = QHBoxLayout()
        button_layout.setSpacing(10)  # Add spacing between buttons
        button_layout.addStretch()
        
        save_btn = QPushButton("Save")
        save_btn.setMinimumSize(80, 30)  # Set minimum button size
        save_btn.clicked.connect(self.accept)
        button_layout.addWidget(save_btn)
        
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setMinimumSize(80, 30)  # Set minimum button size
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)
        
        layout.addLayout(button_layout)

    def setup_unified_conditions(self, parent_layout: QVBoxLayout):
        """Setup a unified layout with the supported options (no tabs)."""
        # Presence triggers
        presence_group = QGroupBox("Presence Triggers (must be present to execute)")
        presence_layout = QVBoxLayout(presence_group)
        presence_layout.setContentsMargins(10, 15, 10, 10)
        presence_layout.setSpacing(8)

        self.presence_triggers_list = QListWidget()
        self.presence_triggers_list.setMinimumHeight(120)
        presence_layout.addWidget(self.presence_triggers_list)

        presence_buttons = QHBoxLayout()
        presence_buttons.setSpacing(8)
        add_presence_btn = QPushButton("Add Presence Trigger")
        add_presence_btn.setMinimumHeight(30)
        add_presence_btn.clicked.connect(self.add_presence_trigger)
        presence_buttons.addWidget(add_presence_btn)

        remove_presence_btn = QPushButton("Remove Selected")
        remove_presence_btn.setMinimumHeight(30)
        remove_presence_btn.clicked.connect(lambda: self.remove_selected_item(self.presence_triggers_list))
        presence_buttons.addWidget(remove_presence_btn)
        presence_buttons.addStretch()

        presence_layout.addLayout(presence_buttons)
        parent_layout.addWidget(presence_group)

        # OCR triggers
        ocr_group = QGroupBox("OCR Triggers (text must be present to execute)")
        ocr_layout = QVBoxLayout(ocr_group)
        ocr_layout.setContentsMargins(10, 15, 10, 10)
        ocr_layout.setSpacing(8)

        self.ocr_triggers_list = QListWidget()
        self.ocr_triggers_list.setMinimumHeight(120)
        ocr_layout.addWidget(self.ocr_triggers_list)

        ocr_buttons = QHBoxLayout()
        ocr_buttons.setSpacing(8)
        add_ocr_btn = QPushButton("Add OCR Trigger")
        add_ocr_btn.setMinimumHeight(30)
        add_ocr_btn.clicked.connect(self.add_ocr_trigger)
        ocr_buttons.addWidget(add_ocr_btn)

        remove_ocr_btn = QPushButton("Remove Selected")
        remove_ocr_btn.setMinimumHeight(30)
        remove_ocr_btn.clicked.connect(lambda: self.remove_selected_item(self.ocr_triggers_list))
        ocr_buttons.addWidget(remove_ocr_btn)
        ocr_buttons.addStretch()

        ocr_layout.addLayout(ocr_buttons)
        parent_layout.addWidget(ocr_group)

        # Pre-sequence conditional loops
        pre_loops_group = QGroupBox("Pre-Sequence Conditional Loops")
        pre_loops_layout = QVBoxLayout(pre_loops_group)
        pre_loops_layout.setContentsMargins(10, 15, 10, 10)
        pre_loops_layout.setSpacing(8)

        self.pre_loops_list = QListWidget()
        self.pre_loops_list.setMinimumHeight(120)
        pre_loops_layout.addWidget(self.pre_loops_list)

        pre_loops_buttons = QHBoxLayout()
        pre_loops_buttons.setSpacing(8)
        add_pre_loop_btn = QPushButton("Add Pre-Loop")
        add_pre_loop_btn.setMinimumHeight(30)
        add_pre_loop_btn.clicked.connect(self.add_pre_conditional_loop)
        pre_loops_buttons.addWidget(add_pre_loop_btn)

        remove_pre_loop_btn = QPushButton("Remove Selected")
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
        self.tab_widget.addTab(tab, "Pre-Conditions")
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(15)
        
        # Presence triggers
        presence_group = QGroupBox("Presence Triggers (must be present to execute)")
        presence_layout = QVBoxLayout(presence_group)
        presence_layout.setContentsMargins(10, 15, 10, 10)
        presence_layout.setSpacing(8)
        
        self.presence_triggers_list = QListWidget()
        self.presence_triggers_list.setMinimumHeight(120)
        presence_layout.addWidget(self.presence_triggers_list)
        
        presence_buttons = QHBoxLayout()
        presence_buttons.setSpacing(8)
        add_presence_btn = QPushButton("Add Presence Trigger")
        add_presence_btn.setMinimumHeight(30)
        add_presence_btn.clicked.connect(self.add_presence_trigger)
        presence_buttons.addWidget(add_presence_btn)
        
        remove_presence_btn = QPushButton("Remove Selected")
        remove_presence_btn.setMinimumHeight(30)
        remove_presence_btn.clicked.connect(lambda: self.remove_selected_item(self.presence_triggers_list))
        presence_buttons.addWidget(remove_presence_btn)
        presence_buttons.addStretch()
        
        presence_layout.addLayout(presence_buttons)
        layout.addWidget(presence_group)
        
        # Absence triggers
        absence_group = QGroupBox("Absence Triggers (must be absent to execute)")
        absence_layout = QVBoxLayout(absence_group)
        absence_layout.setContentsMargins(10, 15, 10, 10)
        absence_layout.setSpacing(8)
        
        self.absence_triggers_list = QListWidget()
        self.absence_triggers_list.setMinimumHeight(120)
        absence_layout.addWidget(self.absence_triggers_list)
        
        absence_buttons = QHBoxLayout()
        absence_buttons.setSpacing(8)
        add_absence_btn = QPushButton("Add Absence Trigger")
        add_absence_btn.setMinimumHeight(30)
        add_absence_btn.clicked.connect(self.add_absence_trigger)
        absence_buttons.addWidget(add_absence_btn)
        
        remove_absence_btn = QPushButton("Remove Selected")
        remove_absence_btn.setMinimumHeight(30)
        remove_absence_btn.clicked.connect(lambda: self.remove_selected_item(self.absence_triggers_list))
        absence_buttons.addWidget(remove_absence_btn)
        absence_buttons.addStretch()
        
        absence_layout.addLayout(absence_buttons)
        layout.addWidget(absence_group)
        
        # OCR triggers
        ocr_group = QGroupBox("OCR Triggers (text must be present to execute)")
        ocr_layout = QVBoxLayout(ocr_group)
        ocr_layout.setContentsMargins(10, 15, 10, 10)
        ocr_layout.setSpacing(8)
        
        self.ocr_triggers_list = QListWidget()
        self.ocr_triggers_list.setMinimumHeight(120)
        ocr_layout.addWidget(self.ocr_triggers_list)
        
        ocr_buttons = QHBoxLayout()
        ocr_buttons.setSpacing(8)
        add_ocr_btn = QPushButton("Add OCR Trigger")
        add_ocr_btn.setMinimumHeight(30)
        add_ocr_btn.clicked.connect(self.add_ocr_trigger)
        ocr_buttons.addWidget(add_ocr_btn)
        
        remove_ocr_btn = QPushButton("Remove Selected")
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
        self.tab_widget.addTab(tab, "Post-Conditions")
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(15)
        
        # Wait conditions
        wait_group = QGroupBox("Wait Conditions (wait after sequence execution)")
        wait_layout = QVBoxLayout(wait_group)
        wait_layout.setContentsMargins(10, 15, 10, 10)
        wait_layout.setSpacing(8)
        
        self.wait_conditions_list = QListWidget()
        self.wait_conditions_list.setMinimumHeight(200)  # Larger height for this tab
        wait_layout.addWidget(self.wait_conditions_list)
        
        wait_buttons = QHBoxLayout()
        wait_buttons.setSpacing(8)
        add_wait_btn = QPushButton("Add Wait Condition")
        add_wait_btn.setMinimumHeight(30)
        add_wait_btn.clicked.connect(self.add_wait_condition)
        wait_buttons.addWidget(add_wait_btn)
        
        remove_wait_btn = QPushButton("Remove Selected")
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
        self.tab_widget.addTab(tab, "Conditional Loops")
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(15)
        
        # Pre-sequence loops
        pre_loops_group = QGroupBox("Pre-Sequence Conditional Loops")
        pre_loops_layout = QVBoxLayout(pre_loops_group)
        pre_loops_layout.setContentsMargins(10, 15, 10, 10)
        pre_loops_layout.setSpacing(8)
        
        self.pre_loops_list = QListWidget()
        self.pre_loops_list.setMinimumHeight(120)
        pre_loops_layout.addWidget(self.pre_loops_list)
        
        pre_loops_buttons = QHBoxLayout()
        pre_loops_buttons.setSpacing(8)
        add_pre_loop_btn = QPushButton("Add Pre-Loop")
        add_pre_loop_btn.setMinimumHeight(30)
        add_pre_loop_btn.clicked.connect(self.add_pre_conditional_loop)
        pre_loops_buttons.addWidget(add_pre_loop_btn)
        
        remove_pre_loop_btn = QPushButton("Remove Selected")
        remove_pre_loop_btn.setMinimumHeight(30)
        remove_pre_loop_btn.clicked.connect(lambda: self.remove_selected_item(self.pre_loops_list))
        pre_loops_buttons.addWidget(remove_pre_loop_btn)
        pre_loops_buttons.addStretch()
        
        pre_loops_layout.addLayout(pre_loops_buttons)
        layout.addWidget(pre_loops_group)
        
        # Post-sequence loops
        post_loops_group = QGroupBox("Post-Sequence Conditional Loops")
        post_loops_layout = QVBoxLayout(post_loops_group)
        post_loops_layout.setContentsMargins(10, 15, 10, 10)
        post_loops_layout.setSpacing(8)
        
        self.post_loops_list = QListWidget()
        self.post_loops_list.setMinimumHeight(120)
        post_loops_layout.addWidget(self.post_loops_list)
        
        post_loops_buttons = QHBoxLayout()
        post_loops_buttons.setSpacing(8)
        add_post_loop_btn = QPushButton("Add Post-Loop")
        add_post_loop_btn.setMinimumHeight(30)
        add_post_loop_btn.clicked.connect(self.add_post_conditional_loop)
        post_loops_buttons.addWidget(add_post_loop_btn)
        
        remove_post_loop_btn = QPushButton("Remove Selected")
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
            item = QListWidgetItem(f"Image: {config['image_path']} (Confidence: {config['confidence']})")
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
            
            item = QListWidgetItem(f"Image: {config['image_path']} (Timeout: {timeout}s)")
            item.setData(32, config)
            self.absence_triggers_list.addItem(item)
    
    def add_ocr_trigger(self):
        """Add an OCR trigger"""
        dialog = OCRTriggerConfigDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            config = dialog.get_config()
            item = QListWidgetItem(f"Text: '{config['target_text']}' (Confidence: {config['confidence']})")
            item.setData(32, config)
            self.ocr_triggers_list.addItem(item)
    
    def add_wait_condition(self):
        """Add a wait condition"""
        dialog = WaitConditionDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            config = dialog.get_config()
            condition_type = config['type']
            timeout = config.get('timeout', 30)
            item = QListWidgetItem(f"Wait for {condition_type}: {config['image_path']} (Timeout: {timeout}s)")
            item.setData(32, config)
            self.wait_conditions_list.addItem(item)
    
    def add_pre_conditional_loop(self):
        """Add a pre-sequence conditional loop"""
        dialog = ConditionalLoopDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            config = dialog.get_config()
            loop_type = config['type']
            max_iter = config['max_iterations']
            item = QListWidgetItem(f"{loop_type} loop: {config['sequence_file']} (Max: {max_iter})")
            item.setData(32, config)
            self.pre_loops_list.addItem(item)
    
    def add_post_conditional_loop(self):
        """Add a post-sequence conditional loop"""
        dialog = ConditionalLoopDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            config = dialog.get_config()
            loop_type = config['type']
            max_iter = config['max_iterations']
            item = QListWidgetItem(f"{loop_type} loop: {config['sequence_file']} (Max: {max_iter})")
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
            item = QListWidgetItem(f"Image: {trigger['image_path']} (Confidence: {trigger['confidence']})")
            item.setData(32, trigger)
            self.presence_triggers_list.addItem(item)

        ocr_triggers = self.current_config.get('ocr_triggers', [])
        for trigger in ocr_triggers:
            item = QListWidgetItem(f"Text: '{trigger['target_text']}' (Confidence: {trigger['confidence']})")
            item.setData(32, trigger)
            self.ocr_triggers_list.addItem(item)
    
    def load_post_conditions(self):
        """Load existing post-conditions"""
        wait_conditions = self.current_config.get('wait_conditions', [])
        for condition in wait_conditions:
            condition_type = condition['type']
            timeout = condition.get('timeout', 30)
            item = QListWidgetItem(f"Wait for {condition_type}: {condition['image_path']} (Timeout: {timeout}s)")
            item.setData(32, condition)
            self.wait_conditions_list.addItem(item)
    
    def load_conditional_loops(self):
        """Load existing conditional loops"""
        pre_loops = self.current_config.get('pre_conditional_loops', [])
        for loop_config in pre_loops:
            loop_type = loop_config['type']
            max_iter = loop_config['max_iterations']
            item = QListWidgetItem(f"{loop_type} loop: {loop_config['sequence_file']} (Max: {max_iter})")
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