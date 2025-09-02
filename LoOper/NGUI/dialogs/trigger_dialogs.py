"""Trigger-related dialog classes for the LoOper application.

This module contains dialog classes for configuring various types of triggers:
- TriggerConfigDialog: Basic image-based trigger configuration
- OCRTriggerConfigDialog: OCR-based trigger configuration
- WaitConditionDialog: Wait condition configuration
- ConditionalLoopDialog: Conditional loop configuration
"""

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QLineEdit, QPushButton, QHBoxLayout, 
    QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox, QFileDialog, QGridLayout, QWidget
)
from ..constants import TEXT_COLOR


class TriggerConfigDialog(QDialog):
    """Dialog for configuring image-based triggers"""
    
    def __init__(self, parent, is_absence_trigger=False):
        super().__init__(parent)
        self.is_absence_trigger = is_absence_trigger
        title = "Absence Trigger Configuration" if is_absence_trigger else "Trigger Configuration"
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(400, 200)
        self.setup_ui()
    
    def setup_ui(self):
        """Setup the UI"""
        layout = QVBoxLayout(self)
        
        # Image path
        image_layout = QHBoxLayout()
        image_layout.addWidget(QLabel("Image Path:"))
        self.image_path_entry = QLineEdit()
        image_layout.addWidget(self.image_path_entry)
        
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self.browse_image)
        image_layout.addWidget(browse_btn)
        layout.addLayout(image_layout)
        
        # Confidence
        conf_layout = QHBoxLayout()
        conf_layout.addWidget(QLabel("Confidence:"))
        self.confidence_spin = QDoubleSpinBox()
        self.confidence_spin.setRange(0.1, 1.0)
        self.confidence_spin.setSingleStep(0.1)
        self.confidence_spin.setValue(0.8)
        conf_layout.addWidget(self.confidence_spin)
        conf_layout.addStretch()
        layout.addLayout(conf_layout)
        
        # Timeout (optional for presence, required for absence)
        timeout_layout = QHBoxLayout()
        timeout_label = "Timeout (s):" if not self.is_absence_trigger else "Timeout (s) - Required:"
        timeout_layout.addWidget(QLabel(timeout_label))
        self.timeout_spin = QSpinBox()
        
        if self.is_absence_trigger:
            # For absence triggers, enforce minimum timeout to prevent freezing
            self.timeout_spin.setRange(1, 300)
            self.timeout_spin.setValue(10)
        else:
            # For presence triggers, allow no timeout (infinite wait)
            self.timeout_spin.setRange(0, 300)
            self.timeout_spin.setValue(0)
            self.timeout_spin.setSpecialValueText("No timeout")
        
        timeout_layout.addWidget(self.timeout_spin)
        timeout_layout.addStretch()
        layout.addLayout(timeout_layout)
        
        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        ok_btn = QPushButton("OK")
        ok_btn.clicked.connect(self.accept)
        button_layout.addWidget(ok_btn)
        
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)
        
        layout.addLayout(button_layout)
    
    def on_wait_type_changed(self, wait_type):
        """Handle wait type change"""
        if wait_type == "ocr":
            self.image_widget.hide()
            self.wait_time_widget.hide()
            self.ocr_widget.show()
        elif wait_type == "wait_time":
            self.image_widget.hide()
            self.ocr_widget.hide()
            self.wait_time_widget.show()
        else:  # presence or absence
            self.ocr_widget.hide()
            self.wait_time_widget.hide()
            self.image_widget.show()
    
    def browse_image(self):
        """Browse for image file"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Image File", "", "Image Files (*.png *.jpg *.jpeg *.bmp)"
        )
        if file_path:
            self.image_path_entry.setText(file_path)
    
    def get_config(self):
        """Get the trigger configuration"""
        config = {
            'image_path': self.image_path_entry.text(),
            'confidence': self.confidence_spin.value()
        }
        
        # Add timeout if specified
        if self.timeout_spin.value() > 0:
            config['timeout'] = self.timeout_spin.value()
        
        return config


class OCRTriggerConfigDialog(QDialog):
    """Dialog for configuring OCR-based triggers"""
    
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("OCR Trigger Configuration")
        self.setModal(True)
        self.resize(450, 350)
        self.setup_ui()
    
    def setup_ui(self):
        """Setup the UI"""
        layout = QVBoxLayout(self)
        
        # Target text
        text_layout = QHBoxLayout()
        text_layout.addWidget(QLabel("Target Text:"))
        self.target_text_entry = QLineEdit()
        text_layout.addWidget(self.target_text_entry)
        layout.addLayout(text_layout)
        
        # Confidence
        conf_layout = QHBoxLayout()
        conf_layout.addWidget(QLabel("Confidence:"))
        self.confidence_spin = QDoubleSpinBox()
        self.confidence_spin.setRange(0.1, 1.0)
        self.confidence_spin.setSingleStep(0.1)
        self.confidence_spin.setValue(0.8)
        conf_layout.addWidget(self.confidence_spin)
        conf_layout.addStretch()
        layout.addLayout(conf_layout)
        
        # Case sensitive
        case_layout = QHBoxLayout()
        self.case_sensitive_check = QCheckBox("Case Sensitive")
        case_layout.addWidget(self.case_sensitive_check)
        case_layout.addStretch()
        layout.addLayout(case_layout)
        
        # Note: Language and region removed - OCR now uses full screen with default English
        
        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        ok_btn = QPushButton("OK")
        ok_btn.clicked.connect(self.accept)
        button_layout.addWidget(ok_btn)
        
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)
        
        layout.addLayout(button_layout)
    
    def get_config(self):
        """Get the OCR trigger configuration"""
        config = {
            'target_text': self.target_text_entry.text(),
            'confidence': self.confidence_spin.value(),
            'case_sensitive': self.case_sensitive_check.isChecked()
        }
        
        return config


class WaitConditionDialog(QDialog):
    """Dialog for configuring wait conditions"""
    
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Wait Condition")
        self.setModal(True)
        self.resize(400, 250)
        self.setup_ui()
    
    def setup_ui(self):
        """Setup the UI"""
        layout = QVBoxLayout(self)
        
        # Condition type
        type_layout = QHBoxLayout()
        type_layout.addWidget(QLabel("Wait Type:"))
        self.type_combo = QComboBox()
        self.type_combo.addItems(["presence", "absence", "ocr", "wait_time"])
        self.type_combo.currentTextChanged.connect(self.on_wait_type_changed)
        type_layout.addWidget(self.type_combo)
        type_layout.addStretch()
        layout.addLayout(type_layout)
        
        # Image path (for presence/absence)
        self.image_widget = QWidget()
        image_layout = QHBoxLayout(self.image_widget)
        image_layout.setContentsMargins(0, 0, 0, 0)
        image_layout.addWidget(QLabel("Image Path:"))
        self.image_path_entry = QLineEdit()
        image_layout.addWidget(self.image_path_entry)
        
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self.browse_image)
        image_layout.addWidget(browse_btn)
        layout.addWidget(self.image_widget)
        
        # OCR settings (for OCR)
        self.ocr_widget = QWidget()
        ocr_layout = QVBoxLayout(self.ocr_widget)
        ocr_layout.setContentsMargins(0, 0, 0, 0)
        
        text_layout = QHBoxLayout()
        text_layout.addWidget(QLabel("Target Text:"))
        self.target_text_entry = QLineEdit()
        text_layout.addWidget(self.target_text_entry)
        ocr_layout.addLayout(text_layout)
        
        ocr_params_layout = QHBoxLayout()
        ocr_params_layout.addWidget(QLabel("Case Sensitive:"))
        self.case_sensitive_check = QCheckBox()
        self.case_sensitive_check.setChecked(True)
        ocr_params_layout.addWidget(self.case_sensitive_check)
        
        # Language removed - OCR now uses default English
        ocr_params_layout.addStretch()
        ocr_layout.addLayout(ocr_params_layout)
        
        layout.addWidget(self.ocr_widget)
        self.ocr_widget.hide()  # Initially hidden
        
        # Wait time settings (for wait_time)
        self.wait_time_widget = QWidget()
        wait_time_layout = QHBoxLayout(self.wait_time_widget)
        wait_time_layout.setContentsMargins(0, 0, 0, 0)
        wait_time_layout.addWidget(QLabel("Wait Duration (s):"))
        self.wait_duration_spin = QDoubleSpinBox()
        self.wait_duration_spin.setRange(0.1, 300.0)
        self.wait_duration_spin.setSingleStep(0.1)
        self.wait_duration_spin.setValue(5.0)
        wait_time_layout.addWidget(self.wait_duration_spin)
        wait_time_layout.addStretch()
        layout.addWidget(self.wait_time_widget)
        self.wait_time_widget.hide()  # Initially hidden
        
        # Confidence
        conf_layout = QHBoxLayout()
        conf_layout.addWidget(QLabel("Confidence:"))
        self.confidence_spin = QDoubleSpinBox()
        self.confidence_spin.setRange(0.1, 1.0)
        self.confidence_spin.setSingleStep(0.1)
        self.confidence_spin.setValue(0.8)
        conf_layout.addWidget(self.confidence_spin)
        conf_layout.addStretch()
        layout.addLayout(conf_layout)
        
        # Timeout
        timeout_layout = QHBoxLayout()
        timeout_layout.addWidget(QLabel("Timeout (s):"))
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(1, 300)
        self.timeout_spin.setValue(30)
        timeout_layout.addWidget(self.timeout_spin)
        timeout_layout.addStretch()
        layout.addLayout(timeout_layout)
        
        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        ok_btn = QPushButton("OK")
        ok_btn.clicked.connect(self.accept)
        button_layout.addWidget(ok_btn)
        
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)
        
        layout.addLayout(button_layout)
    
    def browse_image(self):
        """Browse for image file"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Image File", "", "Image Files (*.png *.jpg *.jpeg *.bmp)"
        )
        if file_path:
            self.image_path_entry.setText(file_path)
    
    def get_config(self):
        """Get the wait condition configuration"""
        wait_type = self.type_combo.currentText()
        
        if wait_type == "ocr":
            return {
                'type': 'ocr',
                'target_text': self.target_text_entry.text(),
                'confidence': self.confidence_spin.value(),
                'case_sensitive': self.case_sensitive_check.isChecked(),
                'timeout': self.timeout_spin.value()
            }
        elif wait_type == "wait_time":
            return {
                'type': 'wait_time',
                'duration': self.wait_duration_spin.value()
            }
        else:  # presence or absence
            return {
                'type': wait_type,
                'image_path': self.image_path_entry.text(),
                'confidence': self.confidence_spin.value(),
                'timeout': self.timeout_spin.value()
            }


