# dialogs/sequence_dialogs.py

from .base_dialog import ModernDialog
from PyQt5.QtWidgets import QVBoxLayout, QLabel, QLineEdit, QDialogButtonBox
from ..i18n import _


class SequencePropertiesDialog(ModernDialog):
    """Dialog for editing sequence properties"""
    
    def __init__(self, sequence_config, parent=None):
        super().__init__(parent, title=_("Sequence Properties"))
        
        # Use content_layout from ModernDialog
        self.layout = self.content_layout
        
        self.loop_count_label = QLabel(_("Loop Count:"))
        self.loop_count_input = QLineEdit(str(sequence_config.get('loop_count', 1)))
        self.layout.addWidget(self.loop_count_label)
        self.layout.addWidget(self.loop_count_input)
        
        self.extra_delay_label = QLabel(_("Extra Delay (s):"))
        self.extra_delay_input = QLineEdit(str(sequence_config.get('extra_delay', 1.0)))
        self.layout.addWidget(self.extra_delay_label)
        self.layout.addWidget(self.extra_delay_input)
        
        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        # Localize button texts
        ok_btn = self.button_box.button(QDialogButtonBox.Ok)
        cancel_btn = self.button_box.button(QDialogButtonBox.Cancel)
        if ok_btn:
            ok_btn.setText(_("OK"))
        if cancel_btn:
            cancel_btn.setText(_("Cancel"))
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        self.layout.addWidget(self.button_box)
        
    def get_properties(self):
        """Get the properties from the dialog"""
        return {
            'loop_count': int(self.loop_count_input.text()),
            'extra_delay': float(self.extra_delay_input.text())
        }