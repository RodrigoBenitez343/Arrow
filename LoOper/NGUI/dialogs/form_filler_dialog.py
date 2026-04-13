"""Form Filler dialog classes for the LoOper application.

This module contains the FormFillerDialog class for configuring
Form Filler node properties.
"""

from .base_dialog import ModernDialog
from PyQt5.QtWidgets import QDialog, QVBoxLayout, QLabel, QDialogButtonBox, QLineEdit, QComboBox, QDoubleSpinBox, QFormLayout
from ..constants import TEXT_COLOR
from ..i18n import _
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from AI.model_cache import get_real_cached_models

class FormFillerDialog(ModernDialog):
    """Dialog for configuring Form Filler node properties"""
    
    def __init__(self, parent, current_config=None):
        super().__init__(parent, title=_("Form Filler Configuration"), help_topic="form-filler-node")
        self.current_config = current_config or {}
        
        self.setModal(True)
        self.resize(400, 300)
        
        self.setup_ui()
    
    def setup_ui(self):
        # Use content_layout from ModernDialog
        layout = self.content_layout
        form = QFormLayout()
        
        self.instruction_edit = QLineEdit()
        form.addRow(_("Instruction:"), self.instruction_edit)
        
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        models = get_real_cached_models(timeout=5.0)
        self.model_combo.addItems(models)
        form.addRow(_("Model:"), self.model_combo)
        
        self.confidence_spin = QDoubleSpinBox()
        self.confidence_spin.setRange(0.0, 1.0)
        self.confidence_spin.setSingleStep(0.01)
        form.addRow(_("Confidence Threshold:"), self.confidence_spin)
        
        layout.addLayout(form)
        
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
        
        self.load_current_config()

    def get_config(self):
        return {
            'instruction': self.instruction_edit.text(),
            'model': self.model_combo.currentText(),
            'confidence_threshold': str(self.confidence_spin.value())
        }

    def load_current_config(self):
        self.instruction_edit.setText(self.current_config.get('instruction', ''))
        self.model_combo.setCurrentText(self.current_config.get('model', 'llama3.2:latest'))
        try:
            val = float(self.current_config.get('confidence_threshold', 0.8))
        except Exception:
            val = 0.8
        self.confidence_spin.setValue(val)
