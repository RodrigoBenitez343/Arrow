# dialogs/sequence_dialogs.py

from PyQt5.QtWidgets import QDialog, QVBoxLayout, QLabel, QLineEdit, QDialogButtonBox


class SequencePropertiesDialog(QDialog):
    """Dialog for editing sequence properties"""
    
    def __init__(self, sequence_config, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sequence Properties")
        
        self.layout = QVBoxLayout(self)
        
        self.loop_count_label = QLabel("Loop Count:")
        self.loop_count_input = QLineEdit(str(sequence_config.get('loop_count', 1)))
        self.layout.addWidget(self.loop_count_label)
        self.layout.addWidget(self.loop_count_input)
        
        self.extra_delay_label = QLabel("Extra Delay (s):")
        self.extra_delay_input = QLineEdit(str(sequence_config.get('extra_delay', 1.0)))
        self.layout.addWidget(self.extra_delay_label)
        self.layout.addWidget(self.extra_delay_input)
        
        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        self.layout.addWidget(self.button_box)
        
    def get_properties(self):
        """Get the properties from the dialog"""
        return {
            'loop_count': int(self.loop_count_input.text()),
            'extra_delay': float(self.extra_delay_input.text())
        }