"""LLM-related dialog classes for the LoOper application.

This module contains the LLMPropertiesDialog class for configuring
LLM (Large Language Model) node properties including model selection,
prompt configuration, generation parameters, and vision capabilities.
"""

import json
import logging
import os
import sys
import threading
import uuid

from PyQt5.QtCore import QSize, Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..constants import TEXT_COLOR
from ..i18n import _
from .base_dialog import ModernDialog
from .toggle_switch import ModernToggle
from .utils import get_default_api_url

logger = logging.getLogger(__name__)

# Add the parent directory to sys.path to import AI modules
sys.path.append(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
from AI.consult import OllamaClient
from AI.model_cache import get_cached_models, get_model_cache, get_real_cached_models


def get_llamacpp_models():
    """Scan for GGUF models in the LoOper/AI/models directory"""
    try:
        from AI.config_loader import get_models_dir
        models_dir = get_models_dir()

        if not os.path.exists(models_dir):
            print(f"Warning: Models directory not found: {models_dir}")
            return []

        gguf_models = []
        # Walk through the models directory to find .gguf files
        for root, dirs, files in os.walk(models_dir):
            for file in files:
                if file.endswith(".gguf"):
                    # Get relative path from models directory
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, models_dir)
                    # Convert to forward slashes for consistency
                    rel_path = rel_path.replace("\\", "/")
                    gguf_models.append(rel_path)

        return sorted(gguf_models)
    except Exception as e:
        print(f"Warning: Failed to scan llama.cpp models: {e}")
        return []


class SkillGeneratorThread(QThread):
    """Thread for generating skill definitions via AI"""

    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, model, prompt, api_url=None):
        super().__init__()
        self.model = model
        self.prompt = prompt
        self.api_url = api_url

    def run(self):
        try:
            api_url = self.api_url or get_default_api_url()
            client = OllamaClient(base_url=api_url)

            system_prompt = (
                "You are an expert at defining AI skills. "
                "Given a description of what a skill should do, "
                "generate a structured skill definition. "
                "Return ONLY a JSON object with these fields: "
                "name (short skill name), description (one-line summary), "
                "trigger_keywords (comma-separated keywords), instructions (detailed instructions). "
                "No markdown, no explanations, just the JSON."
            )

            response = client.generate(
                model=self.model, prompt=self.prompt, system=system_prompt
            )

            result_text = response.get("response", "")
            # Try to extract JSON
            try:
                # Strip markdown if present
                if "```json" in result_text:
                    result_text = result_text.split("```json")[1].split("```")[0]
                elif "```" in result_text:
                    result_text = result_text.split("```")[1].split("```")[0]

                skill_data = json.loads(result_text.strip())
                self.finished.emit(skill_data)
            except json.JSONDecodeError as e:
                self.error.emit(f"Failed to parse skill JSON: {str(e)}")
        except Exception as e:
            self.error.emit(str(e))


class _WebPickThread(QThread):
    """Runs the blocking browser element picker off the GUI thread.

    Local copy of the conditional dialog's thread: importing it from
    ``conditional_dialogs`` would create an import cycle (that module already
    imports this one for ``get_llamacpp_models``).
    """

    picked = pyqtSignal(object)

    def run(self):
        try:
            try:
                from ...player.web.picker import pick_element
            except Exception:
                from player.web.picker import pick_element
        except Exception as exc:
            logger.error(f"Element picker unavailable: {exc}")
            self.picked.emit(None)
            return
        try:
            self.picked.emit(pick_element())
        except Exception as exc:
            logger.error(f"Element picker failed: {exc}")
            self.picked.emit(None)


class _ShrinkTabWidget(QTabWidget):
    """QTabWidget that reports the CURRENT page's size, not the tallest one.

    QTabWidget/QStackedWidget report the max sizeHint AND minimumSizeHint over
    every page, so a short tab inherits the tallest tab's height and shows a
    block of dead space.  Reporting only the current page keeps the dialog
    tight when switching tabs (mirrors conditional_dialogs._ShrinkStack).
    """

    def _bar(self):
        try:
            return self.tabBar().sizeHint()
        except Exception:
            return QSize(0, 0)

    def sizeHint(self):
        page = self.currentWidget()
        if page is None:
            return super().sizeHint()
        hint = page.sizeHint()
        bar = self._bar()
        return QSize(max(hint.width(), bar.width()), hint.height() + bar.height())

    def minimumSizeHint(self):
        page = self.currentWidget()
        if page is None:
            return super().minimumSizeHint()
        hint = page.minimumSizeHint()
        bar = self._bar()
        return QSize(hint.width(), hint.height() + bar.height())


