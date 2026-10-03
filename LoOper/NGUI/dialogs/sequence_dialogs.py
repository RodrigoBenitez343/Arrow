# dialogs/sequence_dialogs.py

from .base_dialog import ModernDialog
from PyQt5.QtWidgets import (QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
                             QDialogButtonBox, QCheckBox, QGroupBox)
from ..i18n import _


class SequencePropertiesDialog(ModernDialog):
    """Dialog for editing sequence properties"""
    
    def __init__(self, sequence_config, parent=None):
        super().__init__(parent, title=_("Sequence Properties"), help_topic="sequence-dialog")
        
        # Use content_layout from ModernDialog
        _content_layout = self.content_layout
        _card = QGroupBox(_("Sequence Properties"))
        _card_layout = QVBoxLayout(_card)
        _card_layout.setContentsMargins(0, 0, 0, 0)
        _card_layout.setSpacing(8)
        self.layout = _card_layout

        self.loop_count_label = QLabel(_("Loop Count:"))
        self.loop_count_input = QLineEdit(str(sequence_config.get('loop_count', 1)))
        self.layout.addWidget(self.loop_count_label)
        self.layout.addWidget(self.loop_count_input)
        
        self.extra_delay_label = QLabel(_("Extra Delay (s):"))
        self.extra_delay_input = QLineEdit(str(sequence_config.get('extra_delay', 1.0)))
        self.layout.addWidget(self.extra_delay_label)
        self.layout.addWidget(self.extra_delay_input)

        # Desktop execution: click drift - each click lands a random distance
        # from the matched centre instead of the identical pixel every time.
        self.click_drift_label = QLabel(_("Click Drift (px, min-max):"))
        self.click_drift_label.setToolTip(
            _("Random offset applied to each click so it never lands on the exact "
              "same pixel. Set both to 0 to click the match centre precisely.")
        )
        self.layout.addWidget(self.click_drift_label)
        click_drift_layout = QHBoxLayout()
        self.click_drift_min_input = QLineEdit(str(sequence_config.get('click_drift_min', 5.0)))
        self.click_drift_max_input = QLineEdit(str(sequence_config.get('click_drift_max', 10.0)))
        click_drift_layout.addWidget(self.click_drift_min_input)
        click_drift_layout.addWidget(self.click_drift_max_input)
        self.layout.addLayout(click_drift_layout)

        # What this sequence DOES — the orchestrator reads it when the node is
        # used as a mini brain (label + structural hint are the fallback).
        self.description_label = QLabel(_("Description (what this sequence does):"))
        self.description_input = QLineEdit(str(sequence_config.get('description', '') or ''))
        self.description_input.setPlaceholderText(
            _("e.g. Open the LinkedIn jobs page and click the first result"))
        self.layout.addWidget(self.description_label)
        self.layout.addWidget(self.description_input)

        use_app_opened = sequence_config.get('use_app_opened', True)
        self.use_app_opened_checkbox = QCheckBox(_("Use recorded app context (open/focus app)"))
        self.use_app_opened_checkbox.setChecked(bool(use_app_opened))
        self.layout.addWidget(self.use_app_opened_checkbox)
        
        _content_layout.addWidget(_card)
        self.layout = _content_layout

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
        drift_min = self._drift_value(self.click_drift_min_input.text(), 5.0)
        drift_max = self._drift_value(self.click_drift_max_input.text(), 10.0)
        if drift_max < drift_min:
            drift_min, drift_max = drift_max, drift_min
        return {
            'loop_count': int(self.loop_count_input.text()),
            'extra_delay': float(self.extra_delay_input.text()),
            'use_app_opened': bool(self.use_app_opened_checkbox.isChecked()),
            'click_drift_min': drift_min,
            'click_drift_max': drift_max,
            'description': self.description_input.text().strip(),
        }

    @staticmethod
    def _drift_value(text, default):
        """Parse a drift field; unparseable input keeps the default, negatives clamp to 0."""
        try:
            return max(0.0, float(text))
        except (TypeError, ValueError):
            return default