class ConditionalLoopDialog(QDialog):
    """Dialog for configuring conditional loops"""
    
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Conditional Loop")
        self.setModal(True)
        self.resize(500, 350)
        self.setup_ui()
    
    def setup_ui(self):
        """Setup the UI"""
        layout = QVBoxLayout(self)
        
        # Loop type
        type_layout = QHBoxLayout()
        type_layout.addWidget(QLabel("Loop Type:"))
        self.type_combo = QComboBox()
        self.type_combo.addItems(["while_present", "while_absent", "until_present", "until_absent"])
        self.type_combo.currentTextChanged.connect(self.on_loop_type_changed)
        type_layout.addWidget(self.type_combo)
        type_layout.addStretch()
        layout.addLayout(type_layout)
        
        # Sequence file
        seq_layout = QHBoxLayout()
        seq_layout.addWidget(QLabel("Sequence File:"))
        self.sequence_file_entry = QLineEdit()
        seq_layout.addWidget(self.sequence_file_entry)
        
        browse_seq_btn = QPushButton("Browse...")
        browse_seq_btn.clicked.connect(self.browse_sequence)
        seq_layout.addWidget(browse_seq_btn)
        layout.addLayout(seq_layout)
        
        # Condition method (image or OCR)
        cond_method_layout = QHBoxLayout()
        cond_method_layout.addWidget(QLabel("Condition Method:"))
        self.condition_method_combo = QComboBox()
        self.condition_method_combo.addItems(["image", "ocr"])
        self.condition_method_combo.currentTextChanged.connect(self.on_condition_method_changed)
        cond_method_layout.addWidget(self.condition_method_combo)
        cond_method_layout.addStretch()
        layout.addLayout(cond_method_layout)
        
        # Condition image (for presence/absence)
        self.image_widget = QWidget()
        image_layout = QHBoxLayout(self.image_widget)
        image_layout.setContentsMargins(0, 0, 0, 0)
        image_layout.addWidget(QLabel("Condition Image:"))
        self.image_path_entry = QLineEdit()
        image_layout.addWidget(self.image_path_entry)
        
        browse_img_btn = QPushButton("Browse...")
        browse_img_btn.clicked.connect(self.browse_image)
        image_layout.addWidget(browse_img_btn)
        layout.addWidget(self.image_widget)
        
        # OCR condition (for OCR)
        self.ocr_widget = QWidget()
        ocr_layout = QVBoxLayout(self.ocr_widget)
        ocr_layout.setContentsMargins(0, 0, 0, 0)
        
        text_layout = QHBoxLayout()
        text_layout.addWidget(QLabel("Target Text:"))
        self.target_text_entry = QLineEdit()
        text_layout.addWidget(self.target_text_entry)
        ocr_layout.addLayout(text_layout)
        
        ocr_params_layout = QHBoxLayout()
        ocr_params_layout.addWidget(QLabel("Case Sensitive:"))
        self.case_sensitive_check = QCheckBox()
        self.case_sensitive_check.setChecked(True)
        ocr_params_layout.addWidget(self.case_sensitive_check)
        
        # Language removed - OCR now uses default English
        ocr_params_layout.addStretch()
        ocr_layout.addLayout(ocr_params_layout)
        
        layout.addWidget(self.ocr_widget)
        self.ocr_widget.hide()  # Initially hidden
        
        # Parameters
        params_layout = QGridLayout()
        
        params_layout.addWidget(QLabel("Max Iterations:"), 0, 0)
        self.max_iterations_spin = QSpinBox()
        self.max_iterations_spin.setRange(1, 100)
        self.max_iterations_spin.setValue(10)
        params_layout.addWidget(self.max_iterations_spin, 0, 1)
        
        params_layout.addWidget(QLabel("Iteration Delay (s):"), 1, 0)
        self.iteration_delay_spin = QDoubleSpinBox()
        self.iteration_delay_spin.setRange(0.1, 60.0)
        self.iteration_delay_spin.setSingleStep(0.1)
        self.iteration_delay_spin.setValue(1.0)
        params_layout.addWidget(self.iteration_delay_spin, 1, 1)
        
        params_layout.addWidget(QLabel("Confidence:"), 2, 0)
        self.confidence_spin = QDoubleSpinBox()
        self.confidence_spin.setRange(0.1, 1.0)
        self.confidence_spin.setSingleStep(0.1)
        self.confidence_spin.setValue(0.8)
        params_layout.addWidget(self.confidence_spin, 2, 1)
        
        layout.addLayout(params_layout)
        
        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        
        ok_btn = QPushButton("OK")
        ok_btn.clicked.connect(self.accept)
        button_layout.addWidget(ok_btn)
        
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)
        
        layout.addLayout(button_layout)
    
    def on_loop_type_changed(self, loop_type):
        """Handle loop type change"""
        # Update UI labels based on loop type
        if loop_type in ["while_present", "until_present"]:
            # For presence-based loops, we're looking for something to appear
            pass
        elif loop_type in ["while_absent", "until_absent"]:
            # For absence-based loops, we're looking for something to disappear
            pass
    
    def on_condition_method_changed(self, condition_method):
        """Handle condition method change"""
        if condition_method == "ocr":
            self.image_widget.hide()
            self.ocr_widget.show()
        else:
            self.ocr_widget.hide()
            self.image_widget.show()
    
    def browse_sequence(self):
        """Browse for sequence file"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Sequence File", "", "JSON Files (*.json)"
        )
        if file_path:
            self.sequence_file_entry.setText(file_path)
    
    def browse_image(self):
        """Browse for image file"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Image File", "", "Image Files (*.png *.jpg *.jpeg *.bmp)"
        )
        if file_path:
            self.image_path_entry.setText(file_path)
    
    def get_config(self):
        """Get the conditional loop configuration"""
        loop_type = self.type_combo.currentText()
        condition_method = self.condition_method_combo.currentText()
        
        # Create unified condition based on loop type and method
        if condition_method == "ocr":
            # For OCR-based conditions, map loop type to OCR trigger type
            condition = {
                'trigger_type': 'ocr',
                'target_text': self.target_text_entry.text(),
                'confidence': self.confidence_spin.value(),
                'case_sensitive': self.case_sensitive_check.isChecked()
            }
        else:
            # For image-based conditions, map loop type to presence/absence trigger type
            if loop_type in ["while_present", "until_present"]:
                trigger_type = 'presence'
            else:  # while_absent, until_absent
                trigger_type = 'absence'
            
            condition = {
                'trigger_type': trigger_type,
                'image_path': self.image_path_entry.text(),
                'confidence': self.confidence_spin.value()
            }
        
        return {
            'type': loop_type,
            'sequence_file': self.sequence_file_entry.text(),
            'max_iterations': self.max_iterations_spin.value(),
            'iteration_delay': self.iteration_delay_spin.value(),
            'condition': condition
        }