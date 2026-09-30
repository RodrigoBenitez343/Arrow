"""TTS-related dialog classes for the LoOper application.

This module contains the TTSPropertiesDialog class for configuring
TTS (Text-to-Speech) node properties including text content, language,
voice model, and speech speed using Piper neural TTS.

``TTSFieldsWidget`` is the reusable voice-settings group (language,
voice model, speed, speaker) shared by the standalone dialog and the
Output node dialog — the standalone TTS node type is gone, but the
field-building lives on as a library.
"""

import os
from .base_dialog import ModernDialog
from PyQt5.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QTextEdit,
    QComboBox, QDialogButtonBox, QDoubleSpinBox,
    QSpinBox, QLineEdit, QPushButton, QFileDialog,
    QGroupBox, QFormLayout
)
from PyQt5.QtCore import Qt
from ..i18n import _


class TTSFieldsWidget(QGroupBox):
    """Voice settings group: language, voice model, speed, speaker id."""

    def __init__(self, parent=None):
        super().__init__(_("Voice Settings"), parent)
        voice_layout = QFormLayout()
        self.setLayout(voice_layout)

        # Language selection
        self.language_combo = QComboBox()
        self.language_combo.addItems([
            'en', 'es', 'fr', 'de', 'it', 'pt', 'ru', 'ja', 'ko', 'zh'
        ])
        voice_layout.addRow(_("Language:"), self.language_combo)

        # Voice model (optional explicit .onnx name/path)
        model_layout = QHBoxLayout()
        self.voice_model_edit = QLineEdit()
        self.voice_model_edit.setPlaceholderText(
            _("(auto) — or type model name / browse to .onnx file")
        )
        model_layout.addWidget(self.voice_model_edit)
        self.browse_btn = QPushButton(_("Browse…"))
        self.browse_btn.setFixedWidth(80)
        self.browse_btn.clicked.connect(self._browse_voice_model)
        model_layout.addWidget(self.browse_btn)
        voice_layout.addRow(_("Voice Model:"), model_layout)

        # Speed (length scale)
        self.speed_spin = QDoubleSpinBox()
        self.speed_spin.setRange(0.3, 3.0)
        self.speed_spin.setSingleStep(0.1)
        self.speed_spin.setDecimals(2)
        self.speed_spin.setValue(1.0)
        self.speed_spin.setToolTip(
            _("Speech speed: 1.0 = normal, <1.0 = faster, >1.0 = slower")
        )
        voice_layout.addRow(_("Speed:"), self.speed_spin)

        # Speaker ID (for multi-speaker models)
        self.speaker_spin = QSpinBox()
        self.speaker_spin.setRange(-1, 999)
        self.speaker_spin.setValue(-1)
        self.speaker_spin.setSpecialValueText(_("Default"))
        self.speaker_spin.setToolTip(
            _("Speaker index for multi-speaker voices. -1 = default speaker.")
        )
        voice_layout.addRow(_("Speaker ID:"), self.speaker_spin)

    def _browse_voice_model(self):
        """Open a file dialog to select a .onnx voice model."""
        try:
            from LoOper.player.tts_engine import get_voices_dir
            start_dir = get_voices_dir()
        except Exception:
            start_dir = os.path.expanduser("~")

        path, _ = QFileDialog.getOpenFileName(
            self,
            _("Select Piper Voice Model"),
            start_dir,
            _("ONNX Models (*.onnx);;All Files (*)"),
        )
        if path:
            self.voice_model_edit.setText(path)

    def load_config(self, config):
        """Load voice settings from a config dict."""
        language = (config or {}).get('language', 'en')
        index = self.language_combo.findText(language)
        if index >= 0:
            self.language_combo.setCurrentIndex(index)

        self.voice_model_edit.setText((config or {}).get('voice_model', ''))

        speed = (config or {}).get('speed', 1.0)
        if speed is not None:
            self.speed_spin.setValue(float(speed))

        speaker_id = (config or {}).get('speaker_id')
        if speaker_id is None:
            self.speaker_spin.setValue(-1)
        else:
            self.speaker_spin.setValue(int(speaker_id))

    def get_config(self):
        """Return the current voice settings as a dict."""
        speaker_id = self.speaker_spin.value()
        return {
            'language': self.language_combo.currentText(),
            'voice_model': self.voice_model_edit.text().strip(),
            'speed': self.speed_spin.value(),
            'speaker_id': None if speaker_id < 0 else speaker_id,
        }


class TTSPropertiesDialog(ModernDialog):
    """Dialog for configuring TTS node properties (Piper backend)"""

    def __init__(self, parent=None, config=None):
        super().__init__(parent, title=_("TTS Properties"), help_topic="tts-dialog")
        self.setModal(True)
        self.resize(480, 380)

        # Store the configuration
        self.config = config or {
            'text': _('Hello, this is a text to speech message.'),
            'language': 'en',
            'voice_model': '',
            'speed': 1.0,
            'speaker_id': None,
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

        # Voice settings group (reusable widget)
        self.voice_fields = TTSFieldsWidget(self)
        layout.addWidget(self.voice_fields)

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
        self.voice_fields.load_config(self.config)

    def get_config(self):
        """Get the current configuration from the dialog"""
        config = self.voice_fields.get_config()
        config['text'] = self.text_edit.toPlainText()
        return config
