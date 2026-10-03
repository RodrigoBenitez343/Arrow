# dialogs/web_sequence_dialogs.py

from .base_dialog import ModernDialog
from PyQt5.QtWidgets import (
    QLabel,
    QLineEdit,
    QPushButton,
    QDialogButtonBox,
    QCheckBox,
    QGroupBox,
    QVBoxLayout,
    QTabWidget,
    QWidget,
)
from ..i18n import _


class WebSequencePropertiesDialog(ModernDialog):
    """Dialog for editing web sequence properties.

    Web sequences have no start URL: like a desktop sequence operates on the
    already-open app, a web session is just one or more actions executed on
    the browser session.  The "Record Web Session…" button closes the dialog
    with ``_record_requested`` set so the caller can start a background
    recording and attach the resulting session file to the node.
    """

    def __init__(self, web_sequence_config, parent=None):
        super().__init__(parent, title=_("Web Sequence Properties"), help_topic="web-sequence-dialog")

        # Use content_layout from ModernDialog
        _content_layout = self.content_layout
        self._record_requested = False

        _tabs = QTabWidget()
        _session_tab = QWidget()
        _sl = QVBoxLayout(_session_tab)
        _sl.setContentsMargins(0, 0, 0, 0)
        _sl.setSpacing(8)
        _extract_tab = QWidget()
        _el = QVBoxLayout(_extract_tab)
        _el.setContentsMargins(0, 0, 0, 0)
        _el.setSpacing(8)
        self.layout = _sl

        # Session file (read-only - assigned by recording or the file picker)
        session_file = web_sequence_config.get('session_file', '')
        self.session_file_label = QLabel(_("Session File:"))
        self.session_file_value = QLabel(session_file or "-")
        self.session_file_value.setWordWrap(True)
        self.layout.addWidget(self.session_file_label)
        self.layout.addWidget(self.session_file_value)

        self.headless_checkbox = QCheckBox(_("Headless (no visible browser window)"))
        self.headless_checkbox.setChecked(bool(web_sequence_config.get('headless', False)))
        self.layout.addWidget(self.headless_checkbox)

        self.speed_label = QLabel(_("Speed Multiplier:"))
        self.speed_input = QLineEdit(str(web_sequence_config.get('speed', 1.0)))
        self.layout.addWidget(self.speed_label)
        self.layout.addWidget(self.speed_input)

        self.native_checkbox = QCheckBox(_("Native Actions (Selenium; required for browser-chrome keys: back/forward, refresh, tabs)"))
        self.native_checkbox.setChecked(bool(web_sequence_config.get('native_actions', True)))
        self.layout.addWidget(self.native_checkbox)

        self.loop_count_label = QLabel(_("Loop Count:"))
        self.loop_count_input = QLineEdit(str(web_sequence_config.get('loop_count', 1)))
        self.layout.addWidget(self.loop_count_label)
        self.layout.addWidget(self.loop_count_input)

        self.extra_delay_label = QLabel(_("Extra Delay (s):"))
        self.extra_delay_input = QLineEdit(str(web_sequence_config.get('extra_delay', 1.0)))
        self.layout.addWidget(self.extra_delay_label)
        self.layout.addWidget(self.extra_delay_input)

        # What this web session DOES — the orchestrator reads it when the node
        # is used as a mini brain (label + structural hint are the fallback).
        self.description_label = QLabel(_("Description (what this web session does):"))
        self.description_input = QLineEdit(str(web_sequence_config.get('description', '') or ''))
        self.description_input.setPlaceholderText(
            _("e.g. Search the job board and open the first listing"))
        self.layout.addWidget(self.description_label)
        self.layout.addWidget(self.description_input)

        # Extract from page (ctx_out) toggles: only the checked items are
        # captured after replay and published on the data-only ctx_out port.
        self.layout = _el
        self.extract_label = QLabel(_("Extract from page (ctx_out):"))
        self.layout.addWidget(self.extract_label)
        self.extract_checks = {}
        for key, label in (
            ('page_text', _("Page text (visible text)")),
            ('page_html', _("Page HTML")),
            ('title', _("Page title")),
            ('url', _("Page URL")),
            # Replay fact, not a page item: "true" once every matching
            # repeating element has been clicked.  A conditional downstream
            # branches on it when the element set runs out.
            ('entity_exhausted', _("Repeating elements exhausted (no more elements to click)")),
        ):
            cb = QCheckBox(label)
            cb.setChecked(key in (web_sequence_config.get('extract_items') or []))
            self.layout.addWidget(cb)
            self.extract_checks[key] = cb

        _tabs.addTab(_session_tab, _("Session"))
        _tabs.addTab(_extract_tab, _("Extraction"))
        _content_layout.addWidget(_tabs, 1)
        self.layout = _content_layout

        self.record_button = QPushButton(_("Record Web Session…"))
        self.record_button.setToolTip(
            _("Open a browser, interact with the page, then press ESC (in the page "
              "or the terminal) or close the window to stop and save.")
        )
        self.record_button.clicked.connect(self._on_record_clicked)
        self.layout.addWidget(self.record_button)

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

    def _on_record_clicked(self):
        """Close the dialog and signal that a recording should be started."""
        self._record_requested = True
        self.accept()

    def get_properties(self):
        """Get the properties from the dialog"""
        return {
            'headless': bool(self.headless_checkbox.isChecked()),
            'speed': float(self.speed_input.text() or 1.0),
            'native_actions': bool(self.native_checkbox.isChecked()),
            'loop_count': int(self.loop_count_input.text() or 1),
            'extra_delay': float(self.extra_delay_input.text() or 1.0),
            'extract_items': [k for k, cb in self.extract_checks.items() if cb.isChecked()],
            'description': self.description_input.text().strip(),
        }
