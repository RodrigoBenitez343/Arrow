"""Input node properties dialog.

Configures the Input node's value behavior (label, default value, user
prompt, passthrough, agent-modifiable) plus an orthogonal TTS group that
speaks the user prompt when the agent asks for input in the overlay.
Built on ``ModernDialog`` — dark theme, scrollable content, drag/resize.
"""

from PyQt5.QtWidgets import (
    QVBoxLayout, QLabel, QTextEdit,
    QDialogButtonBox, QLineEdit, QCheckBox, QGroupBox, QComboBox,
)
from .base_dialog import ModernDialog
from .tts_dialogs import TTSFieldsWidget
from ..i18n import _


class InputPropertiesDialog(ModernDialog):
    """Dialog for configuring Input node value + TTS (speak prompt) properties."""

    def __init__(self, parent=None, config=None):
        super().__init__(parent, title=_("Input Node Properties"), help_topic="node-dialogs")
        self.setModal(True)
        self.resize(560, 900)
        self.config = config or {}
        self.setup_ui()
        self.load_config()

    def setup_ui(self):
        layout = self.content_layout

        # ── Value behavior ──
        layout.addWidget(QLabel(_("Instruction label (describes what to input):")))
        self.label_edit = QLineEdit()
        layout.addWidget(self.label_edit)

        layout.addWidget(QLabel(_("Default value (optional):")))
        self.default_edit = QLineEdit()
        layout.addWidget(self.default_edit)

        layout.addWidget(QLabel(_("User prompt (shown when agent asks for input):")))
        self.prompt_edit = QLineEdit()
        self.prompt_edit.setPlaceholderText(_("e.g. What should I search for on LinkedIn?"))
        layout.addWidget(self.prompt_edit)

        self.passthrough_cb = QCheckBox(_("Data passthrough (no screen typing)"))
        self.passthrough_cb.setWhatsThis(_(
            "ON: this node only forwards the value to the LLM / Code /\n"
            "Conditional nodes it is connected to \u2014 nothing is typed anywhere.\n"
            "OFF: the resolved value is written to the write target below."
        ))
        layout.addWidget(self.passthrough_cb)

        self.web_mode_cb = QCheckBox(_("Write target: browser page"))
        self.web_mode_cb.setWhatsThis(_(
            "ON: the value is written into the focused element of the chain's\n"
            "browser.  It never falls back to the desktop.\n"
            "OFF: the value is written on the desktop (the focused window).\n"
            "Not used in passthrough \u2014 passthrough writes nothing."
        ))
        layout.addWidget(self.web_mode_cb)
        # Passthrough types nothing, so web mode is meaningless there — keep the
        # two gates visually consistent.
        self.passthrough_cb.toggled.connect(
            lambda checked: self.web_mode_cb.setEnabled(not checked)
        )

        self.agent_cb = QCheckBox(_("Agent-modifiable (LLM fills this field)"))
        self.agent_cb.setWhatsThis(_(
            "ON: in agent mode the system chain's LLM derives this value from\n"
            "the user request and the reasoning context.\n"
            "OFF: the value comes from the settings above instead."
        ))
        layout.addWidget(self.agent_cb)

        # ── Decision routing group ──
        decision_group = QGroupBox(_("Decision routing (judge the value, branch true/false)"))
        decision_layout = QVBoxLayout(decision_group)
        decision_layout.setSpacing(6)

        self.decision_mode_cb = QCheckBox(_("Decision mode (the node itself decides)"))
        self.decision_mode_cb.setWhatsThis(_(
            "ON: the resolved value is judged against the criterion below by a\n"
            "small LLM turn (or the embedded Laya engine) and the node routes its\n"
            "true/false output ports.  No regex; unanswerable input takes the\n"
            "configured default branch (fail-closed by default).\n"
            "OFF: the node only supplies its value (today's behavior)."
        ))
        decision_layout.addWidget(self.decision_mode_cb)

        decision_layout.addWidget(QLabel(_("Criterion (natural language, e.g. 'The input asks for a product search'):")))
        self.decision_criterion_edit = QLineEdit()
        decision_layout.addWidget(self.decision_criterion_edit)

        decision_layout.addWidget(QLabel(_("Evaluator:")))
        self.decision_evaluator_combo = QComboBox()
        self.decision_evaluator_combo.addItems(["llm", "laya"])
        self.decision_evaluator_combo.setToolTip(_(
            "llm: one small true/false turn.\n"
            "laya: the embedded Laya engine (typed noul question, zero parsing);\n"
            "falls back to llm when the engine is unreachable."
        ))
        decision_layout.addWidget(self.decision_evaluator_combo)

        decision_layout.addWidget(QLabel(_("Decision model (dropdown; blank = chain LLM node, else app default):")))
        self.decision_model_combo = QComboBox()
        self.decision_model_combo.setEditable(True)
        self.decision_model_combo.setToolTip(_(
            "GGUF models found in AI/models/. Blank borrows the chain's LLM\n"
            "node model; with neither, the app default model is used."
        ))
        try:
            from .llm_dialogs import get_llamacpp_models
            _gguf = get_llamacpp_models() or []
            if _gguf:
                self.decision_model_combo.addItems([str(m) for m in _gguf])
        except Exception:
            pass
        decision_layout.addWidget(self.decision_model_combo)

        self.decision_default_cb = QCheckBox(_("Default branch when unanswerable: TRUE (fail-closed FALSE otherwise)"))
        decision_layout.addWidget(self.decision_default_cb)

        self.route_answer_cb = QCheckBox(_("Route yes/no answers (true/false ports)"))
        self.route_answer_cb.setWhatsThis(_(
            "ON: with question mode yes/no, the user's answer itself drives the\n"
            "true/false output ports (yes -> true).  No input+conditional pair\n"
            "needed."
        ))
        decision_layout.addWidget(self.route_answer_cb)
        layout.addWidget(decision_group)

        # ── Question mode group ──
        question_group = QGroupBox(_("User question (when a prompt is asked)"))
        question_layout = QVBoxLayout(question_group)
        question_layout.setSpacing(6)

        question_layout.addWidget(QLabel(_("Question mode:")))
        self.question_mode_combo = QComboBox()
        self.question_mode_combo.addItems(["text", "yes_no", "choice"])
        question_layout.addWidget(self.question_mode_combo)

        question_layout.addWidget(QLabel(_("Choices (JSON list for 'choice' mode, e.g. [{'label': 'Yes, apply', 'value': 'apply'}]):")))
        self.choices_edit = QTextEdit()
        self.choices_edit.setMaximumHeight(90)
        question_layout.addWidget(self.choices_edit)
        layout.addWidget(question_group)

        # ── Accepted media group ──
        media_group = QGroupBox(_("Accepted input kinds"))
        media_layout = QVBoxLayout(media_group)
        media_layout.setSpacing(6)

        self.accept_text_cb = QCheckBox(_("Text"))
        media_layout.addWidget(self.accept_text_cb)
        self.accept_images_cb = QCheckBox(_("Images"))
        media_layout.addWidget(self.accept_images_cb)
        self.accept_documents_cb = QCheckBox(_("Documents (pdf/docx/txt — text is extracted)"))
        media_layout.addWidget(self.accept_documents_cb)
        layout.addWidget(media_group)

        # ── TTS group ──
        tts_group = QGroupBox(_("Text-to-Speech"))
        tts_layout = QVBoxLayout(tts_group)
        tts_layout.setSpacing(6)

        self.tts_enabled_cb = QCheckBox(_("Speak this prompt"))
        self.tts_enabled_cb.setWhatsThis(_(
            "Speaks the static text below, or the user prompt above, when the\n"
            "agent asks for input in the overlay."
        ))
        tts_layout.addWidget(self.tts_enabled_cb)

        tts_layout.addWidget(QLabel(_("Text to speak (optional, overrides user prompt):")))
        self.tts_text_edit = QTextEdit()
        self.tts_text_edit.setMaximumHeight(70)
        tts_layout.addWidget(self.tts_text_edit)

        self.tts_voice_fields = TTSFieldsWidget(tts_group)
        tts_layout.addWidget(self.tts_voice_fields)

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
        self.default_edit.setText(cfg.get("default_value", ""))
        self.prompt_edit.setText(cfg.get("user_prompt", ""))
        self.passthrough_cb.setChecked(bool(cfg.get("passthrough", False)))
        self.web_mode_cb.setChecked(bool(cfg.get("web_mode", False)))
        self.web_mode_cb.setEnabled(not bool(cfg.get("passthrough", False)))
        self.agent_cb.setChecked(bool(cfg.get("agent_modifiable", False)))

        self.decision_mode_cb.setChecked(bool(cfg.get("decision_mode", False)))
        self.decision_criterion_edit.setText(cfg.get("decision_criterion", "") or "")
        evaluator = str(cfg.get("decision_evaluator") or "llm").strip().lower()
        self.decision_evaluator_combo.setCurrentText(evaluator if evaluator in ("llm", "laya") else "llm")
        self.decision_model_combo.setCurrentText(cfg.get("decision_model", "") or "")
        self.decision_default_cb.setChecked(bool(cfg.get("decision_default", False)))
        self.route_answer_cb.setChecked(bool(cfg.get("route_on_answer", False)))

        qmode = str(cfg.get("question_mode") or "text").strip().lower()
        self.question_mode_combo.setCurrentText(qmode if qmode in ("text", "yes_no", "choice") else "text")
        self.choices_edit.setPlainText(cfg.get("choices", "[]") or "[]")

        self.accept_text_cb.setChecked(bool(cfg.get("accept_text", True)))
        self.accept_images_cb.setChecked(bool(cfg.get("accept_images", False)))
        self.accept_documents_cb.setChecked(bool(cfg.get("accept_documents", False)))

        self.tts_enabled_cb.setChecked(bool(cfg.get("tts_enabled", False)))
        self.tts_text_edit.setPlainText(cfg.get("tts_text", "") or "")
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
            "default_value": self.default_edit.text(),
            "user_prompt": self.prompt_edit.text(),
            "passthrough": self.passthrough_cb.isChecked(),
            "web_mode": self.web_mode_cb.isChecked(),
            "agent_modifiable": self.agent_cb.isChecked(),
            "decision_mode": self.decision_mode_cb.isChecked(),
            "decision_criterion": self.decision_criterion_edit.text(),
            "decision_evaluator": self.decision_evaluator_combo.currentText(),
            "decision_model": self.decision_model_combo.currentText().strip(),
            "decision_default": self.decision_default_cb.isChecked(),
            "question_mode": self.question_mode_combo.currentText(),
            "choices": self.choices_edit.toPlainText(),
            "route_on_answer": self.route_answer_cb.isChecked(),
            "accept_text": self.accept_text_cb.isChecked(),
            "accept_images": self.accept_images_cb.isChecked(),
            "accept_documents": self.accept_documents_cb.isChecked(),
            "tts_enabled": self.tts_enabled_cb.isChecked(),
            "tts_text": self.tts_text_edit.toPlainText(),
        })
        return config
