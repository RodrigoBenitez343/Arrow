"""Output node properties dialog (unified chat/display medium).

The Output node is the unified display sink: a render mode (text/audio/
image/html/markdown) plus an orthogonal TTS group (speak the collected
content or a static ``tts_text`` fallback, optionally blocking the
workflow while speaking).  Built on ``ModernDialog`` so it shares the
dark theme, scrollable content and drag/resize behavior of the other
node dialogs.
"""

from PyQt5.QtWidgets import (
    QVBoxLayout, QLabel, QTextEdit,
    QComboBox, QDialogButtonBox, QLineEdit, QCheckBox,
    QGroupBox, QMessageBox,
)
import re

from .base_dialog import ModernDialog
from .tts_dialogs import TTSFieldsWidget
from ..i18n import _

VARIABLE_NAME_RE = re.compile(r'^[A-Za-z][A-Za-z0-9_]*$')

RENDER_MODES = [
    ("text", "Text"),
    ("audio", "Audio (TTS)"),
    ("image", "Image"),
    ("html", "HTML"),
    ("markdown", "Markdown"),
]


class OutputPropertiesDialog(ModernDialog):
    """Dialog for configuring Output node display + TTS properties."""

    def __init__(self, parent=None, config=None):
        super().__init__(parent, title=_("Output Node Properties"), help_topic="node-dialogs")
        self.setModal(True)
        self.resize(480, 620)
        self.config = config or {}
        self.setup_ui()
        self.load_config()

    def setup_ui(self):
        layout = self.content_layout

        # ── Label ──
        layout.addWidget(QLabel(_("Label (describes what this output collects):")))
        self.label_edit = QLineEdit()
        self.label_edit.setPlaceholderText(_("e.g. Chain result / summary"))
        layout.addWidget(self.label_edit)

        # ── Variable name ──
        layout.addWidget(QLabel(_("Variable name (required to expose this output on a chain import):")))
        self.variable_name_edit = QLineEdit()
        self.variable_name_edit.setPlaceholderText(_("e.g. summary (letters, digits, underscores)"))
        self.variable_name_edit.setToolTip(_(
            "When this chain is imported, every selected Output node becomes "
            "a data port on the chain import node named after this variable."
        ))
        layout.addWidget(self.variable_name_edit)

        # ── Render mode ──
        layout.addWidget(QLabel(_("Render mode (how this output is displayed):")))
        self.mode_combo = QComboBox()
        for value, name in RENDER_MODES:
            self.mode_combo.addItem(name, value)
        layout.addWidget(self.mode_combo)

        # ── Display options ──
        options_group = QGroupBox(_("Display Options"))
        options_layout = QVBoxLayout(options_group)
        options_layout.setSpacing(6)

        self.agent_cb = QCheckBox(
            _("Visible to Agent Mode\n"
              "When enabled, this node's content is returned to the\n"
              "agent overlay as the chain's execution result.")
        )
        options_layout.addWidget(self.agent_cb)

        self.overlay_cb = QCheckBox(
            _("Post to agent chat overlay\n"
              "When disabled, this output never posts in the agent chat\n"
              "(neither mid-run nor as the final reply) while the agent's\n"
              "activity memory still records it.")
        )
        options_layout.addWidget(self.overlay_cb)

        self.popup_cb = QCheckBox(
            _("Show popup on finish\n"
              "When enabled, a popup with the collected output is shown\n"
              "after the chain finishes in manual execution mode.")
        )
        options_layout.addWidget(self.popup_cb)

        self.rating_cb = QCheckBox(
            _("Show 5-star rating widget\n"
              "When enabled, users can rate and give feedback on the tool\n"
              "execution associated with this output node.")
        )
        options_layout.addWidget(self.rating_cb)

        layout.addWidget(options_group)

        # ── TTS group ──
        tts_group = QGroupBox(_("Text-to-Speech"))
        tts_layout = QVBoxLayout(tts_group)
        tts_layout.setSpacing(6)

        self.tts_enabled_cb = QCheckBox(
            _("Speak this output\n"
              "Speaks the static text below, or the collected content\n"
              "when no static text is set.")
        )
        tts_layout.addWidget(self.tts_enabled_cb)

        tts_layout.addWidget(QLabel(_("Text to speak (optional, overrides collected content):")))
        self.tts_text_edit = QTextEdit()
        self.tts_text_edit.setMaximumHeight(80)
        tts_layout.addWidget(self.tts_text_edit)

        self.tts_voice_fields = TTSFieldsWidget(tts_group)
        tts_layout.addWidget(self.tts_voice_fields)

        self.tts_wait_cb = QCheckBox(
            _("Wait for speech to finish\n"
              "When enabled, the workflow pauses while this output speaks.")
        )
        tts_layout.addWidget(self.tts_wait_cb)

        layout.addWidget(tts_group)

        # ── Buttons ──
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        ok_btn = button_box.button(QDialogButtonBox.Ok)
        cancel_btn = button_box.button(QDialogButtonBox.Cancel)
        if ok_btn:
            ok_btn.setText(_("OK"))
            ok_btn.setProperty("class", "primary")
        if cancel_btn:
            cancel_btn.setText(_("Cancel"))
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def load_config(self):
        cfg = self.config
        self.label_edit.setText(cfg.get("label", ""))
        self.variable_name_edit.setText(cfg.get("variable_name", "") or "")
        self.agent_cb.setChecked(bool(cfg.get("agent_visible", True)))
        self.overlay_cb.setChecked(bool(cfg.get("overlay_visible", True)))
        self.popup_cb.setChecked(bool(cfg.get("popup_on_finish", True)))
        self.rating_cb.setChecked(bool(cfg.get("show_rating", True)))

        mode = cfg.get("render_mode", "text") or "text"
        idx = self.mode_combo.findData(mode)
        self.mode_combo.setCurrentIndex(idx if idx >= 0 else 0)

        self.tts_enabled_cb.setChecked(bool(cfg.get("tts_enabled", False)))
        self.tts_text_edit.setPlainText(cfg.get("tts_text", "") or "")
        self.tts_wait_cb.setChecked(bool(cfg.get("tts_wait", False)))
        self.tts_voice_fields.load_config({
            "language": cfg.get("tts_language", "en"),
            "voice_model": cfg.get("tts_voice_model", ""),
            "speed": cfg.get("tts_speed", 1.0),
            "speaker_id": cfg.get("tts_speaker_id"),
        })

    def get_config(self):
        """Return the current dialog state as a node config dict."""
        config = self.tts_voice_fields.get_config()
        config.update({
            "label": self.label_edit.text(),
            "variable_name": self.variable_name_edit.text().strip(),
            "agent_visible": self.agent_cb.isChecked(),
            "overlay_visible": self.overlay_cb.isChecked(),
            "popup_on_finish": self.popup_cb.isChecked(),
            "show_rating": self.rating_cb.isChecked(),
            "render_mode": self.mode_combo.currentData() or "text",
            "tts_enabled": self.tts_enabled_cb.isChecked(),
            "tts_text": self.tts_text_edit.toPlainText(),
            "tts_wait": self.tts_wait_cb.isChecked(),
        })
        return config

    def accept(self):
        """Validate the variable name before closing."""
        variable_name = self.variable_name_edit.text().strip()
        if variable_name and not VARIABLE_NAME_RE.match(variable_name):
            QMessageBox.warning(
                self,
                _("Warning"),
                _("Variable name must start with a letter and contain only "
                  "letters, digits and underscores (e.g. summary).")
            )
            return
        super().accept()
