"""Form Filling node dialog.

Fills ONE PAGE of a form with a small language model.  The runtime owns the
field->value binding; the model only returns one value for one field.  This
dialog mirrors the LLM node's aesthetics (``ModernDialog`` theme), its
engine/model dropdowns, and its Documents section (the context probed via
ComoRAG per field).
"""

import json
import logging
import os
import threading

from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox, QDialogButtonBox, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QSpinBox, QDoubleSpinBox, QTextEdit,
    QPushButton, QVBoxLayout,
)

from .base_dialog import ModernDialog

from .toggle_switch import ModernToggle
from ..i18n import _
from AI.model_cache import get_real_cached_models

logger = logging.getLogger(__name__)

# Picker threads that did not stop in time are PARKED here, with their parent
# cleared: Qt aborts the whole app when a running QThread is destroyed
# ("QThread: Destroyed while thread is still running"), which is exactly what
# happened when the dialog was cancelled while the picker was still armed and
# left the terminal/chromedriver orphaned.  Parking lets it finish harmlessly.
_PARKED_THREADS = set()


def _park_thread(thread) -> None:
    try:
        thread.setParent(None)
    except Exception:
        pass
    _PARKED_THREADS.add(thread)
    try:
        thread.finished.connect(lambda: _PARKED_THREADS.discard(thread))
    except Exception:
        pass


class _WebPickThread(QThread):
    """Runs the blocking browser element picker off the GUI thread."""

    picked = pyqtSignal(object)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._cancel = threading.Event()

    def cancel(self) -> None:
        """Ask the picker to stop: it disarms and returns within one poll."""
        self._cancel.set()

    def run(self):
        try:
            try:
                from ...player.web.picker import pick_element
            except Exception:
                from player.web.picker import pick_element
        except Exception as exc:
            logger.error("Element picker unavailable: %s", exc)
            self.picked.emit(None)
            return
        try:
            self.picked.emit(pick_element(cancel=self._cancel))
        except Exception as exc:
            logger.error("Element picker failed: %s", exc)
            self.picked.emit(None)

# Dialog labels <-> runtime engine values
_ENGINE_LABELS = ["Ollama", "llama.cpp"]
_ENGINE_TO_RUNTIME = {"Ollama": "ollama", "llama.cpp": "llamacpp"}
_ENGINE_FROM_RUNTIME = {"ollama": "Ollama", "llamacpp": "llama.cpp"}


def _wrap_label(text):
    """A label that wraps, so a long sentence never widens the dialog."""
    label = QLabel(text)
    label.setWordWrap(True)
    return label


def _constrain_combo(combo):
    """Keep long model names from setting the dialog's width: the combo sizes
    to a fixed character budget instead of the widest item it holds."""
    combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(16)