class LLMPropertiesDialog(ModernDialog):
    """Dialog for configuring LLM node properties"""

    _refresh_complete_signal = pyqtSignal(list, str, object)

    def __init__(self, parent, current_config=None):
        super().__init__(
            parent, title=_("LLM Node Configuration"), help_topic="llm-dialog"
        )
        self.current_config = current_config or {}

        # Do NOT let the scroll area STRETCH its content: a stretched content
        # widget keeps the TALLEST page's height, so every short tab shows a
        # block of vertical dead space.  With resizing off, _apply_fit sizes the
        # content to the current tab and the scroll area only adds scrollbars
        # when the content is genuinely taller than the dialog.
        try:
            self.scroll_area.setWidgetResizable(False)
        except Exception:
            pass

        self.setModal(True)
        # No fixed size: the dialog is fitted to the current tab's content
        # (see _refit_to_current_tab) so short tabs leave no dead space.
        self.setMinimumSize(460, 260)

        # Connect async refresh signal
        self._refresh_complete_signal.connect(self._on_refresh_complete)

        # Style logic moved to ModernDialog, but we can keep specific overrides if needed
        # or rely on ModernDialog's theme.

        self.setup_ui()
        self.load_current_config()

    def get_available_models(self):
        # Return models from cache only; do not trigger API calls here
        # Use a short timeout so UI does not freeze when API is still starting
        return get_real_cached_models(timeout=2.0)

    def refresh_models(self):
        """Refresh Ollama model list asynchronously to avoid UI freeze."""
        api_url = self.api_url_entry.text() or get_default_api_url()
        current_model = self.model_combo.currentText()

        # Disable button to prevent double-clicks while loading
        refresh_btn = self.sender()
        if isinstance(refresh_btn, QPushButton):
            refresh_btn.setEnabled(False)
            refresh_btn.setText(_("Loading..."))

        def _do_refresh():
            cache = get_model_cache()
            cache.refresh_models(api_url)
            available_models = get_real_cached_models(timeout=5.0)
            # Schedule UI update on the main thread
            QThread.currentThread()  # no-op, we're in a worker thread
            self._refresh_complete_signal.emit(available_models, current_model, refresh_btn)

        threading.Thread(target=_do_refresh, daemon=True).start()

    def _on_refresh_complete(self, available_models, current_model, refresh_btn):
        """Called on main thread when model refresh finishes."""
        self.model_combo.clear()
        self.model_combo.addItems(available_models)
        if current_model in available_models:
            self.model_combo.setCurrentText(current_model)
        # Re-enable the button
        if isinstance(refresh_btn, QPushButton):
            refresh_btn.setEnabled(True)
            refresh_btn.setText(_("Refresh Models"))

    def on_engine_changed(self, engine):
        """Handle engine selection change"""
        use_llamacpp = engine == "llama.cpp"

        # Show/hide appropriate model dropdowns
        self.model_combo.setVisible(not use_llamacpp)
        self.model_label.setVisible(not use_llamacpp)

        self.llamacpp_model_combo.setVisible(use_llamacpp)
        self.llamacpp_model_label.setVisible(use_llamacpp)

        # Refresh GGUF model list when switching to llama.cpp
        # so newly downloaded models are visible immediately
        if use_llamacpp:
            current_llamacpp = self.llamacpp_model_combo.currentText()
            gguf_models = get_llamacpp_models()
            self.llamacpp_model_combo.clear()
            if gguf_models:
                self.llamacpp_model_combo.addItems(gguf_models)
                if current_llamacpp in gguf_models:
                    self.llamacpp_model_combo.setCurrentText(current_llamacpp)
            else:
                self.llamacpp_model_combo.setPlaceholderText(
                    "No GGUF models found in AI/models/"
                )

        # Also show/hide API URL (not needed for llama.cpp)
        # We'll keep it visible but could hide it if desired

        # Keep the RAG embedding model aligned with the selected engine:
        # llama.cpp → local embeddinggemma GGUF; Ollama → nomic-embed-text.
        # Only rewrite empty/auto/legacy values — never a custom model.
        _rag_entry = getattr(self, "rag_embedding_model_entry", None)
        if _rag_entry is not None:
            _cur_rag = (_rag_entry.text() or "").strip().lower()
            if _cur_rag in (
                "", "auto", "default",
                "nomic-embed-text", "nomic-embed-text:v1.5",
            ) or (not use_llamacpp and _cur_rag == "embeddinggemma-300m-q8_0.gguf"):
                _rag_entry.setText(
                    "embeddinggemma-300M-Q8_0.gguf"
                    if use_llamacpp
                    else "nomic-embed-text"
                )

    def setup_ui(self):
        # Use content_layout from ModernDialog instead of creating a new layout on self
        layout = self.content_layout
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        self.tab_widget = _ShrinkTabWidget()
        # Stylesheet removed as ModernDialog handles it
        self.tab_widget.currentChanged.connect(self._refit_to_current_tab)
        self.create_model_tab()
        self.create_prompt_tab()
        self.create_output_tab()
        self.create_knowledge_tab()
        self.create_tools_skills_tab()
        layout.addWidget(self.tab_widget)
        self.setup_signal_connections()
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
        self._refit_to_current_tab()

    def _refit_to_current_tab(self):
        """Re-fit the dialog to the tab that just became current.

        A plain tab widget forces the tallest page's height on every tab; the
        _ShrinkTabWidget reports only the current page.  This drops the parent
        layout's CACHED size — otherwise the scroll area keeps the content
        widget as tall as the TALLEST page and every short tab shows a block of
        vertical dead space — then fits on the next loop tick so the resize
        reads the post-invalidate sizes.
        """
        try:
            if self.tab_widget.currentWidget() is None:
                return
            self.tab_widget.updateGeometry()
            self.content_layout.invalidate()
            self.content_widget.updateGeometry()
            self.scroll_area.updateGeometry()
        except Exception:
            pass
        QTimer.singleShot(0, self._apply_fit)

    def _apply_fit(self):
        """Size the content AND the dialog to the current tab's content."""
        try:
            if self.tab_widget.currentWidget() is None:
                return
            hint = self.content_layout.sizeHint()
            # Pin the content to the current tab so the tab widget is never
            # stretched past its content (with widgetResizable off the scroll
            # area keeps this size and only adds scrollbars when needed).
            self.content_widget.setFixedSize(hint.width(), hint.height())
            # Chrome = everything around the scroll viewport (title bar, grip, borders).
            chrome = self.size() - self.scroll_area.viewport().size()
            width = max(460, hint.width() + max(chrome.width(), 34))
            height = max(260, hint.height() + max(chrome.height(), 60))
            scr = QApplication.primaryScreen()
            if scr is not None:
                avail = scr.availableGeometry()
                width = min(width, avail.width() - 60)
                height = min(height, avail.height() - 60)
            self.resize(width, height)
        except Exception:
            pass

    def showEvent(self, event):
        super().showEvent(event)
        self._refit_to_current_tab()

    def create_model_tab(self):
        """Model tab: which model to run and how it runs."""
        model_tab = QWidget()
        layout = QVBoxLayout(model_tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # Node Mode: a SWITCH.  OFF = vanilla LLM node.  ON = orchestrator: the
        # node gains the orchestrator ports (trace/route), runs the goal loop
        # over the chains wired to its 'tools' port, and picks the next worker
        # with Laya (fixed — no choice).
        mode_group = QGroupBox(_("Node Mode"))
        mode_layout = QGridLayout(mode_group)
        mode_layout.setContentsMargins(8, 12, 8, 8)
        mode_layout.setSpacing(6)
        self.orchestrator_mode_check = ModernToggle(
            text=_("Orchestrator mode (this node becomes an orchestrator)")
        )
        mode_layout.addWidget(self.orchestrator_mode_check, 0, 0, 1, 2)

        mode_help = QLabel(
            _("ON: goal loop over the chains wired to this node's 'tools' port, "
              "next worker picked by Laya.  OFF: a plain LLM node.")
        )
        mode_help.setWordWrap(True)
        mode_help.setStyleSheet("color: #888; font-size: 10px; margin-top: 3px;")
        mode_layout.addWidget(mode_help, 1, 0, 1, 2)
        layout.addWidget(mode_group)

        # Orchestrator settings — shown only while the switch is ON.
        self.orch_group = QGroupBox(_("Orchestrator"))
        orch_layout = QGridLayout(self.orch_group)
        orch_layout.setContentsMargins(8, 12, 8, 8)
        orch_layout.setSpacing(6)

        orch_layout.addWidget(QLabel(_("Max steps:")), 0, 0)
        self.orch_max_steps_spin = QSpinBox()
        self.orch_max_steps_spin.setRange(1, 50)
        self.orch_max_steps_spin.setValue(15)
        self.orch_max_steps_spin.setToolTip(_("Per-activation step cap"))
        orch_layout.addWidget(self.orch_max_steps_spin, 0, 1)

        # Fixed goal for the loop.  A goal wired to the node's 'prompt' port
        # OVERRIDES it (the port wins; this is the fallback).
        orch_layout.addWidget(QLabel(_("Goal:")), 1, 0)
        self.orch_goal_edit = QTextEdit()
        self.orch_goal_edit.setPlaceholderText(
            _("Fixed goal for the loop (used when nothing is wired to the prompt port)"))
        self.orch_goal_edit.setMaximumHeight(60)
        orch_layout.addWidget(self.orch_goal_edit, 1, 1)

        self.orch_synthesize_check = ModernToggle(
            text=_("Synthesize a final answer from the trace")
        )
        orch_layout.addWidget(self.orch_synthesize_check, 2, 0, 1, 2)

        orch_layout.addWidget(QLabel(_("Synthesis system:")), 3, 0)
        self.orch_synthesis_system_edit = QLineEdit()
        self.orch_synthesis_system_edit.setPlaceholderText(_("optional system message"))
        orch_layout.addWidget(self.orch_synthesis_system_edit, 3, 1)

        self.orch_use_goal_ledger_check = ModernToggle(
            text=_("Use long-term goal ledger")
        )
        orch_layout.addWidget(self.orch_use_goal_ledger_check, 4, 0, 1, 2)

        orch_note = QLabel(_("Worker picker: Laya (fixed)."))
        orch_note.setStyleSheet("color: #888; font-size: 10px;")
        orch_layout.addWidget(orch_note, 5, 0, 1, 2)
        self.orch_group.setVisible(False)
        layout.addWidget(self.orch_group)

        # Swap the dialog contents with the switch.
        try:
            self.orchestrator_mode_check.toggled.connect(self._apply_node_mode_visibility)
        except Exception:
            pass

        # Model Configuration Group
        model_group = QGroupBox(_("Model Configuration"))
        model_layout = QGridLayout(model_group)
        model_layout.setContentsMargins(8, 12, 8, 8)
        model_layout.setSpacing(6)

        # Engine selection (Ollama vs llama.cpp)
        model_layout.addWidget(QLabel(_("Engine:")), 0, 0)
        self.engine_combo = QComboBox()
        self.engine_combo.addItems(["Ollama", "llama.cpp"])
        self.engine_combo.currentTextChanged.connect(self.on_engine_changed)
        model_layout.addWidget(self.engine_combo, 0, 1)

        # Ollama model selection
        model_layout.addWidget(QLabel(_("Ollama Model:")), 1, 0)
        self.model_label = model_layout.itemAtPosition(1, 0).widget()
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        # Populate model list from cache only (no API calls on dialog open)
        self.model_combo.addItems(self.get_available_models())
        model_layout.addWidget(self.model_combo, 1, 1)

        # llama.cpp model selection (initially hidden)
        model_layout.addWidget(QLabel(_("GGUF Model:")), 2, 0)
        self.llamacpp_model_combo = QComboBox()
        self.llamacpp_model_combo.setEditable(True)
        gguf_models = get_llamacpp_models()
        self.llamacpp_model_combo.addItems(gguf_models)
        if not gguf_models:
            self.llamacpp_model_combo.setPlaceholderText(
                "No GGUF models found in AI/models/"
            )
        model_layout.addWidget(self.llamacpp_model_combo, 2, 1)
        self.llamacpp_model_combo.setVisible(False)
        self.llamacpp_model_label = model_layout.itemAtPosition(2, 0).widget()
        self.llamacpp_model_label.setVisible(False)

        # API URL
        model_layout.addWidget(QLabel(_("API URL:")), 3, 0)
        api_url_layout = QHBoxLayout()
        self.api_url_entry = QLineEdit()
        self.api_url_entry.setPlaceholderText(get_default_api_url())
        api_url_layout.addWidget(self.api_url_entry)
        refresh_btn = QPushButton(_("Refresh Models"))
        refresh_btn.clicked.connect(self.refresh_models)
        api_url_layout.addWidget(refresh_btn)
        model_layout.addLayout(api_url_layout, 3, 1)

        layout.addWidget(model_group)

        # Generation Group
        gen_group = QGroupBox(_("Generation"))
        gen_layout = QGridLayout(gen_group)
        gen_layout.setContentsMargins(8, 12, 8, 8)
        gen_layout.setSpacing(6)
        gen_layout.addWidget(QLabel(_("Temperature:")), 0, 0)
        self.temperature_spin = QDoubleSpinBox()
        self.temperature_spin.setRange(0.0, 2.0)
        self.temperature_spin.setSingleStep(0.1)
        self.temperature_spin.setValue(0.7)
        gen_layout.addWidget(self.temperature_spin, 0, 1)
        layout.addWidget(gen_group)

        # Quick Templates Group
        template_group = QGroupBox(_("Quick Templates"))
        template_layout = QVBoxLayout(template_group)
        template_layout.setContentsMargins(8, 12, 8, 8)
        template_layout.setSpacing(6)
        template_layout.addWidget(QLabel(_("Apply Template:")))
        self.template_combo = QComboBox()
        self.template_combo.addItems(
            [
                "Custom (no template)",
                "Information Extraction (OCR + LLM)",
                "Form Filling (Vision + LLM)",
                "Custom Instructions",
                "Direct Prompt",
                "Data Processing Chain",
                "Text Analysis",
                "Content Summarization",
                "Tool Selection (LLM Tools)",
                "Python Code Generation",
            ]
        )
        self.template_combo.currentTextChanged.connect(self.apply_template)
        template_layout.addWidget(self.template_combo)
        self.template_description = QLabel()
        self.template_description.setWordWrap(True)
        self.template_description.setStyleSheet(
            "color: #888; font-size: 10px; margin-top: 3px;"
        )
        template_layout.addWidget(self.template_description)
        layout.addWidget(template_group)

        # Llama.cpp Runtime Group
        llamacpp_group = QGroupBox(_("Llama.cpp Runtime"))
        llamacpp_layout = QGridLayout(llamacpp_group)
        llamacpp_layout.setContentsMargins(8, 12, 8, 8)
        llamacpp_layout.setSpacing(6)
        llamacpp_layout.addWidget(QLabel(_("Context Size:")), 0, 0)
        self.context_size_spin = QSpinBox()
        # 0 = model's native training context (the server resolves --ctx-size 0
        # to the model's own value); anything above 0 is a manual cap.
        self.context_size_spin.setRange(0, 131072)
        self.context_size_spin.setSingleStep(512)
        self.context_size_spin.setValue(0)
        self.context_size_spin.setToolTip(
            _(
                "llama.cpp context window in tokens (--ctx-size). 0 = the "
                "model's native training context (recommended). A positive "
                "value caps the window and is bounded by the model's trained "
                "context and your RAM/VRAM. Responses are not capped — the "
                "model generates until it finishes or this context fills."
            )
        )
        llamacpp_layout.addWidget(self.context_size_spin, 0, 1)

        llamacpp_layout.addWidget(QLabel(_("GPU Layers:")), 1, 0)
        self.gpu_layers_spin = QSpinBox()
        # 0 = CPU-first (default).  Positive values offload that many layers
        # to a GPU backend (Vulkan/CUDA) when the server binary supports one.
        self.gpu_layers_spin.setRange(0, 200)
        self.gpu_layers_spin.setValue(0)
        self.gpu_layers_spin.setToolTip(
            _(
                "Layers offloaded to the GPU (--gpu-layers). 0 = CPU-only "
                "(recommended default). Set >0 to use a Vulkan/CUDA backend "
                "if available (e.g. integrated graphics) for acceleration."
            )
        )
        llamacpp_layout.addWidget(self.gpu_layers_spin, 1, 1)

        llamacpp_help_label = QLabel(
            _(
                "There is no output token cap: llama.cpp generates up to the "
                "maximum the model + hardware can handle (context-bounded). "
                "If the context exceeds the model or your RAM/VRAM, the "
                "llama.cpp server fails to start — check automation.log."
            )
        )
        llamacpp_help_label.setWordWrap(True)
        llamacpp_help_label.setStyleSheet(
            "color: #888; font-size: 10px; margin-top: 3px;"
        )
        llamacpp_layout.addWidget(llamacpp_help_label, 2, 0, 1, 2)
        layout.addWidget(llamacpp_group)

        # Async Processing Group
        async_group = QGroupBox(_("Async Processing"))
        async_layout = QGridLayout(async_group)
        async_layout.setContentsMargins(8, 12, 8, 8)
        async_layout.setSpacing(6)
        async_layout.addWidget(QLabel(_("Enable Async:")), 0, 0)
        self.use_async_checkbox = ModernToggle(
            text=_("Process requests asynchronously")
        )
        self.use_async_checkbox.setChecked(True)
        self.use_async_checkbox.setToolTip(
            _("Enable asynchronous processing for better performance")
        )
        async_layout.addWidget(self.use_async_checkbox, 0, 1)
        async_help_label = QLabel(
            _(
                "Async processing allows the workflow to continue while waiting for LLM responses."
            )
        )
        async_help_label.setWordWrap(True)
        async_help_label.setStyleSheet("color: #888; font-size: 10px; margin-top: 3px;")
        async_layout.addWidget(async_help_label, 1, 0, 1, 2)
        layout.addWidget(async_group)

        layout.addStretch()
        self.tab_widget.addTab(model_tab, _("Model"))

    def create_prompt_tab(self):
        """Prompt tab: what the model sees (prompt + input readers + vision)."""
        prompt_tab = QWidget()
        layout = QVBoxLayout(prompt_tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # Prompt Configuration Group
        prompt_group = QGroupBox(_("Prompt Configuration"))
        prompt_layout = QVBoxLayout(prompt_group)
        prompt_layout.setContentsMargins(8, 12, 8, 8)
        prompt_layout.setSpacing(6)

        # System message
        prompt_layout.addWidget(QLabel(_("System Message (optional):")))
        self.system_message_edit = QTextEdit()
        self.system_message_edit.setMaximumHeight(100)
        self.system_message_edit.setPlaceholderText(
            _("Enter system instructions for the AI model...")
        )
        prompt_layout.addWidget(self.system_message_edit)

        # User prompt
        prompt_layout.addWidget(QLabel(_("Prompt:")))
        self.prompt_edit = QTextEdit()
        self.prompt_edit.setMaximumHeight(120)
        self.prompt_edit.setPlaceholderText(_("Enter your prompt here..."))
        prompt_layout.addWidget(self.prompt_edit)

        layout.addWidget(prompt_group)

        # Web Mode: route input/vision/write on the chain's shared browser
        # instead of the desktop screen (mirrors the conditional's web mode).
        self.web_mode_checkbox = ModernToggle(
            text=_("Web Mode (run on the chain's shared browser)")
        )
        self.web_mode_checkbox.setChecked(False)
        try:
            self.web_mode_checkbox.toggled.connect(self._on_web_mode_toggled)
        except Exception:
            try:
                self.web_mode_checkbox.stateChanged.connect(self._on_web_mode_toggled)
            except Exception:
                pass
        layout.addWidget(self.web_mode_checkbox)

        # External input Group — only NON-connection readers live here. Text
        # from other nodes arrives through the 'prompt' and 'context' ports (a
        # connection IS the instruction), exactly like a web sequence's output
        # port feeding a code/conditional/LLM node.
        input_source_group = QGroupBox(_("External input (not from connections)"))
        input_source_layout = QVBoxLayout(input_source_group)
        input_source_layout.setContentsMargins(8, 12, 8, 8)
        input_source_layout.setSpacing(6)

        self.input_source_group = QButtonGroup(self)
        self.input_none_radio = QRadioButton(
            _("None (use connected inputs / direct prompt)"))
        self.input_none_radio.setChecked(True)
        self.input_source_group.addButton(self.input_none_radio, 0)

        self.input_ocr_radio = QRadioButton(
            _("OCR — desktop: screen region; web: browser element"))
        self.input_source_group.addButton(self.input_ocr_radio, 1)

        self.input_page_text_radio = QRadioButton(
            _("Read browser page text (web mode only)"))
        self.input_page_text_radio.setToolTip(
            _("Reads the visible text of the chain's browser page "
              "(only meaningful in Web Mode)")
        )
        self.input_source_group.addButton(self.input_page_text_radio, 2)

        self.input_clipboard_radio = QRadioButton(_("Clipboard content"))
        self.input_source_group.addButton(self.input_clipboard_radio, 3)

        input_source_layout.addWidget(self.input_none_radio)
        input_source_layout.addWidget(self.input_ocr_radio)
        input_source_layout.addWidget(self.input_page_text_radio)
        input_source_layout.addWidget(self.input_clipboard_radio)

        # OCR Configuration (visible when OCR is selected)
        self.ocr_config_widget = QWidget()
        ocr_config_layout = QGridLayout(self.ocr_config_widget)
        ocr_config_layout.setContentsMargins(0, 8, 0, 0)
        ocr_config_layout.setSpacing(6)

        # OCR confidence threshold
        ocr_config_layout.addWidget(QLabel(_("OCR Confidence:")), 0, 0)
        self.ocr_confidence_spin = QDoubleSpinBox()
        self.ocr_confidence_spin.setRange(0.1, 1.0)
        self.ocr_confidence_spin.setSingleStep(0.1)
        self.ocr_confidence_spin.setValue(0.7)
        self.ocr_confidence_spin.setToolTip(
            _("Minimum confidence for OCR text extraction")
        )
        ocr_config_layout.addWidget(self.ocr_confidence_spin, 0, 1)

        # OCR text preprocessing
        ocr_config_layout.addWidget(QLabel(_("Text Preprocessing:")), 1, 0)
        self.ocr_preprocessing_combo = QComboBox()
        self.ocr_preprocessing_combo.addItems(
            [
                _("None"),
                _("Remove extra spaces"),
                _("Clean formatting"),
                _("Extract structured data"),
            ]
        )
        ocr_config_layout.addWidget(self.ocr_preprocessing_combo, 1, 1)

        # OCR region: limit OCR to a picked area.  Desktop uses the on-screen
        # element overlay; web mode uses the browser picker (a picked element's
        # region).  Empty = full screen / whole viewport.
        self._ocr_region = ""
        self._web_ocr_locator = ""
        self.ocr_region_label = QLabel(_("No region picked (full screen)"))
        self.ocr_region_label.setWordWrap(True)
        self.ocr_region_pick_btn = QPushButton(_("Pick region"))
        self.ocr_region_pick_btn.clicked.connect(self._pick_ocr_region)
        self.ocr_region_clear_btn = QPushButton(_("Clear"))
        self.ocr_region_clear_btn.clicked.connect(self._clear_ocr_region)
        ocr_region_row = QHBoxLayout()
        ocr_region_row.setSpacing(6)
        ocr_region_row.addWidget(self.ocr_region_label, 1)
        ocr_region_row.addWidget(self.ocr_region_pick_btn)
        ocr_region_row.addWidget(self.ocr_region_clear_btn)
        ocr_config_layout.addLayout(ocr_region_row, 2, 0, 1, 2)

        input_source_layout.addWidget(self.ocr_config_widget)

        layout.addWidget(input_source_group)

        # Vision (screenshot input) Group
        vision_group = QGroupBox(_("Vision (screenshot input)"))
        vision_layout = QGridLayout(vision_group)
        vision_layout.setContentsMargins(8, 12, 8, 8)
        vision_layout.setSpacing(6)
        vision_layout.addWidget(QLabel(_("Use Vision:")), 0, 0)
        self.use_vision_checkbox = ModernToggle(
            text=_("Enable vision capabilities for this model")
        )
        self.use_vision_checkbox.setChecked(False)
        vision_layout.addWidget(self.use_vision_checkbox, 0, 1)
        vision_layout.addWidget(QLabel(_("Screenshot:")), 1, 0)
        self.screenshot_enabled_checkbox = ModernToggle(
            text=_("Take screenshot automatically for vision models")
        )
        self.screenshot_enabled_checkbox.setChecked(True)
        vision_layout.addWidget(self.screenshot_enabled_checkbox, 1, 1)
        vision_help = QLabel(
            _(
                "Screenshots come from the desktop screen, or from the chain's "
                "browser when Web Mode is on."
            )
        )
        vision_help.setWordWrap(True)
        vision_help.setStyleSheet("color: #888; font-size: 10px; margin-top: 3px;")
        vision_layout.addWidget(vision_help, 2, 0, 1, 2)
        layout.addWidget(vision_group)

        layout.addStretch()
        self.tab_widget.addTab(prompt_tab, _("Prompt"))

    def create_output_tab(self):
        """Output tab: what the node publishes and how it writes text."""
        output_tab = QWidget()
        layout = QVBoxLayout(output_tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # Output variable Group
        var_group = QGroupBox(_("Output"))
        var_layout = QGridLayout(var_group)
        var_layout.setContentsMargins(8, 12, 8, 8)
        var_layout.setSpacing(6)
        var_layout.addWidget(QLabel(_("Output Variable:")), 0, 0)
        self.output_variable_entry = QLineEdit()
        self.output_variable_entry.setPlaceholderText("llm_output")
        var_layout.addWidget(self.output_variable_entry, 0, 1)
        var_help = QLabel(
            _(
                "Downstream nodes read this value from the node's output / "
                "context port — no wiring is needed here."
            )
        )
        var_help.setWordWrap(True)
        var_help.setStyleSheet("color: #888; font-size: 10px; margin-top: 3px;")
        var_layout.addWidget(var_help, 1, 0, 1, 2)
        layout.addWidget(var_group)

        # Write Generated Text Group
        write_group = QGroupBox(_("Write Generated Text"))
        write_layout = QGridLayout(write_group)
        write_layout.setContentsMargins(8, 12, 8, 8)
        write_layout.setSpacing(6)

        write_layout.addWidget(QLabel(_("Write Text:")), 0, 0)
        self.write_text_checkbox = ModernToggle(
            text=_("Automatically write generated text")
        )
        self.write_text_checkbox.setChecked(True)
        write_layout.addWidget(self.write_text_checkbox, 0, 1)

        write_layout.addWidget(QLabel(_("Typing Batch Size:")), 1, 0)
        self.typing_batch_size_spin = QSpinBox()
        self.typing_batch_size_spin.setRange(1, 100)
        self.typing_batch_size_spin.setValue(20)
        self.typing_batch_size_spin.setToolTip(
            _("Number of characters to type at once (higher = faster typing)")
        )
        write_layout.addWidget(self.typing_batch_size_spin, 1, 1)

        write_layout.addWidget(QLabel(_("Typing Batch Delay:")), 2, 0)
        self.typing_batch_delay_spin = QDoubleSpinBox()
        self.typing_batch_delay_spin.setRange(0.01, 1.0)
        self.typing_batch_delay_spin.setSingleStep(0.01)
        self.typing_batch_delay_spin.setValue(0.05)
        self.typing_batch_delay_spin.setToolTip(
            _("Delay between typing batches in seconds (lower = faster typing)")
        )
        write_layout.addWidget(self.typing_batch_delay_spin, 2, 1)

        self.write_text_checkbox.toggled.connect(self.typing_batch_size_spin.setEnabled)
        self.write_text_checkbox.toggled.connect(
            self.typing_batch_delay_spin.setEnabled
        )
        write_help = QLabel(
            _(
                "Desktop: types into the focused window. Web Mode: types into "
                "the browser's focused input field."
            )
        )
        write_help.setWordWrap(True)
        write_help.setStyleSheet("color: #888; font-size: 10px; margin-top: 3px;")
        write_layout.addWidget(write_help, 3, 0, 1, 2)
        layout.addWidget(write_group)

        layout.addStretch()
        self.tab_widget.addTab(output_tab, _("Output"))

    def setup_signal_connections(self):
        """Wire cross-widget signals once every tab exists."""
        # External-input change: show/hide the OCR settings block.
        try:
            self.input_source_group.buttonClicked.connect(
                self.update_input_source_visibility
            )
        except Exception:
            pass
        # Template selection is wired in create_model_tab (apply_template).

    # --------------------------------------------------------------- web mode
    def _on_web_mode_toggled(self, enabled):
        """Web mode swaps the OCR screenshot source between screen and browser."""
        try:
            enabled = bool(enabled)
        except Exception:
            enabled = False
        try:
            self.input_page_text_radio.setEnabled(enabled)
            if not enabled and self.input_page_text_radio.isChecked():
                self.input_none_radio.setChecked(True)
        except Exception:
            pass
        try:
            self.ocr_region_pick_btn.setText(
                _("Pick element") if enabled else _("Pick region")
            )
        except Exception:
            pass
        self._update_ocr_region_label()

    @staticmethod
    def _locator_summary(locator):
        if not isinstance(locator, dict):
            return ''
        parts = [str(locator.get('tag') or 'element')]
        if locator.get('id'):
            parts.append('#' + str(locator['id']))
        text = str(locator.get('text') or '').strip()
        if text:
            parts.append('"' + text[:40] + '"')
        return ' '.join(parts)

    @staticmethod
    def _safe_locator(value):
        if isinstance(value, dict):
            return value
        try:
            return json.loads(value)
        except Exception:
            return None

    def _update_ocr_region_label(self):
        try:
            web = bool(self.web_mode_checkbox.isChecked())
        except Exception:
            web = False
        try:
            if web:
                locator = self._safe_locator(self._web_ocr_locator)
                if locator:
                    self.ocr_region_label.setText(
                        _("OCR element: {summary}").format(
                            summary=self._locator_summary(locator))
                    )
                else:
                    self.ocr_region_label.setText(
                        _("No element picked (whole browser viewport)"))
            elif self._ocr_region:
                x, y, w, h = self._ocr_region
                self.ocr_region_label.setText(
                    _("OCR region: x={x}, y={y}, w={w}, h={h}").format(
                        x=x, y=y, w=w, h=h)
                )
            else:
                self.ocr_region_label.setText(_("No region picked (full screen)"))
        except Exception:
            pass

    def _clear_ocr_region(self):
        self._ocr_region = ""
        self._web_ocr_locator = ""
        self._update_ocr_region_label()

    def _pick_ocr_region(self):
        """Pick an OCR area with the overlay (desktop) or the browser (web)."""
        try:
            web = bool(self.web_mode_checkbox.isChecked())
        except Exception:
            web = False
        if web:
            self._pick_web_ocr_element()
        else:
            self._pick_desktop_ocr_region()

    def _pick_desktop_ocr_region(self):
        """Hide this dialog and let the user drag a box on screen."""
        try:
            from .trigger_dialogs import (
                ElementPickOverlay, _get_main_window_from_widget)
        except Exception as exc:
            logger.error(f"Element picker unavailable: {exc}")
            return
        self._pick_main_window = _get_main_window_from_widget(self.parent())
        try:
            self._pick_original_geo = self.geometry()
        except Exception:
            self._pick_original_geo = None
        try:
            self.move(-10000, -10000)
            QApplication.processEvents()
        except Exception:
            pass
        try:
            if self._pick_main_window is not None:
                self._pick_main_window.showMinimized()
                QApplication.processEvents()
        except Exception:
            pass
        QTimer.singleShot(
            450, lambda: self._run_desktop_pick(ElementPickOverlay))

    def _run_desktop_pick(self, overlay_cls):
        try:
            overlay = overlay_cls(parent=None)
        except Exception as exc:
            logger.error(f"Element picker unavailable: {exc}")
            self._restore_after_pick()
            return
        overlay.picked.connect(self._on_desktop_region_picked)
        try:
            overlay.exec_()
        except Exception as exc:
            logger.error(f"Element picker failed: {exc}")
        finally:
            self._restore_after_pick()

    def _on_desktop_region_picked(self, rect):
        if rect:
            l, t, r, b = rect
            self._ocr_region = [int(l), int(t),
                                max(1, int(r) - int(l)), max(1, int(b) - int(t))]
        self._update_ocr_region_label()

    def _restore_after_pick(self):
        try:
            if getattr(self, '_pick_original_geo', None) is not None:
                self.setGeometry(self._pick_original_geo)
            self.show()
            self.raise_()
            self.activateWindow()
        except Exception:
            pass
        try:
            if getattr(self, '_pick_main_window', None) is not None:
                self._pick_main_window.showNormal()
                self._pick_main_window.raise_()
                self._pick_main_window.activateWindow()
        except Exception:
            pass

    def _pick_web_ocr_element(self):
        """Arm the browser picker off the GUI thread and store the locator."""
        try:
            self.setModal(False)
            self.showMinimized()
        except Exception:
            pass
        self._web_pick_thread = _WebPickThread(self)
        self._web_pick_thread.picked.connect(self._on_web_locator_picked)
        self._web_pick_thread.start()

    def _on_web_locator_picked(self, result):
        try:
            self.setModal(True)
            self.showNormal()
            self.raise_()
            self.activateWindow()
        except Exception:
            pass
        if not result or (isinstance(result, dict) and result.get('cancelled')):
            return
        locator = dict(result.get('locator') or {})
        # Where the element lives: a frame-relative css/xpath must be re-applied
        # to the SAME document (see the web conditional's picker callback).
        locator['_frame_path'] = result.get('frame_path') or []
        locator['_cross_origin'] = bool(result.get('cross_origin_frame'))
        if result.get('_window_ordinal') is not None:
            locator['_window_ordinal'] = int(result['_window_ordinal'])
        self._web_ocr_locator = json.dumps(locator)
        self._update_ocr_region_label()

    def load_current_config(self):
        """Load current configuration into the dialog"""
        if self.current_config:
            # Check if using llama.cpp
            use_llamacpp = self.current_config.get("use_llamacpp", False)
            if isinstance(use_llamacpp, str):
                use_llamacpp = use_llamacpp.lower() in ("true", "1", "yes", "on")

            # Set engine selection
            if use_llamacpp:
                self.engine_combo.setCurrentText("llama.cpp")
            else:
                self.engine_combo.setCurrentText("Ollama")

            # Basic tab settings — empty by default: llama.cpp nodes run
            # their GGUF, Ollama nodes pick a model explicitly.
            self.model_combo.setCurrentText(
                self.current_config.get("model") or ""
            )

            # Load llama.cpp model if applicable
            llamacpp_model = self.current_config.get("llamacpp_model_path", "")
            if llamacpp_model:
                self.llamacpp_model_combo.setCurrentText(llamacpp_model)

            self.api_url_entry.setText(
                self.current_config.get("api_url", get_default_api_url())
            )
            self.temperature_spin.setValue(self.current_config.get("temperature", 0.7))
            # Restore the llama.cpp context size (0 = model's native context).
            self.context_size_spin.setValue(
                int(self.current_config.get("llamacpp_context_size", 0))
            )
            # Restore the GPU layers (0 = CPU-first).
            self.gpu_layers_spin.setValue(
                int(self.current_config.get("llamacpp_gpu_layers", 0))
            )
            self.output_variable_entry.setText(
                self.current_config.get("output_variable", "llm_output")
            )
            self.orchestrator_mode_check.setChecked(
                bool(self.current_config.get("orchestrator_mode", False))
            )
            try:
                self.orch_max_steps_spin.setValue(
                    int(self.current_config.get("orch_max_steps", 15) or 15))
            except Exception:
                pass
            self.orch_synthesize_check.setChecked(
                bool(self.current_config.get("orch_synthesize", True)))
            self.orch_goal_edit.setPlainText(
                self.current_config.get("orch_goal") or "")
            self.orch_synthesis_system_edit.setText(
                self.current_config.get("orch_synthesis_system") or "")
            self.orch_use_goal_ledger_check.setChecked(
                bool(self.current_config.get("orch_use_goal_ledger", False)))
            try:
                self._apply_node_mode_visibility()
            except Exception:
                pass

            # Prompt tab settings
            self.system_message_edit.setPlainText(
                self.current_config.get("system_message", "")
            )
            self.prompt_edit.setPlainText(
                self.current_config.get("prompt", "Enter your prompt here...")
            )

            # External input (connection-driven values are NOT listed here — a
            # connection into the input/context port already feeds the node).
            # Legacy "previous"/"input"/"input_node" values fall back to none.
            input_source = str(
                self.current_config.get("input_source", "none") or "none"
            ).lower()
            _radio_by_source = {
                "none": self.input_none_radio,
                "ocr": self.input_ocr_radio,
                "page_text": self.input_page_text_radio,
                "clipboard": self.input_clipboard_radio,
            }
            radio = _radio_by_source.get(input_source)
            if radio is not None:
                radio.setChecked(True)
            self.ocr_confidence_spin.setValue(
                self.current_config.get("ocr_confidence", 0.7)
            )

            # Web mode + OCR region (page-text radio is enabled by the toggle).
            try:
                self.web_mode_checkbox.setChecked(
                    bool(self.current_config.get("web_mode", False)))
            except Exception:
                pass
            _region = self.current_config.get("ocr_region", "")
            if isinstance(_region, str):
                try:
                    _region = json.loads(_region)
                except Exception:
                    _region = None
            if isinstance(_region, (list, tuple)) and len(_region) == 4:
                try:
                    self._ocr_region = [int(v) for v in _region]
                except (TypeError, ValueError):
                    self._ocr_region = ""
            self._web_ocr_locator = self.current_config.get("web_ocr_locator", "") or ""
            self._update_ocr_region_label()

            ocr_preprocessing = self.current_config.get("ocr_preprocessing", "None")
            index = self.ocr_preprocessing_combo.findText(ocr_preprocessing)
            if index >= 0:
                self.ocr_preprocessing_combo.setCurrentIndex(index)

            # Advanced tab settings
            self.write_text_checkbox.setChecked(
                self.current_config.get("write_text", True)
            )
            self.typing_batch_size_spin.setValue(
                self.current_config.get("typing_batch_size", 20)
            )
            self.typing_batch_delay_spin.setValue(
                self.current_config.get("typing_batch_delay", 0.05)
            )
            self.use_async_checkbox.setChecked(
                self.current_config.get("use_async", True)
            )

            # Enable/disable typing speed controls based on write_text setting
            write_text_enabled = self.current_config.get("write_text", True)
            self.typing_batch_size_spin.setEnabled(write_text_enabled)
            self.typing_batch_delay_spin.setEnabled(write_text_enabled)

            # Features tab settings
            self.use_vision_checkbox.setChecked(
                self.current_config.get("use_vision", False)
            )
            # vision_model is no longer used — images go directly to the main model
            self.screenshot_enabled_checkbox.setChecked(
                self.current_config.get("screenshot_enabled", True)
            )

            self.use_direct_rag_checkbox.setChecked(
                self.current_config.get("use_direct_rag", False)
            )
            # Engine-aware default: llama.cpp nodes embed with the local
            # embeddinggemma GGUF; Ollama nodes with nomic-embed-text.  Only
            # rewrite empty/auto/legacy values — never a custom model.
            _rag_model = str(
                self.current_config.get("rag_embedding_model", "") or ""
            ).strip()
            if _rag_model.lower() in ("", "auto", "default") or (
                use_llamacpp
                and _rag_model.lower() in ("nomic-embed-text", "nomic-embed-text:v1.5")
            ):
                _rag_model = (
                    "embeddinggemma-300M-Q8_0.gguf"
                    if use_llamacpp
                    else "nomic-embed-text"
                )
            self.rag_embedding_model_entry.setText(_rag_model)
            self.rag_chunk_size_spin.setValue(
                self.current_config.get("rag_chunk_size", 500)
            )
            self.rag_overlap_spin.setValue(self.current_config.get("rag_overlap", 100))
            self.rag_top_k_spin.setValue(self.current_config.get("rag_top_k", 3))
            self.rag_include_raw_input_checkbox.setChecked(
                self.current_config.get("rag_include_raw_input", False)
            )
            self.rag_max_chars_spin.setValue(
                self.current_config.get("rag_max_chars", 1500)
            )
            docs = self.current_config.get("rag_documents", []) or []
            try:
                import json as _json

                if isinstance(docs, str):
                    docs = _json.loads(docs)
            except Exception:
                docs = []
            for p in docs:
                self.rag_documents_list.addItem(p)

            # Context Consolidation config (opt-in — disabled by default)
            self.use_consolidation_checkbox.setChecked(
                self.current_config.get("use_context_consolidation", False)
            )
            self.consolidation_chunk_size_spin.setValue(
                self.current_config.get("consolidation_chunk_size", 1000)
            )
            self.consolidation_overlap_spin.setValue(
                self.current_config.get("consolidation_overlap", 200)
            )
            self.consolidation_top_k_spin.setValue(
                self.current_config.get("consolidation_top_k", 5)
            )
            self.consolidation_max_tokens_spin.setValue(
                self.current_config.get("consolidation_max_tokens", 64)
            )
            self.consolidation_max_probes_spin.setValue(
                self.current_config.get("consolidation_max_probes", 6)
            )
            self.consolidation_min_facts_spin.setValue(
                self.current_config.get("consolidation_min_facts", 3)
            )

            # Load template
            template_applied = self.current_config.get(
                "template_applied", "Custom (no template)"
            )
            index = self.template_combo.findText(template_applied)
            if index >= 0:
                self.template_combo.setCurrentIndex(index)

            # Skills tab settings
            skills = self.current_config.get("skills", [])
            self._populate_skills_table(skills)
            self.skill_routing_check.setChecked(
                self.current_config.get("use_skill_routing", True)
            )

            # Update visibility based on input source
        self.update_input_source_visibility()

    def _apply_node_mode_visibility(self, *args):
        """Show the orchestrator fields (hide the chat tabs) while ON."""
        try:
            on = bool(self.orchestrator_mode_check.isChecked())
        except Exception:
            on = False
        try:
            self.orch_group.setVisible(on)
        except Exception:
            pass
        try:
            tabs = self.tab_widget
            for i in range(tabs.count()):
                if tabs.tabText(i) == _("Model"):
                    continue
                try:
                    tabs.setTabVisible(i, not on)
                except Exception:
                    pass
        except Exception:
            pass
        try:
            self._refit_to_current_tab()
        except Exception:
            pass

    def get_config(self):
        """Get the LLM configuration from the dialog"""
        # Determine which engine is selected
        use_llamacpp = self.engine_combo.currentText() == "llama.cpp"

        config = {
            "use_llamacpp": use_llamacpp,
            "model": self.model_combo.currentText() if not use_llamacpp else "llamacpp",
            "llamacpp_model_path": self.llamacpp_model_combo.currentText()
            if use_llamacpp
            else "",
            "llamacpp_gpu_layers": self.gpu_layers_spin.value(),  # 0 = CPU-first
            "llamacpp_threads": -1,  # -1 = auto-detect
            "llamacpp_context_size": self.context_size_spin.value(),
            "api_url": self.api_url_entry.text() or get_default_api_url(),
            "system_message": self.system_message_edit.toPlainText(),
            "prompt": self.prompt_edit.toPlainText(),
            "temperature": self.temperature_spin.value(),
            "output_variable": self.output_variable_entry.text() or "llm_output",
            "orchestrator_mode": self.orchestrator_mode_check.isChecked(),
            "orch_max_steps": self.orch_max_steps_spin.value(),
            "orch_goal": self.orch_goal_edit.toPlainText(),
            "orch_synthesize": self.orch_synthesize_check.isChecked(),
            "orch_synthesis_system": self.orch_synthesis_system_edit.text(),
            "orch_use_goal_ledger": self.orch_use_goal_ledger_check.isChecked(),
            "write_text": self.write_text_checkbox.isChecked(),
            "use_async": self.use_async_checkbox.isChecked(),
            "use_vision": self.use_vision_checkbox.isChecked(),
            "vision_model": "",  # No separate vision model — images go directly to the main model
            "screenshot_enabled": self.screenshot_enabled_checkbox.isChecked(),
            # Typing speed parameters
            "typing_batch_size": self.typing_batch_size_spin.value(),
            "typing_batch_delay": self.typing_batch_delay_spin.value(),
        }

        # Add input source configuration
        selected_button = self.input_source_group.checkedButton()
        if selected_button:
            source_id = self.input_source_group.id(selected_button)
            mapping = {0: "none", 1: "ocr", 2: "page_text", 3: "clipboard"}
            config["input_source"] = mapping.get(source_id, "none")
        else:
            config["input_source"] = "none"

        config["ocr_confidence"] = self.ocr_confidence_spin.value()
        config["ocr_preprocessing"] = self.ocr_preprocessing_combo.currentText()
        config["web_mode"] = bool(self.web_mode_checkbox.isChecked())
        config["ocr_region"] = self._ocr_region or ""
        config["web_ocr_locator"] = self._web_ocr_locator or ""
        config["template_applied"] = self.template_combo.currentText()

        # Determine RAG usage directly from RAG tab toggle
        use_direct_rag_value = self.use_direct_rag_checkbox.isChecked()
        config.update(
            {
                "use_direct_rag": use_direct_rag_value,
                "rag_embedding_model": self.rag_embedding_model_entry.text().strip(),
                "rag_chunk_size": self.rag_chunk_size_spin.value(),
                "rag_overlap": self.rag_overlap_spin.value(),
                "rag_top_k": self.rag_top_k_spin.value(),
                "rag_include_raw_input": self.rag_include_raw_input_checkbox.isChecked(),
                "rag_max_chars": self.rag_max_chars_spin.value(),
                "rag_documents": [
                    self.rag_documents_list.item(i).text()
                    for i in range(self.rag_documents_list.count())
                ],
                # Context Consolidation config
                "use_context_consolidation": self.use_consolidation_checkbox.isChecked(),
                "consolidation_chunk_size": self.consolidation_chunk_size_spin.value(),
                "consolidation_overlap": self.consolidation_overlap_spin.value(),
                "consolidation_top_k": self.consolidation_top_k_spin.value(),
                "consolidation_max_tokens": self.consolidation_max_tokens_spin.value(),
                "consolidation_max_probes": self.consolidation_max_probes_spin.value(),
                "consolidation_min_facts": self.consolidation_min_facts_spin.value(),
            }
        )
        td = {}
        try:
            for i in range(self.tools_list.count()):
                txt = self.tools_list.item(i).text()
                if ":" in txt:
                    k, v = txt.split(":", 1)
                    td[k.strip()] = v.strip()
        except Exception:
            td = {}
        config["tool_descriptions"] = td

        # Skills configuration
        config["skills"] = self._get_skills_list()
        config["use_skill_routing"] = self.skill_routing_check.isChecked()

        return config

    def create_knowledge_tab(self):
        rag_tab = QWidget()
        layout = QVBoxLayout(rag_tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        params_group = QGroupBox("RAG Configuration")
        params_layout = QGridLayout(params_group)
        params_layout.setContentsMargins(8, 12, 8, 8)
        params_layout.setSpacing(6)
        params_layout.addWidget(QLabel("Enable RAG:"), 0, 0)
        self.use_direct_rag_checkbox = ModernToggle(
            text="Use embeddings to add context"
        )
        self.use_direct_rag_checkbox.setChecked(True)
        params_layout.addWidget(self.use_direct_rag_checkbox, 0, 1)
        params_layout.addWidget(QLabel("Embedding Model:"), 1, 0)
        self.rag_embedding_model_entry = QLineEdit()
        self.rag_embedding_model_entry.setPlaceholderText(
            "auto — llama.cpp: embeddinggemma / Ollama: nomic-embed-text"
        )
        params_layout.addWidget(self.rag_embedding_model_entry, 1, 1)
        params_layout.addWidget(QLabel("Chunk Size:"), 2, 0)
        self.rag_chunk_size_spin = QSpinBox()
        self.rag_chunk_size_spin.setRange(50, 5000)
        self.rag_chunk_size_spin.setValue(500)
        params_layout.addWidget(self.rag_chunk_size_spin, 2, 1)
        params_layout.addWidget(QLabel("Overlap:"), 3, 0)
        self.rag_overlap_spin = QSpinBox()
        self.rag_overlap_spin.setRange(0, 1000)
        self.rag_overlap_spin.setValue(100)
        params_layout.addWidget(self.rag_overlap_spin, 3, 1)
        params_layout.addWidget(QLabel("Top K:"), 4, 0)
        self.rag_top_k_spin = QSpinBox()
        self.rag_top_k_spin.setRange(1, 20)
        self.rag_top_k_spin.setValue(3)
        params_layout.addWidget(self.rag_top_k_spin, 4, 1)
        params_layout.addWidget(QLabel("Include Raw Input:"), 5, 0)
        self.rag_include_raw_input_checkbox = ModernToggle(
            text="Also include raw previous input"
        )
        params_layout.addWidget(self.rag_include_raw_input_checkbox, 5, 1)
        params_layout.addWidget(QLabel("Max Context Chars:"), 6, 0)
        self.rag_max_chars_spin = QSpinBox()
        self.rag_max_chars_spin.setRange(100, 10000)
        self.rag_max_chars_spin.setValue(1500)
        params_layout.addWidget(self.rag_max_chars_spin, 6, 1)
        layout.addWidget(params_group)

        # ── Context Consolidation section ──
        cons_group = QGroupBox("Context Consolidation (ComoRAG)")
        cons_layout = QGridLayout(cons_group)
        cons_layout.setContentsMargins(8, 12, 8, 8)
        cons_layout.setSpacing(6)

        cons_layout.addWidget(QLabel("Enable Consolidation:"), 0, 0)
        self.use_consolidation_checkbox = ModernToggle(
            text="Probe-based context consolidation (opt-in)"
        )
        self.use_consolidation_checkbox.setChecked(False)
        cons_layout.addWidget(self.use_consolidation_checkbox, 0, 1)

        cons_layout.addWidget(QLabel("Chunk Size:"), 1, 0)
        self.consolidation_chunk_size_spin = QSpinBox()
        self.consolidation_chunk_size_spin.setRange(100, 5000)
        self.consolidation_chunk_size_spin.setValue(1000)
        cons_layout.addWidget(self.consolidation_chunk_size_spin, 1, 1)

        cons_layout.addWidget(QLabel("Overlap:"), 2, 0)
        self.consolidation_overlap_spin = QSpinBox()
        self.consolidation_overlap_spin.setRange(0, 1000)
        self.consolidation_overlap_spin.setValue(200)
        cons_layout.addWidget(self.consolidation_overlap_spin, 2, 1)

        cons_layout.addWidget(QLabel("Top K (per probe):"), 3, 0)
        self.consolidation_top_k_spin = QSpinBox()
        self.consolidation_top_k_spin.setRange(1, 20)
        self.consolidation_top_k_spin.setValue(5)
        cons_layout.addWidget(self.consolidation_top_k_spin, 3, 1)

        cons_layout.addWidget(QLabel("Probe Max Tokens:"), 4, 0)
        self.consolidation_max_tokens_spin = QSpinBox()
        self.consolidation_max_tokens_spin.setRange(64, 2048)
        self.consolidation_max_tokens_spin.setValue(64)
        self.consolidation_max_tokens_spin.setToolTip(
            "Max tokens per LLM-generated retrieval probe. Probes are written "
            "by the node's own model; failures fall back to lexical probes."
        )
        cons_layout.addWidget(self.consolidation_max_tokens_spin, 4, 1)

        cons_layout.addWidget(QLabel("Max Probes (cycle ceiling):"), 5, 0)
        self.consolidation_max_probes_spin = QSpinBox()
        self.consolidation_max_probes_spin.setRange(1, 12)
        self.consolidation_max_probes_spin.setValue(6)
        self.consolidation_max_probes_spin.setToolTip(
            "Ceiling on probing cycles. The sweep normally stops earlier — "
            "the moment the judge's answer is grounded, or when a cycle "
            "adds no new on-topic fact."
        )
        cons_layout.addWidget(self.consolidation_max_probes_spin, 5, 1)

        cons_layout.addWidget(QLabel("Min Facts / Cycle:"), 6, 0)
        self.consolidation_min_facts_spin = QSpinBox()
        self.consolidation_min_facts_spin.setRange(1, 8)
        self.consolidation_min_facts_spin.setValue(3)
        self.consolidation_min_facts_spin.setToolTip(
            "Composed facts a cycle must accumulate before it counts as done. "
            "Cycles producing no facts are invalid and re-probe with feedback."
        )
        cons_layout.addWidget(self.consolidation_min_facts_spin, 6, 1)

        layout.addWidget(cons_group)

        docs_group = QGroupBox("Documents")
        docs_layout = QVBoxLayout(docs_group)
        self.rag_documents_list = QListWidget()
        docs_layout.addWidget(self.rag_documents_list)
        btns = QHBoxLayout()
        add_btn = QPushButton("Add Document")
        rem_btn = QPushButton("Remove Selected")
        add_btn.clicked.connect(self.add_document)
        rem_btn.clicked.connect(self.remove_selected_document)
        btns.addWidget(add_btn)
        btns.addWidget(rem_btn)
        docs_layout.addLayout(btns)
        layout.addWidget(docs_group)
        layout.addStretch()
        self.tab_widget.addTab(rag_tab, _("Knowledge"))

    def create_tools_skills_tab(self):
        """Tools & Skills tab: connected tools and the skills library."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # ── Connected tools ──
        group = QGroupBox(_("Tools Configuration"))
        gl = QGridLayout(group)
        gl.setContentsMargins(8, 12, 8, 8)
        gl.setSpacing(6)
        self.tools_list = QListWidget()
        gl.addWidget(QLabel(_("Connected Tools (id: description)")), 0, 0, 1, 2)
        gl.addWidget(self.tools_list, 1, 0, 1, 2)
        gl.addWidget(QLabel(_("Tool ID")), 2, 0)
        self.tool_id_entry = QLineEdit()
        gl.addWidget(self.tool_id_entry, 2, 1)
        gl.addWidget(QLabel(_("Description")), 3, 0)
        self.tool_desc_entry = QLineEdit()
        gl.addWidget(self.tool_desc_entry, 3, 1)
        btn_row = QHBoxLayout()
        add_btn = QPushButton(_("Add/Update"))
        rem_btn = QPushButton(_("Remove Selected"))
        btn_row.addWidget(add_btn)
        btn_row.addWidget(rem_btn)
        gl.addLayout(btn_row, 4, 0, 1, 2)
        layout.addWidget(group)

        def on_add():
            try:
                tid = self.tool_id_entry.text().strip()
                desc = self.tool_desc_entry.text().strip()
                if not tid:
                    return
                row_text = f"{tid}: {desc}" if desc else tid
                found = None
                for i in range(self.tools_list.count()):
                    if (
                        self.tools_list.item(i).text().startswith(tid + ":")
                        or self.tools_list.item(i).text() == tid
                    ):
                        found = i
                        break
                if found is not None:
                    self.tools_list.item(found).setText(row_text)
                else:
                    self.tools_list.addItem(row_text)
            except Exception:
                pass

        def on_remove():
            try:
                row = self.tools_list.currentRow()
                if row >= 0:
                    self.tools_list.takeItem(row)
            except Exception:
                pass

        def on_select():
            try:
                row = self.tools_list.currentRow()
                if row >= 0:
                    txt = self.tools_list.item(row).text()
                    if ":" in txt:
                        tid, desc = txt.split(":", 1)
                        self.tool_id_entry.setText(tid.strip())
                        self.tool_desc_entry.setText(desc.strip())
                    else:
                        self.tool_id_entry.setText(txt.strip())
                        self.tool_desc_entry.setText("")
            except Exception:
                pass

        def on_double_click(item):
            try:
                txt = item.text()
                if ":" in txt:
                    tid, desc = txt.split(":", 1)
                else:
                    tid, desc = txt, ""
                new_desc, ok = QInputDialog.getText(
                    self,
                    _("Edit Description"),
                    _("Description for {id}").format(id=tid.strip()),
                    text=desc.strip(),
                )
                if ok:
                    new_txt = f"{tid.strip()}: {new_desc.strip()}"
                    item.setText(new_txt)
                    self.tool_id_entry.setText(tid.strip())
                    self.tool_desc_entry.setText(new_desc.strip())
            except Exception:
                pass

        try:
            self.tool_id_entry.setReadOnly(True)
        except Exception:
            pass
        try:
            self.tools_list.setSelectionMode(QListWidget.SingleSelection)
        except Exception:
            pass
        add_btn.clicked.connect(on_add)
        rem_btn.clicked.connect(on_remove)
        self.tools_list.itemSelectionChanged.connect(on_select)
        self.tools_list.itemDoubleClicked.connect(on_double_click)

        # ── Skills list ──
        skills_list_group = QGroupBox(_("Skills List"))
        skills_list_layout = QVBoxLayout(skills_list_group)
        skills_list_layout.setContentsMargins(8, 12, 8, 8)
        skills_list_layout.setSpacing(6)

        self.skills_table = QTableWidget()
        self.skills_table.setColumnCount(5)
        self.skills_table.setHorizontalHeaderLabels(
            [_("Enable"), _("Name"), _("Description"), _("Priority"), _("ID")]
        )
        self.skills_table.setColumnHidden(4, True)  # Hide ID column
        self.skills_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.skills_table.setSelectionMode(QTableWidget.SingleSelection)
        self.skills_table.horizontalHeader().setStretchLastSection(True)
        self.skills_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch
        )
        self.skills_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.Stretch
        )
        self.skills_table.setMaximumHeight(150)
        self.skills_table.itemSelectionChanged.connect(self.on_skill_selected)
        skills_list_layout.addWidget(self.skills_table)

        skill_btn_layout = QHBoxLayout()
        add_skill_btn = QPushButton(_("Add Skill"))
        remove_skill_btn = QPushButton(_("Remove Skill"))
        add_skill_btn.clicked.connect(self.add_new_skill)
        remove_skill_btn.clicked.connect(self.remove_selected_skill)
        skill_btn_layout.addWidget(add_skill_btn)
        skill_btn_layout.addWidget(remove_skill_btn)
        skill_btn_layout.addStretch()
        skills_list_layout.addLayout(skill_btn_layout)
        layout.addWidget(skills_list_group)

        # ── Skill editor ──
        editor_group = QGroupBox(_("Skill Editor"))
        editor_grid = QGridLayout(editor_group)
        editor_grid.setContentsMargins(8, 12, 8, 8)
        editor_grid.setSpacing(6)

        editor_grid.addWidget(QLabel(_("Skill Name:")), 0, 0)
        self.skill_name_edit = QLineEdit()
        self.skill_name_edit.setPlaceholderText(_("e.g., Data Extraction"))
        editor_grid.addWidget(self.skill_name_edit, 0, 1)

        editor_grid.addWidget(QLabel(_("Description:")), 1, 0)
        self.skill_desc_edit = QLineEdit()
        self.skill_desc_edit.setPlaceholderText(
            _("One-line summary for context-aware routing")
        )
        editor_grid.addWidget(self.skill_desc_edit, 1, 1)

        editor_grid.addWidget(QLabel(_("Trigger Keywords:")), 2, 0)
        self.skill_keywords_edit = QLineEdit()
        self.skill_keywords_edit.setPlaceholderText(
            _("comma-separated, e.g., extract,parse,structure")
        )
        editor_grid.addWidget(self.skill_keywords_edit, 2, 1)

        editor_grid.addWidget(QLabel(_("Instructions:")), 3, 0)
        self.skill_instructions_edit = QTextEdit()
        self.skill_instructions_edit.setPlaceholderText(
            _("Detailed instructions for the LLM when this skill is active")
        )
        self.skill_instructions_edit.setMaximumHeight(80)
        editor_grid.addWidget(self.skill_instructions_edit, 3, 1)

        editor_grid.addWidget(QLabel(_("Priority:")), 4, 0)
        self.skill_priority_spin = QSpinBox()
        self.skill_priority_spin.setRange(1, 10)
        self.skill_priority_spin.setValue(5)
        editor_grid.addWidget(self.skill_priority_spin, 4, 1)

        save_skill_btn = QPushButton(_("Save Skill"))
        save_skill_btn.clicked.connect(self.save_current_skill)
        editor_grid.addWidget(save_skill_btn, 5, 0, 1, 2)
        layout.addWidget(editor_group)

        # ── AI skill generator ──
        ai_group = QGroupBox(_("AI Skill Generator"))
        ai_layout = QGridLayout(ai_group)
        ai_layout.setContentsMargins(8, 12, 8, 8)
        ai_layout.setSpacing(6)

        ai_layout.addWidget(QLabel(_("Model:")), 0, 0)
        model_layout = QHBoxLayout()
        self.skill_gen_model_combo = QComboBox()
        self.skill_gen_model_combo.setEditable(True)
        try:
            models = get_real_cached_models(timeout=2.0)
            if models:
                self.skill_gen_model_combo.addItems(models)
            else:
                self.skill_gen_model_combo.addItem("llama3.2:latest")
        except Exception:
            self.skill_gen_model_combo.addItem("llama3.2:latest")
        model_layout.addWidget(self.skill_gen_model_combo)
        refresh_btn = QPushButton(_("Refresh"))
        refresh_btn.clicked.connect(self.refresh_skill_gen_models)
        model_layout.addWidget(refresh_btn)
        ai_layout.addLayout(model_layout, 0, 1)

        ai_layout.addWidget(QLabel(_("What should this skill do?")), 1, 0)
        self.skill_gen_prompt_edit = QTextEdit()
        self.skill_gen_prompt_edit.setPlaceholderText(
            _("Describe what this skill should accomplish...")
        )
        self.skill_gen_prompt_edit.setMaximumHeight(60)
        ai_layout.addWidget(self.skill_gen_prompt_edit, 1, 1)

        gen_skill_btn = QPushButton(_("Generate Skill"))
        gen_skill_btn.clicked.connect(self.generate_skill)
        ai_layout.addWidget(gen_skill_btn, 2, 0, 1, 2)

        self.skill_gen_status = QLabel("")
        self.skill_gen_status.setStyleSheet("color: #888; font-style: italic;")
        self.skill_gen_status.setAlignment(Qt.AlignCenter)
        ai_layout.addWidget(self.skill_gen_status, 3, 0, 1, 2)
        layout.addWidget(ai_group)

        # ── Skill routing ──
        routing_group = QGroupBox(_("Skill Routing"))
        routing_layout = QVBoxLayout(routing_group)
        routing_layout.setContentsMargins(8, 12, 8, 8)
        routing_layout.setSpacing(6)
        self.skill_routing_check = QCheckBox(_("Enable context-aware skill routing"))
        self.skill_routing_check.setChecked(True)
        routing_layout.addWidget(self.skill_routing_check)
        routing_info = QLabel(
            _(
                "When enabled, skills are selected based on context from connected Context nodes. When disabled, all enabled skills are injected."
            )
        )
        routing_info.setWordWrap(True)
        routing_info.setStyleSheet("color: #888; font-size: 10px; margin-top: 3px;")
        routing_layout.addWidget(routing_info)
        layout.addWidget(routing_group)

        layout.addStretch()
        self.tab_widget.addTab(tab, _("Tools & Skills"))

    def load_tool_descriptions(self, mapping):
        try:
            self.tools_list.clear()
            if isinstance(mapping, dict):
                for k, v in mapping.items():
                    self.tools_list.addItem(f"{k}: {v}")
        except Exception:
            pass

    def refresh_skill_gen_models(self):
        """Refresh models for skill generator"""
        try:
            api_url = self.api_url_entry.text() or get_default_api_url()
            current_model = self.skill_gen_model_combo.currentText()
            cache = get_model_cache()
            if cache:
                cache.refresh_models(api_url)
            models = get_real_cached_models(timeout=5.0)
            if models:
                self.skill_gen_model_combo.clear()
                self.skill_gen_model_combo.addItems(models)
                if current_model in models:
                    self.skill_gen_model_combo.setCurrentText(current_model)
        except Exception as e:
            QMessageBox.warning(self, _("Refresh Failed"), str(e))

    def generate_skill(self):
        """Generate a skill using AI"""
        prompt = self.skill_gen_prompt_edit.toPlainText().strip()
        if not prompt:
            QMessageBox.warning(
                self,
                _("Missing Prompt"),
                _("Please describe what the skill should do."),
            )
            return

        model = self.skill_gen_model_combo.currentText()
        api_url = self.api_url_entry.text() or get_default_api_url()

        self.skill_gen_status.setText(_("Generating skill..."))

        self.skill_gen_thread = SkillGeneratorThread(model, prompt, api_url)
        self.skill_gen_thread.finished.connect(self.on_skill_generation_finished)
        self.skill_gen_thread.error.connect(self.on_skill_generation_error)
        self.skill_gen_thread.start()

    def on_skill_generation_finished(self, skill_data):
        """Handle successful skill generation"""
        self.skill_name_edit.setText(skill_data.get("name", ""))
        self.skill_desc_edit.setText(skill_data.get("description", ""))
        self.skill_keywords_edit.setText(skill_data.get("trigger_keywords", ""))
        self.skill_instructions_edit.setPlainText(skill_data.get("instructions", ""))
        self.skill_gen_status.setText(_("Skill generated successfully!"))

    def on_skill_generation_error(self, error_msg):
        """Handle skill generation error"""
        self.skill_gen_status.setText(_("Generation failed."))
        QMessageBox.critical(self, _("Generation Error"), error_msg)

    def add_new_skill(self):
        """Add a new empty skill to the table"""
        row = self.skills_table.rowCount()
        self.skills_table.insertRow(row)

        # Create checkbox for enabled
        checkbox = QCheckBox()
        checkbox.setChecked(True)
        checkbox_widget = QWidget()
        checkbox_layout = QHBoxLayout(checkbox_widget)
        checkbox_layout.addWidget(checkbox)
        checkbox_layout.setAlignment(Qt.AlignCenter)
        checkbox_layout.setContentsMargins(0, 0, 0, 0)

        skill_id = str(uuid.uuid4())
        self.skills_table.setCellWidget(row, 0, checkbox_widget)
        self.skills_table.setItem(row, 1, QTableWidgetItem(_("New Skill")))
        self.skills_table.setItem(row, 2, QTableWidgetItem(_("")))
        self.skills_table.setItem(row, 3, QTableWidgetItem("5"))
        self.skills_table.setItem(row, 4, QTableWidgetItem(skill_id))

        # Select the new row
        self.skills_table.selectRow(row)
        self.on_skill_selected()

    def remove_selected_skill(self):
        """Remove the selected skill from the table"""
        row = self.skills_table.currentRow()
        if row >= 0:
            self.skills_table.removeRow(row)
            # Clear editor
            self.skill_name_edit.clear()
            self.skill_desc_edit.clear()
            self.skill_keywords_edit.clear()
            self.skill_instructions_edit.clear()
            self.skill_priority_spin.setValue(5)

    def on_skill_selected(self):
        """Populate editor when a skill row is selected"""
        row = self.skills_table.currentRow()
        if row < 0:
            return

        name_item = self.skills_table.item(row, 1)
        desc_item = self.skills_table.item(row, 2)
        priority_item = self.skills_table.item(row, 3)

        # Get full skill data from the cell's stored data
        skill_data = self._get_skill_data_from_row(row)

        self.skill_name_edit.setText(name_item.text() if name_item else "")
        self.skill_desc_edit.setText(desc_item.text() if desc_item else "")
        self.skill_keywords_edit.setText(skill_data.get("trigger_keywords", ""))
        self.skill_instructions_edit.setPlainText(skill_data.get("instructions", ""))

        try:
            priority = int(priority_item.text()) if priority_item else 5
        except ValueError:
            priority = 5
        self.skill_priority_spin.setValue(priority)

    def _get_skill_data_from_row(self, row):
        """Get full skill data from a table row"""
        # Store full skill data in the Name item's data role
        name_item = self.skills_table.item(row, 1)
        if name_item:
            data = name_item.data(Qt.UserRole)
            if data:
                return data
        return {}

    def _set_skill_data_to_row(self, row, skill_data):
        """Store full skill data in a table row"""
        name_item = self.skills_table.item(row, 1)
        if name_item:
            name_item.setData(Qt.UserRole, skill_data)

    def save_current_skill(self):
        """Save the current skill editor values to the selected table row"""
        row = self.skills_table.currentRow()
        if row < 0:
            # No row selected, add new
            self.add_new_skill()
            row = self.skills_table.currentRow()

        if row >= 0:
            # Update the table
            self.skills_table.item(row, 1).setText(self.skill_name_edit.text())
            self.skills_table.item(row, 2).setText(self.skill_desc_edit.text())
            self.skills_table.item(row, 3).setText(
                str(self.skill_priority_spin.value())
            )

            # Store full data
            skill_data = {
                "trigger_keywords": self.skill_keywords_edit.text(),
                "instructions": self.skill_instructions_edit.toPlainText(),
            }
            self._set_skill_data_to_row(row, skill_data)

    def _get_skills_list(self):
        """Get the list of skills from the table"""
        skills = []
        for row in range(self.skills_table.rowCount()):
            checkbox_widget = self.skills_table.cellWidget(row, 0)
            checkbox = checkbox_widget.findChild(QCheckBox) if checkbox_widget else None
            enabled = checkbox.isChecked() if checkbox else True

            name_item = self.skills_table.item(row, 1)
            desc_item = self.skills_table.item(row, 2)
            priority_item = self.skills_table.item(row, 3)
            id_item = self.skills_table.item(row, 4)

            skill_data = self._get_skill_data_from_row(row)

            try:
                priority = int(priority_item.text()) if priority_item else 5
            except ValueError:
                priority = 5

            skill = {
                "id": id_item.text() if id_item else str(uuid.uuid4()),
                "name": name_item.text() if name_item else "",
                "description": desc_item.text() if desc_item else "",
                "instructions": skill_data.get("instructions", ""),
                "trigger_keywords": skill_data.get("trigger_keywords", ""),
                "priority": priority,
                "enabled": enabled,
            }
            skills.append(skill)
        return skills

    def _populate_skills_table(self, skills):
        """Populate the skills table with a list of skill dicts"""
        self.skills_table.setRowCount(0)
        for skill in skills:
            row = self.skills_table.rowCount()
            self.skills_table.insertRow(row)

            # Checkbox for enabled
            checkbox = QCheckBox()
            checkbox.setChecked(skill.get("enabled", True))
            checkbox_widget = QWidget()
            checkbox_layout = QHBoxLayout(checkbox_widget)
            checkbox_layout.addWidget(checkbox)
            checkbox_layout.setAlignment(Qt.AlignCenter)
            checkbox_layout.setContentsMargins(0, 0, 0, 0)

            self.skills_table.setCellWidget(row, 0, checkbox_widget)
            self.skills_table.setItem(row, 1, QTableWidgetItem(skill.get("name", "")))
            self.skills_table.setItem(
                row, 2, QTableWidgetItem(skill.get("description", ""))
            )
            self.skills_table.setItem(
                row, 3, QTableWidgetItem(str(skill.get("priority", 5)))
            )
            self.skills_table.setItem(
                row, 4, QTableWidgetItem(skill.get("id", str(uuid.uuid4())))
            )

            # Store full data
            skill_data = {
                "trigger_keywords": skill.get("trigger_keywords", ""),
                "instructions": skill.get("instructions", ""),
            }
            self._set_skill_data_to_row(row, skill_data)

    def add_document(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Document",
            os.path.expanduser("~"),
            "All Files (*.*);;Text Files (*.txt *.md *.csv *.json);;Word Documents (*.docx);;PDF Files (*.pdf)",
        )
        if path:
            self.rag_documents_list.addItem(path)

    def remove_selected_document(self):
        items = self.rag_documents_list.selectedItems()
        for it in items:
            row = self.rag_documents_list.row(it)
            self.rag_documents_list.takeItem(row)

    def apply_template(self, template_name):
        """Apply a predefined template to configure the LLM node"""
        templates = {
            "Information Extraction (OCR + LLM)": {
                "description": "Extract specific information from screen text using OCR and LLM",
                "input_source": 1,  # OCR
                "system_message": "You are an expert at extracting specific information from text. Extract the requested information accurately and concisely.",
                "prompt": "Extract the following information from the text: [SPECIFY WHAT TO EXTRACT]",
                "temperature": 0.3,
                "max_tokens": 4096,
                "ocr_confidence": 0.7,
                "ocr_preprocessing": "Clean formatting",
            },
            "Form Filling (Vision + LLM)": {
                "description": "Fill out on-screen forms by analyzing pruned UI elements and providing answers from context documents.",
                "input_source": 1,  # We can reuse OCR mode or create a new one. Let's use 1 to trigger screen reading.
                "system_message": "You are an autonomous Task Planning Agent. Your goal is to achieve the user's objective using the provided RAG document context and the current UI screen state.\n\nYou can execute physical actions on the screen by outputting EXACTLY the following commands on separate lines. You may output a plan containing between 1 and 10 actions:\nCLICK: x, y\nTYPE: exact text to type\nSCROLL: amount (e.g., -500 to scroll down, 500 to scroll up)\nWAIT: seconds (e.g., 0.5)\n\nRULES:\n1. Look at the JSON_SCREEN_MAP object containing the UI fields.\n2. Determine the sequence of actions required to progress the task.\n3. Output a sequential plan of actions (up to 10 steps).\n4. DO NOT click submit or complete unless all necessary preceding steps are done.\n\nTo fill an input:\nCLICK: x, y (use the center_x, center_y coordinates of the input box)\nTYPE: John Doe\n\nTo open a dropdown/combobox:\nCLICK: x, y\nWAIT: 0.5\nCLICK: x, y (coordinates of the option)\n\nTo click a toggle/checkbox/radio:\nCLICK: x, y\n\nIf you cannot see the required fields, use SCROLL: -500 to scroll down.\nIf all fields are filled or the task is done, you may click the final action button. If no further actions are needed, output DONE.\nOnly respond with the sequence of action commands. Do not include markdown blocks, explanations, or quotes.",
                "prompt": "Analyze the UI elements provided in the JSON_SCREEN_MAP and provide a multi-step plan (1-10 actions) to progress the task.\nUse the attached RAG context for any necessary information. Output only action commands.",
                "temperature": 0.1,
                "max_tokens": 512,
                "use_direct_rag": True,
                "ocr_confidence": 0.7,
            },
            "Custom Instructions": {
                "description": "Provide custom instructions for the LLM to follow",
                "input_source": 0,  # No input
                "system_message": "You are a helpful assistant that follows instructions precisely.",
                "prompt": "[ENTER YOUR CUSTOM INSTRUCTIONS HERE]",
                "temperature": 0.7,
                "max_tokens": 4096,
            },
            "Direct Prompt": {
                "description": "Simple direct prompt without any special configuration",
                "input_source": 0,  # No input
                "system_message": "",
                "prompt": "[ENTER YOUR PROMPT HERE]",
                "temperature": 0.7,
                "max_tokens": 4096,
            },
            "Data Processing Chain": {
                "description": "Process data from previous node for multi-step automation",
                "input_source": 0,  # connections feed the data
                "system_message": "You are a data processing node in an automation chain. Process the input data according to the specified requirements.",
                "prompt": "Process the input data to: [SPECIFY PROCESSING REQUIREMENTS]",
                "temperature": 0.3,
                "max_tokens": 4096,
            },
            "Text Analysis": {
                "description": "Analyze text content with OCR input",
                "input_source": 1,  # OCR
                "system_message": "You are a text analysis expert. Analyze the provided text and extract insights, patterns, or requested information.",
                "prompt": "Analyze the text and provide: [SPECIFY ANALYSIS REQUIREMENTS]",
                "temperature": 0.4,
                "max_tokens": 4096,
                "ocr_confidence": 0.6,
                "ocr_preprocessing": "Remove extra spaces",
            },
            "Content Summarization": {
                "description": "Summarize content from screen or previous node",
                "input_source": 1,  # OCR (can be changed to 2 for previous node)
                "system_message": "You are an expert at creating concise, accurate summaries. Summarize the content clearly and effectively.",
                "prompt": "Summarize the key points from the content. Focus on the most important information.",
                "temperature": 0.3,
                "max_tokens": 4096,
                "ocr_confidence": 0.6,
                "ocr_preprocessing": "Clean formatting",
            },
            "Tool Selection (LLM Tools)": {
                "description": "Configure base system prompt for tool use. Respond only with 'USE_TOOL:<id>'.",
                "input_source": 0,
                "system_message": "You select tools to fulfill the user's request. Choose exactly one tool that best matches the request using the available descriptions. When a tool is needed, respond ONLY with 'USE_TOOL:<id>' and nothing else.",
                "prompt": "Decide which connected tool best solves the user's request and respond ONLY with 'USE_TOOL:<tool_id>'.",
                "temperature": 0.1,
                "max_tokens": 150,
            },
            "Python Code Generation": {
                "description": "Generate executable Python code in a single block.",
                "input_source": 0,
                "system_message": "You are a Python code generator. Your ONLY task is to output executable Python code.\\n1. Output the entire script in a SINGLE markdown code block (```python ... ```).\\n2. Do NOT split the code into multiple blocks.\\n3. Do NOT add any explanations, comments, steps, or text outside the code block.\\n4. Ensure all imports and logic are contained within that one block.",
                "prompt": "Write a complete, single-file Python script to: [TASK_DESCRIPTION]\\n\\nIMPORTANT: Provide ONLY the raw python code inside a single ```python``` block. No other text.",
                "temperature": 0.2,
                "max_tokens": 4096,
            },
        }

        # Update description
        if template_name in templates:
            template = templates[template_name]
            self.template_description.setText(template["description"])

            # Only apply template if it's not the "Custom" option
            if template_name != "Custom (no template)":
                self.apply_template_config(template)
        else:
            self.template_description.setText("")

    def apply_template_config(self, template):
        """Apply the template configuration to the dialog"""
        # Input source
        if "input_source" in template:
            input_source_id = template["input_source"]
            button = self.input_source_group.button(input_source_id)
            if button:
                button.setChecked(True)
                self.update_input_source_visibility()

        # System message
        if "system_message" in template:
            self.system_message_edit.setPlainText(template["system_message"])

        # Prompt
        if "prompt" in template:
            self.prompt_edit.setPlainText(template["prompt"])

        # Temperature
        if "temperature" in template:
            self.temperature_spin.setValue(template["temperature"])

        # OCR settings
        if "ocr_confidence" in template:
            self.ocr_confidence_spin.setValue(template["ocr_confidence"])

        if "ocr_preprocessing" in template:
            index = self.ocr_preprocessing_combo.findText(template["ocr_preprocessing"])
            if index >= 0:
                self.ocr_preprocessing_combo.setCurrentIndex(index)

    def update_input_source_visibility(self):
        """Show the OCR settings only while OCR is the chosen external reader."""
        input_source_id = self.input_source_group.checkedId()
        try:
            self.ocr_config_widget.setVisible(input_source_id == 1)  # OCR
        except Exception:
            pass
        # Web page text is only meaningful on the chain's browser.
        try:
            web_mode = bool(self.web_mode_checkbox.isChecked())
            self.input_page_text_radio.setEnabled(web_mode)
            if not web_mode and self.input_page_text_radio.isChecked():
                self.input_none_radio.setChecked(True)
        except Exception:
            pass
