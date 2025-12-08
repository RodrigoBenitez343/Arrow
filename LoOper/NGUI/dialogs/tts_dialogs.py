"""TTS-related dialog classes for the LoOper application.

This module contains the TTSPropertiesDialog class for configuring
TTS (Text-to-Speech) node properties including text content and language.
"""

from .base_dialog import ModernDialog
from PyQt5.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QTextEdit, 
    QComboBox, QDialogButtonBox
)
from PyQt5.QtCore import Qt
from ..i18n import _


class TTSPropertiesDialog(ModernDialog):
    """Dialog for configuring TTS node properties"""
    
    def __init__(self, parent=None, config=None):
        super().__init__(parent, title=_("TTS Properties"))
        self.setModal(True)
        self.resize(400, 250)
        
        # Store the configuration
        self.config = config or {
            'text': _('Hello, this is a text to speech message.'),
            'language': 'en'
        }
        
        self.setup_ui()
        self.load_config()
    
    def setup_ui(self):
        """Set up the user interface"""
        # Use content_layout from ModernDialog
        layout = self.content_layout
        
        # Text input
        text_label = QLabel(_("Text to Speak:"))
        layout.addWidget(text_label)
        
        self.text_edit = QTextEdit()
        self.text_edit.setMaximumHeight(120)
        layout.addWidget(self.text_edit)
        
        # Language selection
        lang_layout = QHBoxLayout()
        lang_layout.addWidget(QLabel(_("Language:")))
        self.language_combo = QComboBox()
        self.language_combo.addItems([
            'en', 'es', 'fr', 'de', 'it', 'pt', 'ru', 'ja', 'ko', 'zh'
        ])
        lang_layout.addWidget(self.language_combo)
        lang_layout.addStretch()  # Add stretch to push combo to the left
        
        layout.addLayout(lang_layout)
        
        # Dialog buttons
        button_box = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        # Localize button texts
        ok_btn = button_box.button(QDialogButtonBox.Ok)
        cancel_btn = button_box.button(QDialogButtonBox.Cancel)
        if ok_btn:
            ok_btn.setText(_("OK"))
        if cancel_btn:
            cancel_btn.setText(_("Cancel"))
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)
    
    def load_config(self):
        """Load configuration into the dialog"""
        self.text_edit.setPlainText(self.config.get('text', ''))
        
        language = self.config.get('language', 'en')
        index = self.language_combo.findText(language)
        if index >= 0:
            self.language_combo.setCurrentIndex(index)
    
    def get_config(self):
        """Get the current configuration from the dialog"""
        return {
            'text': self.text_edit.toPlainText(),
            'language': self.language_combo.currentText()
        }