class FormFillerDialog(ModernDialog):
    """Configuration dialog for the Form Filling node."""

    def __init__(self, parent, current_config=None):
        super().__init__(
            parent,
            title=_("Form Filling Configuration"),
            help_topic="form-filler-node",
        )
        self.current_config = current_config or {}
        self.setModal(True)
        self.resize(560, 720)
        self.setup_ui()

    # ------------------------------------------------------------------

    def setup_ui(self):
        layout = self.content_layout

        # ── Substrate (explicit per node) ──
        mode_group = QGroupBox(_("Substrate"))
        mode_layout = QGridLayout(mode_group)
        mode_layout.setContentsMargins(8, 12, 8, 8)
        mode_layout.setSpacing(6)
        mode_layout.addWidget(QLabel(_("Mode:")), 0, 0)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["web", "desktop"])
        self.mode_combo.setToolTip(
            _("web: fill the live DOM form in the chain's shared browser.\n"
              "desktop: fill an on-screen form via OCR + Handle grounding.")
        )
        mode_layout.addWidget(self.mode_combo, 0, 1)
        mode_help = QLabel(
            _("One page per node. Chain several nodes with Conditionals for "
              "multi-page forms.")
        )
        mode_help.setWordWrap(True)
        mode_help.setStyleSheet("color: #888; font-size: 10px; margin-top: 3px;")
        mode_layout.addWidget(mode_help, 1, 0, 1, 2)
        layout.addWidget(mode_group)

        # ── Model Configuration (mirrors the LLM node) ──
        model_group = QGroupBox(_("Model Configuration"))
        model_layout = QGridLayout(model_group)
        model_layout.setContentsMargins(8, 12, 8, 8)
        model_layout.setSpacing(6)

        model_layout.addWidget(QLabel(_("Engine:")), 0, 0)
        self.engine_combo = QComboBox()
        self.engine_combo.addItems(_ENGINE_LABELS)
        self.engine_combo.currentTextChanged.connect(self.on_engine_changed)
        model_layout.addWidget(self.engine_combo, 0, 1)

        model_layout.addWidget(QLabel(_("Ollama Model:")), 1, 0)
        self.ollama_model_combo = QComboBox()
        self.ollama_model_combo.setEditable(True)
        _constrain_combo(self.ollama_model_combo)
        try:
            self.ollama_model_combo.addItems(
                get_real_cached_models(timeout=5.0) or []
            )
        except Exception:
            pass
        model_layout.addWidget(self.ollama_model_combo, 1, 1)

        model_layout.addWidget(QLabel(_("GGUF Model:")), 2, 0)
        self.gguf_model_combo = QComboBox()
        self.gguf_model_combo.setEditable(True)
        _constrain_combo(self.gguf_model_combo)
        try:
            from .llm_dialogs import get_llamacpp_models
            gguf_models = get_llamacpp_models()
            self.gguf_model_combo.addItems(gguf_models)
            if not gguf_models:
                self.gguf_model_combo.setPlaceholderText(
                    _("No GGUF models found in AI/models/")
                )
        except Exception:
            pass
        model_layout.addWidget(self.gguf_model_combo, 2, 1)
        self.ollama_model_label = model_layout.itemAtPosition(1, 0).widget()
        self.gguf_model_label = model_layout.itemAtPosition(2, 0).widget()

        model_layout.addWidget(QLabel(_("Temperature:")), 3, 0)
        self.temperature_spin = QDoubleSpinBox()
        self.temperature_spin.setRange(0.0, 2.0)
        self.temperature_spin.setSingleStep(0.05)
        self.temperature_spin.setDecimals(2)
        model_layout.addWidget(self.temperature_spin, 3, 1)

        model_layout.addWidget(QLabel(_("Max tokens:")), 4, 0)
        self.max_tokens_spin = QSpinBox()
        self.max_tokens_spin.setRange(8, 4096)
        self.max_tokens_spin.setToolTip(
            _("Budget for the FINAL ANSWER per field; the chain of thought gets "
              "its own room on top and is never charged to this budget. Raise "
              "it when free-text fields (summaries, 'why this role') come back "
              "empty or truncated.")
        )
        model_layout.addWidget(self.max_tokens_spin, 4, 1)

        model_layout.addWidget(QLabel(_("Context size:")), 5, 0)
        self.context_size_spin = QSpinBox()
        self.context_size_spin.setRange(0, 131072)
        self.context_size_spin.setSingleStep(1024)
        self.context_size_spin.setToolTip(
            _("Context window for the llama.cpp engine. 0 = automatic "
              "(RAM-aware; on a low-RAM machine it can cap very small and then "
              "reject every prompt with 'exceed_context_size_error'). Set e.g. "
              "4096 to override.")
        )
        model_layout.addWidget(self.context_size_spin, 5, 1)
        layout.addWidget(model_group)

        # ── Task & field selection ──
        task_group = QGroupBox(_("Task && Fields"))
        task_layout = QVBoxLayout(task_group)
        task_layout.setContentsMargins(8, 12, 8, 8)
        task_layout.setSpacing(6)
        task_layout.addWidget(QLabel(_("Instruction (what to fill from):")))
        self.instruction_edit = QTextEdit()
        self.instruction_edit.setMaximumHeight(80)
        self.instruction_edit.setPlaceholderText(
            _("e.g. fill the job application using my resume and the wired-in context")
        )
        task_layout.addWidget(self.instruction_edit)

        task_layout.addWidget(_wrap_label(_("Only these fields (comma-separated, blank = all):")))
        self.fields_include_edit = QLineEdit()
        self.fields_include_edit.setPlaceholderText(_("e.g. Email, Full name"))
        task_layout.addWidget(self.fields_include_edit)

        task_layout.addWidget(_wrap_label(_("Skip these fields (comma-separated):")))
        self.fields_skip_edit = QLineEdit()
        self.fields_skip_edit.setPlaceholderText(_("e.g. Password, CAPTCHA"))
        task_layout.addWidget(self.fields_skip_edit)
        layout.addWidget(task_group)

        # ── Retrieval (ComoRAG, per field) ──
        retr_group = QGroupBox(_("Retrieval (ComoRAG, per field)"))
        retr_layout = QGridLayout(retr_group)
        retr_layout.setContentsMargins(8, 12, 8, 8)
        retr_layout.setSpacing(6)
        retr_layout.addWidget(QLabel(_("Probe top-k chunks:")), 0, 0)
        self.probe_top_k_spin = QSpinBox()
        self.probe_top_k_spin.setRange(1, 20)
        retr_layout.addWidget(self.probe_top_k_spin, 0, 1)
        retr_layout.addWidget(QLabel(_("Probe char budget:")), 1, 0)
        self.probe_char_budget_spin = QSpinBox()
        self.probe_char_budget_spin.setRange(200, 20000)
        self.probe_char_budget_spin.setSingleStep(100)
        retr_layout.addWidget(self.probe_char_budget_spin, 1, 1)
        retr_layout.addWidget(QLabel(_("Probe context chars:")), 2, 0)
        self.probe_context_chars_spin = QSpinBox()
        self.probe_context_chars_spin.setRange(1000, 12000)
        self.probe_context_chars_spin.setSingleStep(500)
        retr_layout.addWidget(self.probe_context_chars_spin, 2, 1)
        retr_layout.addWidget(QLabel(_("Retrieval cycles:")), 3, 0)
        self.probe_cycles_spin = QSpinBox()
        self.probe_cycles_spin.setRange(1, 12)
        self.probe_cycles_spin.setToolTip(
            _("MAX ComoRAG cycles per field; the field's probe stops as soon "
              "as the judge's answer is grounded or a cycle adds nothing "
              "new. 1 = single-pass top-k.")
        )
        retr_layout.addWidget(self.probe_cycles_spin, 3, 1)
        self.consolidate_checkbox = ModernToggle(
            text=_("Use ComoRAG consolidation (multi-cycle probing)")
        )
        self.consolidate_checkbox.setToolTip(
            _("On (default): each field's probe runs the iterative ComoRAG "
              "engine over several cycles for stronger grounding - slower and "
              "it spends more model calls.\n"
              "Off: one single-pass embedding retrieval per field - lighter, "
              "for a bigger model that needs less scaffolding.")
        )
        retr_layout.addWidget(self.consolidate_checkbox, 4, 0, 1, 2)
        retr_help = QLabel(
            _("Each field's label is the probe; only the most relevant slices "
              "of the wired-in context reach the model (keeps the window small).")
        )
        retr_help.setWordWrap(True)
        retr_help.setStyleSheet("color: #888; font-size: 10px; margin-top: 3px;")
        retr_layout.addWidget(retr_help, 5, 0, 1, 2)
        layout.addWidget(retr_group)

        # ── Page scope (web): confine field detection to a picked container ──
        self._web_scope = ""
        scope_group = QGroupBox(_("Page scope (web, optional)"))
        scope_layout = QVBoxLayout(scope_group)
        scope_layout.setContentsMargins(8, 12, 8, 8)
        scope_layout.setSpacing(6)
        scope_layout.addWidget(_wrap_label(
            _("Limit field detection to one container "
              "(a form, a modal dialog, or an iframe):")
        ))
        self.scope_label = QLabel(_("(whole page)"))
        self.scope_label.setWordWrap(True)
        self.scope_label.setStyleSheet("color: #aaa; font-size: 11px;")
        scope_layout.addWidget(self.scope_label)
        scope_btns = QHBoxLayout()
        pick_scope_btn = QPushButton(_("Pick container"))
        pick_scope_btn.clicked.connect(self.pick_scope)
        clear_scope_btn = QPushButton(_("Clear"))
        clear_scope_btn.clicked.connect(self.clear_scope)
        scope_btns.addWidget(pick_scope_btn)
        scope_btns.addWidget(clear_scope_btn)
        scope_layout.addLayout(scope_btns)
        scope_help = QLabel(
            _("Click 'Pick container', then click the form/modal in the browser "
              "(ESC cancels). Fields outside it - nav bars, the page behind a "
              "modal - are ignored.")
        )
        scope_help.setWordWrap(True)
        scope_help.setStyleSheet("color: #888; font-size: 10px; margin-top: 3px;")
        scope_layout.addWidget(scope_help)
        layout.addWidget(scope_group)

        # ── Behaviour ──
        beh_group = QGroupBox(_("Behaviour"))
        beh_layout = QGridLayout(beh_group)
        beh_layout.setContentsMargins(8, 12, 8, 8)
        beh_layout.setSpacing(6)
        beh_layout.addWidget(QLabel(_("Max fields:")), 0, 0)
        self.max_fields_spin = QSpinBox()
        self.max_fields_spin.setRange(1, 500)
        beh_layout.addWidget(self.max_fields_spin, 0, 1)
        self.verify_checkbox = ModernToggle(
            text=_("Verify each field after writing (re-ask once on mismatch)")
        )
        beh_layout.addWidget(self.verify_checkbox, 1, 0, 1, 2)
        self.answer_no_checkbox = ModernToggle(
            text=_("Answer 'No' to a yes/no question when nothing grounds a 'Yes'")
        )
        beh_layout.addWidget(self.answer_no_checkbox, 2, 0, 1, 2)
        self.ask_user_checkbox = ModernToggle(
            text=_("Ask me when the context has nothing for a field "
                   "(fills it and remembers the answer)")
        )
        self.ask_user_checkbox.setToolTip(
            _("When nothing grounds a field, ask the question here and WAIT for "
              "the answer (the fill pauses). The answer fills the field and is "
              "written into the wired-in Context node, so a later run retrieves "
              "it instead of asking again.")
        )
        beh_layout.addWidget(self.ask_user_checkbox, 4, 0, 1, 2)
        self.answer_na_checkbox = ModernToggle(
            text=_("Write 'N/A' for a field that cannot be answered "
                   "(last resort: a photo in a text box, off-topic options, "
                   "nothing in the source)")
        )
        self.answer_na_checkbox.setToolTip(
            _("A LAST RESORT, never a shortcut. Only when a field genuinely "
              "cannot be answered: the control cannot hold what the question "
              "asks for (a text box asking for a photo), every allowed option "
              "is off-topic, or the source holds nothing about it and no value "
              "can be derived. A list-backed field is only given its OWN N/A "
              "option; a field with none is left alone.")
        )
        beh_layout.addWidget(self.answer_na_checkbox, 3, 0, 1, 2)
        self.repair_checkbox = ModernToggle(
            text=_("Repair values the page rejects (re-check validity after filling)")
        )
        beh_layout.addWidget(self.repair_checkbox, 6, 0, 1, 2)
        beh_layout.addWidget(QLabel(_("Repair attempts:")), 7, 0)
        self.repair_attempts_spin = QSpinBox()
        self.repair_attempts_spin.setRange(1, 5)
        self.repair_attempts_spin.setToolTip(
            _("How many times a rejected field is re-probed and re-answered "
              "with the page's rejection message as feedback.")
        )
        beh_layout.addWidget(self.repair_attempts_spin, 7, 1)
        layout.addWidget(beh_group)

        layout.addStretch()

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

        self.load_current_config()

    # ------------------------------------------------------------------

    def on_engine_changed(self, text):
        use_llamacpp = text == "llama.cpp"
        self.ollama_model_combo.setVisible(not use_llamacpp)
        self.ollama_model_label.setVisible(not use_llamacpp)
        self.gguf_model_combo.setVisible(use_llamacpp)
        self.gguf_model_label.setVisible(use_llamacpp)

    # ── Page scope picker ──────────────────────────────────────────────

    def pick_scope(self):
        """Arm the browser element picker; store the picked container.

        The dialog is MINIMIZED (never hidden) while picking so the browser is
        reachable and the pick can be cancelled with ESC.
        """
        try:
            self.setModal(False)
            self.showMinimized()
        except Exception:
            pass
        self._cancel_pick_thread()          # a previous pick must not linger
        self._web_pick_thread = _WebPickThread(self)
        self._web_pick_thread.picked.connect(self._on_scope_picked)
        self._web_pick_thread.start()

    def _cancel_pick_thread(self):
        """Stop and join the picker thread so the dialog can be destroyed.

        Qt ABORTS the process when a running QThread is destroyed - that is the
        "QThread: Destroyed while thread is still running" crash when the dialog
        was cancelled with the picker still armed.  The thread is asked to stop
        and joined; one that is still inside a slow browser call is parked
        (parent cleared) so it can finish without taking the app down.
        """
        thread = getattr(self, "_web_pick_thread", None)
        self._web_pick_thread = None
        if thread is None:
            return
        try:
            thread.cancel()
            if thread.isRunning() and not thread.wait(4000):
                _park_thread(thread)
        except Exception:
            _park_thread(thread)

    def reject(self):
        self._cancel_pick_thread()
        super().reject()

    def closeEvent(self, event):
        self._cancel_pick_thread()
        super().closeEvent(event)

    def _on_scope_picked(self, result):
        try:
            self.setModal(True)
            self.showNormal()
            self.raise_()
            self.activateWindow()
        except Exception:
            pass
        if not result or (isinstance(result, dict) and result.get("cancelled")):
            return
        self._web_scope = json.dumps(result)
        self._update_scope_label()

    def clear_scope(self):
        self._web_scope = ""
        self._update_scope_label()

    def _update_scope_label(self):
        try:
            if not self._web_scope:
                self.scope_label.setText(_("(whole page)"))
                return
            data = self._web_scope
            if isinstance(data, str):
                data = json.loads(data)
            loc = (data or {}).get("locator") or {}
            if loc.get("id"):
                desc = "#" + str(loc["id"])
            elif loc.get("css"):
                desc = str(loc["css"])
            elif loc.get("text"):
                desc = f"{loc.get('tag') or ''} \"{str(loc['text'])[:40]}\""
            else:
                desc = loc.get("xpath") or _("picked element")
            self.scope_label.setText(_("Scoped to: ") + str(desc))
        except Exception:
            self.scope_label.setText(_("Scoped to: picked element"))

    # ------------------------------------------------------------------

    def get_config(self):
        engine = _ENGINE_TO_RUNTIME.get(self.engine_combo.currentText(), "llamacpp")
        if engine == "ollama":
            model = self.ollama_model_combo.currentText().strip()
        else:
            model = self.gguf_model_combo.currentText().strip()
        return {
            'mode': self.mode_combo.currentText(),
            'instruction': self.instruction_edit.toPlainText().strip(),
            'fields_include': self.fields_include_edit.text().strip(),
            'fields_skip': self.fields_skip_edit.text().strip(),
            'probe_top_k': self.probe_top_k_spin.value(),
            'probe_char_budget': self.probe_char_budget_spin.value(),
            'probe_context_chars': self.probe_context_chars_spin.value(),
            'probe_cycles': self.probe_cycles_spin.value(),
            'consolidate': self.consolidate_checkbox.isChecked(),
            'verify': self.verify_checkbox.isChecked(),
            'answer_no': self.answer_no_checkbox.isChecked(),
            'answer_na': self.answer_na_checkbox.isChecked(),
            'ask_user': self.ask_user_checkbox.isChecked(),
            'repair': self.repair_checkbox.isChecked(),
            'repair_attempts': self.repair_attempts_spin.value(),
            'max_fields': self.max_fields_spin.value(),
            'engine': engine,
            'model': model,
            'temperature': self.temperature_spin.value(),
            'max_tokens': self.max_tokens_spin.value(),
            'context_size': self.context_size_spin.value(),
            'web_scope': self._web_scope,
        }

    def load_current_config(self):
        cfg = self.current_config

        mode = str(cfg.get('mode') or 'web')
        self.mode_combo.setCurrentText(mode if mode in ('web', 'desktop') else 'web')

        engine = str(cfg.get('engine') or 'llamacpp').lower()
        self.engine_combo.setCurrentText(_ENGINE_FROM_RUNTIME.get(engine, 'llama.cpp'))
        model = str(cfg.get('model') or '')
        if engine == 'ollama':
            self.ollama_model_combo.setCurrentText(model)
        else:
            self.gguf_model_combo.setCurrentText(model)
        self.on_engine_changed(self.engine_combo.currentText())

        try:
            self.temperature_spin.setValue(float(cfg.get('temperature', 0.1)))
        except Exception:
            self.temperature_spin.setValue(0.1)
        try:
            self.max_tokens_spin.setValue(int(cfg.get('max_tokens', 1024)))
        except Exception:
            self.max_tokens_spin.setValue(1024)

        self.instruction_edit.setPlainText(cfg.get('instruction', '') or '')
        self.fields_include_edit.setText(cfg.get('fields_include', '') or '')
        self.fields_skip_edit.setText(cfg.get('fields_skip', '') or '')

        def _int(key, default):
            try:
                return int(cfg.get(key, default))
            except Exception:
                return default
        self.probe_top_k_spin.setValue(_int('probe_top_k', 3))
        self.probe_char_budget_spin.setValue(_int('probe_char_budget', 1500))
        self.probe_context_chars_spin.setValue(_int('probe_context_chars', 6000))
        self.probe_cycles_spin.setValue(_int('probe_cycles', 3))
        self.context_size_spin.setValue(_int('context_size', 0))
        consolidate = cfg.get('consolidate', True)
        self.consolidate_checkbox.setChecked(
            consolidate if isinstance(consolidate, bool) else str(consolidate).lower() in ('true', '1', 'yes', 'on')
        )
        self.max_fields_spin.setValue(_int('max_fields', 40))

        verify = cfg.get('verify', True)
        self.verify_checkbox.setChecked(
            verify if isinstance(verify, bool) else str(verify).lower() in ('true', '1', 'yes', 'on')
        )
        answer_no = cfg.get('answer_no', True)
        self.answer_no_checkbox.setChecked(
            answer_no if isinstance(answer_no, bool) else str(answer_no).lower() in ('true', '1', 'yes', 'on')
        )
        answer_na = cfg.get('answer_na', True)
        self.answer_na_checkbox.setChecked(
            answer_na if isinstance(answer_na, bool) else str(answer_na).lower() in ('true', '1', 'yes', 'on')
        )
        ask_user = cfg.get('ask_user', False)
        self.ask_user_checkbox.setChecked(
            ask_user if isinstance(ask_user, bool) else str(ask_user).lower() in ('true', '1', 'yes', 'on')
        )
        repair = cfg.get('repair', True)
        self.repair_checkbox.setChecked(
            repair if isinstance(repair, bool) else str(repair).lower() in ('true', '1', 'yes', 'on')
        )
        self.repair_attempts_spin.setValue(_int('repair_attempts', 2))

        self._web_scope = cfg.get('web_scope', '') or ''
        self._update_scope_label()
