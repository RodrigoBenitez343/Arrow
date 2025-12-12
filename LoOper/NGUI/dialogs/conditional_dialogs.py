"""Conditional dialog classes for the LoOper application.

This module contains the AdvancedConditionalDialog class for configuring
complex conditional logic including pre-conditions, post-conditions, and
conditional loops.
"""

from .base_dialog import ModernDialog
from .toggle_switch import ModernToggle
from PyQt5.QtWidgets import (
    QVBoxLayout, QLabel, QLineEdit, QPushButton, QHBoxLayout, 
    QComboBox, QSpinBox, QDoubleSpinBox, QWidget, QFileDialog,
    QListWidgetItem, QTextEdit, QGroupBox, QListWidget, QDialog,
    QFrame
)
from PyQt5.QtCore import Qt, QSize
from PyQt5.QtGui import QPixmap
from ..constants import TEXT_COLOR
from .trigger_dialogs import TriggerConfigDialog, OCRTriggerConfigDialog, ConditionalLoopDialog, WaitConditionDialog
from ..i18n import _


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
                background-color: #2b2b2b;
                border: 1px solid #3d3d3d;
                border-radius: 8px;
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
        preview_label.setStyleSheet("background-color: #1e1e1e; border-radius: 4px;")
        
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
            timeout_str = f", Timeout: {timeout}s" if timeout > 0 else ""
            info_text = f"<b>{_('Image Trigger')}</b><br>{image_path}<br>Confidence: {conf}{timeout_str}"
            
        elif self.condition_type == "ocr":
            preview_label.setText("OCR")
            text = self.config.get('target_text', '')
            conf = self.config.get('confidence', 0.8)
            info_text = f"<b>{_('OCR Trigger')}</b><br>Text: '{text}'<br>Confidence: {conf}"
            
        elif self.condition_type == "loop":
            preview_label.setText("LOOP")
            loop_type = self.config.get('type', '')
            seq = self.config.get('sequence_file', '')
            max_iter = self.config.get('max_iterations', 10)
            info_text = f"<b>{_('Conditional Loop')}</b><br>Type: {loop_type}<br>Sequence: {seq}<br>Max Iterations: {max_iter}"
            
        elif self.condition_type == "wait":
             preview_label.setText("WAIT")
             wtype = self.config.get('type', 'wait_time')
             if wtype == 'wait_time':
                 duration = self.config.get('duration', 5.0)
                 info_text = f"<b>{_('Wait Condition')}</b><br>Wait Time: {duration}s"
             else:
                 timeout = self.config.get('timeout', 30)
                 info_text = f"<b>{_('Wait Condition')}</b><br>Type: {wtype}, Timeout: {timeout}s"

        layout.addWidget(preview_label)
        
        info_label = QLabel(info_text)
        info_label.setWordWrap(True)
        layout.addWidget(info_label)
        layout.addStretch()


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