from PyQt5.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton,
    QWidget, QSpinBox, QComboBox, QMessageBox,
    QTextBrowser, QSplitter, QCheckBox
)
from PyQt5.QtGui import QFont, QTextCursor
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QProcess, QTimer
from .base_dialog import ModernDialog
from ..constants import TEXT_COLOR, LIGHT_GREY, ACCENT_COLOR, DARK_GREY, MEDIUM_GREY
from ..nodes_resources.base_node import get_default_api_url
from ..i18n import _
import sys
import os
import re

# Import AI modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
try:
    from AI.consult import OllamaClient
    from AI.model_cache import get_cached_models, get_real_cached_models, get_model_cache
except ImportError:
    OllamaClient = None
    get_cached_models = lambda: []
    get_real_cached_models = lambda timeout=5.0: []
    get_model_cache = lambda: None

# Import llama.cpp model scanner
try:
    from .llm_dialogs import get_llamacpp_models
except ImportError:
    get_llamacpp_models = lambda: []


# ---------------------------------------------------------------------------
# System prompt for the coding agent
# ---------------------------------------------------------------------------
CODING_AGENT_SYSTEM = (
    "You are an expert Python developer and coding assistant integrated into a workflow automation tool. "
    "The user will ask you to write, modify, or debug Python code. "
    "Your response MUST contain the final code inside a Python fenced code block: ```python\n...\n```"
    "You may include explanations OUTSIDE the code block. "
    "The code will be executed in a local environment where:\n"
    "- 'input_data' and 'args' variables are available from the previous node (if any)\n"
    "- Standard libraries (os, sys, json, csv, re, math, etc.) are available\n"
    "- The result should be assigned to a variable named 'result' for downstream use\n"
    "Keep responses concise and focused on the code."
)


# ---------------------------------------------------------------------------
# Thread: send a chat message to the AI and get a response
# ---------------------------------------------------------------------------
class CodeAgentThread(QThread):
    finished = pyqtSignal(str)          # full assistant response text
    error = pyqtSignal(str)

    def __init__(self, conversation_prompt, model, api_url=None, system=CODING_AGENT_SYSTEM):
        super().__init__()
        self.prompt = conversation_prompt
        self.model = model
        self.api_url = api_url
        self.system = system

    def run(self):
        try:
            api_url = self.api_url or get_default_api_url()
            client = OllamaClient(base_url=api_url)
            response = client.generate(
                model=self.model,
                prompt=self.prompt,
                system=self.system
            )
            text = response.get('response', '')
            self.finished.emit(text.strip())
        except Exception as e:
            self.error.emit(str(e))


# ---------------------------------------------------------------------------
# Helper: extract Python code from an AI response
# ---------------------------------------------------------------------------
def _extract_code(text):
    """Return the content of the first ```python ... ``` block, or the whole text."""
    m = re.search(r'```python\s*\n(.*?)```', text, re.DOTALL)
    if m:
        return m.group(1).strip()
    m = re.search(r'```\s*\n(.*?)```', text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return text.strip()


# ---------------------------------------------------------------------------
# Helper: format a chat message for HTML display
# ---------------------------------------------------------------------------
def _format_message(role, content):
    if role == 'user':
        color = ACCENT_COLOR
        label = '🧑 You'
        bg = '#1E2A2A'
    else:
        color = '#FFB347'
        label = '🤖 Assistant'
        bg = '#1E1E2E'
    # Escape HTML
    safe = content.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    # Wrap code blocks
    safe = re.sub(r'```(\w*)\n(.*?)```', r'<pre><code>\2</code></pre>', safe, flags=re.DOTALL)
    # Inline code
    safe = re.sub(r'`([^`]+)`', r'<code>\1</code>', safe)
    # Newlines
    safe = safe.replace('\n', '<br>')
    return (
        f'<div style="margin:8px 0;padding:10px 14px;border-radius:8px;background:{bg};'
        f'border-left:3px solid {color};">'
        f'<b style="color:{color};">{label}</b>'
        f'<div style="color:{TEXT_COLOR};margin-top:4px;">{safe}</div></div>'
    )


# ---------------------------------------------------------------------------
# Main Dialog
# ---------------------------------------------------------------------------
class CodeNodeDialog(ModernDialog):
    def __init__(self, parent=None, config=None):
        super().__init__(parent, title=_("Code Node Configuration"), help_topic="code-node-dialog")
        self.setMinimumSize(780, 680)
        self.resize(820, 720)

        self.config = config or {}

        # Chat history: list of {"role": str, "content": str}
        self.chat_history = []
        # Currently selected / generated code
        self.current_code = self.config.get('code', '')
        # QProcess for running code
        self._process = None

        # ---- Build UI ----
        # Override the scroll area: we don't want a scroll for the whole dialog
        # Instead we'll build directly into content_layout.
        # First remove the scroll_area from base (keeping it for compatibility)
        # Actually ModernDialog already provides content_layout inside a scroll area.
        # For this complex layout we'll replace the scroll area approach.

        # Since ModernDialog uses scroll_area, we need to manage it.
        # We'll work within self.content_layout but we need the whole dialog
        # to scroll if needed. Let's use the scroll area properly.

        # Remove default margins for our layout
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(0)

        # Main vertical layout inside content
        main_widget = QWidget()
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(16, 12, 16, 12)
        main_layout.setSpacing(8)

        # 1. Model/Engine bar (always visible, prominent)
        model_bar = QHBoxLayout()
        model_bar.setSpacing(8)

        eng_label = QLabel(_("Engine:"))
        eng_label.setStyleSheet(f"color: {TEXT_COLOR}; font-weight: bold; font-size: 13px;")
        model_bar.addWidget(eng_label)
        self.engine_combo = QComboBox()
        self.engine_combo.addItems(["Ollama", "llama.cpp"])
        self.engine_combo.setStyleSheet(f"""
            QComboBox {{
                background-color: {MEDIUM_GREY};
                color: {TEXT_COLOR};
                border: 1px solid {LIGHT_GREY};
                border-radius: 4px;
                padding: 4px 10px;
                font-size: 13px;
                min-width: 100px;
            }}
            QComboBox::drop-down {{ border: none; width: 20px; }}
        """)
        self.engine_combo.currentTextChanged.connect(self._on_engine_changed)
        model_bar.addWidget(self.engine_combo)

        # separator
        sep = QLabel("|")
        sep.setStyleSheet(f"color: {LIGHT_GREY}; padding: 0 4px;")
        model_bar.addWidget(sep)

        self.model_label = QLabel(_("Model:"))
        self.model_label.setStyleSheet(f"color: {TEXT_COLOR}; font-weight: bold; font-size: 13px;")
        model_bar.addWidget(self.model_label)
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.setStyleSheet(f"""
            QComboBox {{
                background-color: {MEDIUM_GREY};
                color: {TEXT_COLOR};
                border: 1px solid {LIGHT_GREY};
                border-radius: 4px;
                padding: 4px 10px;
                font-size: 13px;
                min-width: 180px;
            }}
            QComboBox::drop-down {{ border: none; width: 20px; }}
        """)
        model_bar.addWidget(self.model_combo)

        self.refresh_btn = QPushButton(_("⟳"))
        self.refresh_btn.setFixedSize(30, 30)
        self.refresh_btn.setToolTip(_("Refresh model list from API"))
        self.refresh_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {MEDIUM_GREY};
                color: {TEXT_COLOR};
                border: 1px solid {LIGHT_GREY};
                border-radius: 4px;
                font-size: 18px;
            }}
            QPushButton:hover {{ background-color: {LIGHT_GREY}; }}
        """)
        self.refresh_btn.clicked.connect(self.refresh_models)
        model_bar.addWidget(self.refresh_btn)

        # llama.cpp model combo (hidden by default)
        self.llamacpp_label = QLabel(_("GGUF Model:"))
        self.llamacpp_label.setStyleSheet(f"color: {TEXT_COLOR}; font-weight: bold; font-size: 13px;")
        self.llamacpp_label.setVisible(False)
        model_bar.addWidget(self.llamacpp_label)
        self.llamacpp_combo = QComboBox()
        self.llamacpp_combo.setEditable(True)
        self.llamacpp_combo.setStyleSheet(f"""
            QComboBox {{
                background-color: {MEDIUM_GREY};
                color: {TEXT_COLOR};
                border: 1px solid {LIGHT_GREY};
                border-radius: 4px;
                padding: 4px 10px;
                font-size: 13px;
                min-width: 200px;
            }}
            QComboBox::drop-down {{ border: none; width: 20px; }}
        """)
        self.llamacpp_combo.setVisible(False)
        model_bar.addWidget(self.llamacpp_combo)

        model_bar.addStretch()
        main_layout.addLayout(model_bar)

        # 2. Splitter: Chat + Code + Terminal
        splitter = QSplitter(Qt.Vertical)
        splitter.setHandleWidth(2)
        splitter.setStyleSheet(f"""
            QSplitter::handle {{
                background-color: {LIGHT_GREY};
                height: 2px;
            }}
        """)

        # Chat area
        chat_widget = QWidget()
        chat_layout = QVBoxLayout(chat_widget)
        chat_layout.setContentsMargins(0, 0, 0, 0)
        chat_layout.setSpacing(4)
        chat_label = QLabel(_("💬 Conversation"))
        chat_label.setStyleSheet(f"color: {ACCENT_COLOR}; font-weight: bold; font-size: 12px;")
        chat_layout.addWidget(chat_label)
        self.chat_browser = QTextBrowser()
        self.chat_browser.setReadOnly(True)
        self.chat_browser.setOpenExternalLinks(False)
        self.chat_browser.setStyleSheet(f"""
            QTextBrowser {{
                background-color: {DARK_GREY};
                color: {TEXT_COLOR};
                border: 1px solid {LIGHT_GREY};
                border-radius: 6px;
                padding: 8px;
            }}
        """)
        chat_layout.addWidget(self.chat_browser)
        splitter.addWidget(chat_widget)

        # Code editor area
        code_widget = QWidget()
        code_layout = QVBoxLayout(code_widget)
        code_layout.setContentsMargins(0, 0, 0, 0)
        code_layout.setSpacing(4)
        code_header = QHBoxLayout()
        code_label = QLabel(_("📝 Code"))
        code_label.setStyleSheet(f"color: {ACCENT_COLOR}; font-weight: bold; font-size: 12px;")
        code_header.addWidget(code_label)
        code_header.addStretch()

        self.run_btn = QPushButton(_("▶ Run"))
        self.run_btn.setFixedHeight(28)
        self.run_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: #2D8F2D;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 4px 14px;
                font-weight: bold;
                font-size: 12px;
            }}
            QPushButton:hover {{ background-color: #3AA53A; }}
            QPushButton:disabled {{ background-color: #555; color: #888; }}
        """)
        self.run_btn.clicked.connect(self._run_code)
        code_header.addWidget(self.run_btn)

        self.kill_btn = QPushButton(_("⏹"))
        self.kill_btn.setFixedSize(28, 28)
        self.kill_btn.setToolTip(_("Stop execution"))
        self.kill_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: #C0392B;
                color: white;
                border: none;
                border-radius: 4px;
                font-weight: bold;
                font-size: 14px;
            }}
            QPushButton:hover {{ background-color: #E74C3C; }}
            QPushButton:disabled {{ background-color: #555; color: #888; }}
        """)
        self.kill_btn.clicked.connect(self._kill_process)
        self.kill_btn.setEnabled(False)
        code_header.addWidget(self.kill_btn)

        code_layout.addLayout(code_header)

        self.code_edit = QPlainTextEdit()
        self.code_edit.setFont(QFont("Consolas", 10))
        self.code_edit.setStyleSheet(f"""
            QPlainTextEdit {{
                background-color: #1E1E1E;
                color: #D4D4D4;
                border: 1px solid {LIGHT_GREY};
                border-radius: 6px;
            }}
        """)
        self.code_edit.setPlainText(self.current_code)
        self.code_edit.textChanged.connect(self._on_code_changed)
        code_layout.addWidget(self.code_edit)
        splitter.addWidget(code_widget)

        # Terminal area
        term_widget = QWidget()
        term_layout = QVBoxLayout(term_widget)
        term_layout.setContentsMargins(0, 0, 0, 0)
        term_layout.setSpacing(4)
        term_header = QHBoxLayout()
        term_label = QLabel(_("🖥 Terminal"))
        term_label.setStyleSheet(f"color: {ACCENT_COLOR}; font-weight: bold; font-size: 12px;")
        term_header.addWidget(term_label)
        term_header.addStretch()
        self.clear_term_btn = QPushButton(_("Clear"))
        self.clear_term_btn.setFixedHeight(24)
        self.clear_term_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {MEDIUM_GREY};
                color: {TEXT_COLOR};
                border: 1px solid {LIGHT_GREY};
                border-radius: 4px;
                padding: 2px 10px;
                font-size: 11px;
            }}
            QPushButton:hover {{ background-color: {LIGHT_GREY}; }}
        """)
        self.clear_term_btn.clicked.connect(self._clear_terminal)
        term_header.addWidget(self.clear_term_btn)
        term_layout.addLayout(term_header)

        self.terminal_browser = QTextBrowser()
        self.terminal_browser.setReadOnly(True)
        self.terminal_browser.setFont(QFont("Consolas", 10))
        self.terminal_browser.setStyleSheet(f"""
            QTextBrowser {{
                background-color: #0C0C0C;
                color: #00FF00;
                border: 1px solid {LIGHT_GREY};
                border-radius: 6px;
                padding: 8px;
            }}
        """)
        term_layout.addWidget(self.terminal_browser)
        splitter.addWidget(term_widget)

        # Set initial sizes: chat 40%, code 35%, terminal 25%
        splitter.setSizes([280, 240, 160])
        main_layout.addWidget(splitter, 1)

        # 3. Input bar
        input_layout = QHBoxLayout()
        input_layout.setSpacing(6)
        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText(_("Ask the AI to write or change code..."))
        self.input_edit.setStyleSheet(f"""
            QLineEdit {{
                background-color: {MEDIUM_GREY};
                color: {TEXT_COLOR};
                border: 1px solid {LIGHT_GREY};
                border-radius: 6px;
                padding: 8px 12px;
                font-size: 13px;
            }}
            QLineEdit:focus {{ border: 1px solid {ACCENT_COLOR}; }}
        """)
        self.input_edit.returnPressed.connect(self._send_message)
        input_layout.addWidget(self.input_edit, 1)

        self.send_btn = QPushButton(_("Send"))
        self.send_btn.setFixedHeight(36)
        self.send_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {ACCENT_COLOR};
                color: {DARK_GREY};
                border: none;
                border-radius: 6px;
                padding: 8px 20px;
                font-weight: bold;
                font-size: 13px;
            }}
            QPushButton:hover {{ background-color: #00CC99; }}
            QPushButton:disabled {{ background-color: #555; color: #888; }}
        """)
        self.send_btn.clicked.connect(self._send_message)
        input_layout.addWidget(self.send_btn)
        main_layout.addLayout(input_layout)

        # 4. Advanced settings toggle + bottom bar
        bottom_bar = QHBoxLayout()
        bottom_bar.setSpacing(8)

        self.settings_toggle_btn = QPushButton(_("⚙ Advanced"))
        self.settings_toggle_btn.setFixedHeight(28)
        self.settings_toggle_btn.setCheckable(True)
        self.settings_toggle_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {MEDIUM_GREY};
                color: {TEXT_COLOR};
                border: 1px solid {LIGHT_GREY};
                border-radius: 4px;
                padding: 4px 12px;
                font-size: 12px;
            }}
            QPushButton:hover {{ background-color: {LIGHT_GREY}; }}
            QPushButton:checked {{
                background-color: {ACCENT_COLOR};
                color: {DARK_GREY};
            }}
        """)
        self.settings_toggle_btn.toggled.connect(self._toggle_advanced)
        bottom_bar.addWidget(self.settings_toggle_btn)
        bottom_bar.addStretch()

        cancel_btn = QPushButton(_("Cancel"))
        cancel_btn.setFixedHeight(32)
        cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {MEDIUM_GREY};
                color: {TEXT_COLOR};
                border: 1px solid {LIGHT_GREY};
                border-radius: 6px;
                padding: 6px 20px;
                font-weight: bold;
            }}
            QPushButton:hover {{ background-color: {LIGHT_GREY}; }}
        """)
        cancel_btn.clicked.connect(self.reject)
        bottom_bar.addWidget(cancel_btn)

        save_btn = QPushButton(_("Save"))
        save_btn.setFixedHeight(32)
        save_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {ACCENT_COLOR};
                color: {DARK_GREY};
                border: none;
                border-radius: 6px;
                padding: 6px 20px;
                font-weight: bold;
            }}
            QPushButton:hover {{ background-color: #00CC99; }}
        """)
        save_btn.clicked.connect(self.accept)
        bottom_bar.addWidget(save_btn)
        main_layout.addLayout(bottom_bar)

        # 5. Advanced settings panel (collapsible)
        self.advanced_panel = QWidget()
        self.advanced_panel.setVisible(False)
        adv_layout = QHBoxLayout(self.advanced_panel)
        adv_layout.setContentsMargins(0, 4, 0, 0)
        adv_layout.setSpacing(12)

        # Output variable
        ov_label = QLabel(_("Output var:"))
        ov_label.setStyleSheet(f"color: {TEXT_COLOR}; font-size: 12px;")
        adv_layout.addWidget(ov_label)
        self.output_var_edit = QLineEdit(self.config.get('output_variable', 'result'))
        self.output_var_edit.setFixedWidth(80)
        self.output_var_edit.setStyleSheet(f"""
            QLineEdit {{
                background-color: {MEDIUM_GREY};
                color: {TEXT_COLOR};
                border: 1px solid {LIGHT_GREY};
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 12px;
            }}
        """)
        adv_layout.addWidget(self.output_var_edit)

        # Timeout
        to_label = QLabel(_("Timeout (s):"))
        to_label.setStyleSheet(f"color: {TEXT_COLOR}; font-size: 12px;")
        adv_layout.addWidget(to_label)
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(1, 3600)
        self.timeout_spin.setValue(self.config.get('timeout', 30))
        self.timeout_spin.setFixedWidth(60)
        self.timeout_spin.setStyleSheet(f"""
            QSpinBox {{
                background-color: {MEDIUM_GREY};
                color: {TEXT_COLOR};
                border: 1px solid {LIGHT_GREY};
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 12px;
            }}
            QSpinBox::up-button, QSpinBox::down-button {{
                background-color: {LIGHT_GREY};
                border: none;
                width: 14px;
            }}
        """)
        adv_layout.addWidget(self.timeout_spin)

        # Execute on input
        exec_label = QLabel(_("⏺ Auto-exec"))
        exec_label.setStyleSheet(f"color: {TEXT_COLOR}; font-size: 12px;")
        adv_layout.addWidget(exec_label)
        self.exec_cb = QCheckBox()
        self.exec_cb.setChecked(self.config.get('execute_on_input', True))
        self.exec_cb.setStyleSheet(f"color: {TEXT_COLOR};")
        adv_layout.addWidget(self.exec_cb)

        # Description
        desc_label = QLabel(_("Desc:"))
        desc_label.setStyleSheet(f"color: {TEXT_COLOR}; font-size: 12px;")
        adv_layout.addWidget(desc_label)
        self.desc_edit = QLineEdit(self.config.get('description', ''))
        self.desc_edit.setPlaceholderText(_("context key"))
        self.desc_edit.setStyleSheet(f"""
            QLineEdit {{
                background-color: {MEDIUM_GREY};
                color: {TEXT_COLOR};
                border: 1px solid {LIGHT_GREY};
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 12px;
            }}
        """)
        adv_layout.addWidget(self.desc_edit, 1)

        adv_layout.addStretch()
        main_layout.addWidget(self.advanced_panel)

        # Replace the scroll area content
        self.content_layout.addWidget(main_widget)

        # ---- Load initial data ----
        self._init_models()

        # Load engine selection
        use_llamacpp = self.config.get('use_llamacpp', False)
        if isinstance(use_llamacpp, str):
            use_llamacpp = use_llamacpp.lower() in ('true', '1', 'yes', 'on')
        if use_llamacpp:
            self.engine_combo.setCurrentText("llama.cpp")

    # ------------------------------------------------------------------ #
    #  Model management
    # ------------------------------------------------------------------ #
    def _init_models(self):
        """Populate model combos."""
        try:
            models = get_real_cached_models(timeout=2.0)
            if not models:
                models = get_cached_models()
            if models:
                self.model_combo.addItems(models)
            else:
                self.model_combo.addItem("llama3.2:latest")
        except Exception:
            self.model_combo.addItem("llama3.2:latest")

        current_model = self.config.get('gen_model', 'llama3.2:latest')
        self.model_combo.setCurrentText(current_model)

        # GGUF models
        gguf = get_llamacpp_models()
        if gguf:
            self.llamacpp_combo.addItems(gguf)
        else:
            self.llamacpp_combo.setPlaceholderText("No GGUF models found")
        current_llamacpp = self.config.get('llamacpp_model_path', '')
        if current_llamacpp:
            self.llamacpp_combo.setCurrentText(current_llamacpp)

    def refresh_models(self):
        try:
            api_url = get_default_api_url()
            current_model = self.model_combo.currentText()
            cache = get_model_cache()
            if cache:
                cache.refresh_models(api_url)
            models = get_real_cached_models(timeout=5.0)
            if models:
                self.model_combo.clear()
                self.model_combo.addItems(models)
                if current_model in models:
                    self.model_combo.setCurrentText(current_model)
                else:
                    self.model_combo.setCurrentText(models[0])
            else:
                QMessageBox.information(self, _("No Models Found"),
                                        _("Could not detect any downloaded models from Ollama API."))
        except Exception as e:
            QMessageBox.warning(self, _("Refresh Failed"),
                                _("Failed to refresh models: {error}").format(error=str(e)))

    def _on_engine_changed(self, engine):
        use_llamacpp = (engine == "llama.cpp")
        self.model_label.setVisible(not use_llamacpp)
        self.model_combo.setVisible(not use_llamacpp)
        self.refresh_btn.setVisible(not use_llamacpp)
        self.llamacpp_label.setVisible(use_llamacpp)
        self.llamacpp_combo.setVisible(use_llamacpp)

    # ------------------------------------------------------------------ #
    #  Conversation
    # ------------------------------------------------------------------ #
    def _add_welcome_message(self):
        welcome = (
            "Welcome to the Code Agent! 🚀\n\n"
            "Describe the Python code you'd like to write, and I'll generate it for you. "
            "You can ask me to modify, debug, or enhance the code through natural conversation. "
            "Use the **▶ Run** button to execute the current code and see output in the Terminal below."
        )
        self.chat_history.append({"role": "assistant", "content": welcome})
        self._render_chat()

    def _build_conversation_prompt(self):
        """Format chat history as a prompt string for the LLM."""
        lines = []
        for msg in self.chat_history:
            if msg['role'] == 'user':
                lines.append(f"User: {msg['content']}")
            else:
                lines.append(f"Assistant: {msg['content']}")
        # If the last message is from assistant, we still want the model to see it
        # For generating new responses, we construct: full history + "Assistant:"
        # The system prompt is sent separately.
        return "\n\n".join(lines)

    def _send_message(self):
        text = self.input_edit.text().strip()
        if not text:
            return

        self.input_edit.clear()
        self.send_btn.setEnabled(False)
        self.send_btn.setText(_("Generating..."))

        # Add user message
        self.chat_history.append({"role": "user", "content": text})
        self._render_chat()
        self._scroll_chat_to_bottom()

        # Build conversation prompt
        prompt = self._build_conversation_prompt()
        use_llamacpp = (self.engine_combo.currentText() == "llama.cpp")
        if use_llamacpp:
            model = self.llamacpp_combo.currentText()
        else:
            model = self.model_combo.currentText()

        self._gen_thread = CodeAgentThread(prompt, model)
        self._gen_thread.finished.connect(self._on_ai_response)
        self._gen_thread.error.connect(self._on_ai_error)
        self._gen_thread.start()

    def _on_ai_response(self, response_text):
        self.send_btn.setEnabled(True)
        self.send_btn.setText(_("Send"))
        self.input_edit.setFocus()

        # Add to history
        self.chat_history.append({"role": "assistant", "content": response_text})
        self._render_chat()
        self._scroll_chat_to_bottom()

        # Extract code and update editor
        code = _extract_code(response_text)
        if code:
            self.code_edit.setPlainText(code)
            self.current_code = code

    def _on_ai_error(self, error_msg):
        self.send_btn.setEnabled(True)
        self.send_btn.setText(_("Send"))
        self.chat_history.append({
            "role": "assistant",
            "content": f"⚠️ Error: {error_msg}"
        })
        self._render_chat()
        self._scroll_chat_to_bottom()

    def _render_chat(self):
        html = '<div style="font-size:13px;">'
        for msg in self.chat_history:
            html += _format_message(msg['role'], msg['content'])
        html += '</div>'
        self.chat_browser.setHtml(html)

    def _scroll_chat_to_bottom(self):
        self.chat_browser.verticalScrollBar().setValue(
            self.chat_browser.verticalScrollBar().maximum()
        )

    # ------------------------------------------------------------------ #
    #  Code execution
    # ------------------------------------------------------------------ #
    def _run_code(self):
        code = self.code_edit.toPlainText().strip()
        if not code:
            QMessageBox.information(self, _("No Code"), _("There is no code to execute."))
            return

        # Write code to a temp file and run it
        temp_file = os.path.join(os.environ.get('TEMP', os.getcwd()),
                                 f'_looper_code_node_{os.getpid()}.py')
        try:
            with open(temp_file, 'w', encoding='utf-8') as f:
                f.write(code)
        except Exception as e:
            self._append_terminal(f"Error writing temp file: {e}\n")
            return

        self._append_terminal(f"$ python {os.path.basename(temp_file)}\n")
        self.run_btn.setEnabled(False)
        self.kill_btn.setEnabled(True)

        self._process = QProcess(self)
        self._process.setProcessChannelMode(QProcess.MergedChannels)
        timeout = self.timeout_spin.value()

        # Timer for timeout
        self._run_timer = QTimer(self)
        self._run_timer.setSingleShot(True)
        self._run_timer.timeout.connect(self._on_run_timeout)

        buffer = []

        def on_ready():
            data = self._process.readAllStandardOutput().data().decode('utf-8', errors='replace')
            if data:
                buffer.append(data)

        def on_finished(exit_code, exit_status):
            self._process = None
            self.run_btn.setEnabled(True)
            self.kill_btn.setEnabled(False)
            self._run_timer.stop()
            if buffer:
                self._append_terminal(''.join(buffer))
            if exit_code == 0:
                self._append_terminal(f"\n{_('✅ Process exited with code 0')}\n")
            else:
                self._append_terminal(f"\n{_('❌ Process exited with code')} {exit_code}\n")
            # Cleanup
            try:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
            except Exception:
                pass

        self._process.readyReadStandardOutput.connect(on_ready)
        self._process.readyReadStandardError.connect(on_ready)
        self._process.finished.connect(on_finished)

        self._process.start("python", [temp_file])

        if timeout > 0:
            self._run_timer.start(timeout * 1000)

    def _on_run_timeout(self):
        if self._process and self._process.state() == QProcess.Running:
            self._append_terminal(f"\n{_('⏰ Timeout reached, terminating process...')}\n")
            self._process.kill()

    def _kill_process(self):
        if self._process and self._process.state() == QProcess.Running:
            self._append_terminal(f"\n{_('⏹ Process terminated by user')}\n")
            self._process.kill()
            self.run_btn.setEnabled(True)
            self.kill_btn.setEnabled(False)

    def _append_terminal(self, text):
        cursor = self.terminal_browser.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(text)
        self.terminal_browser.setTextCursor(cursor)
        self.terminal_browser.verticalScrollBar().setValue(
            self.terminal_browser.verticalScrollBar().maximum()
        )

    def _clear_terminal(self):
        self.terminal_browser.clear()

    def _on_code_changed(self):
        self.current_code = self.code_edit.toPlainText()

    # ------------------------------------------------------------------ #
    #  Advanced settings toggle
    # ------------------------------------------------------------------ #
    def _toggle_advanced(self, checked):
        self.advanced_panel.setVisible(checked)

    # ------------------------------------------------------------------ #
    #  Config
    # ------------------------------------------------------------------ #
    def get_config(self):
        use_llamacpp = (self.engine_combo.currentText() == "llama.cpp")
        return {
            'code': self.code_edit.toPlainText(),
            'file_path': '',  # Removed file path from simplified UI
            'execute_on_input': self.exec_cb.isChecked(),
            'output_variable': self.output_var_edit.text(),
            'timeout': self.timeout_spin.value(),
            'use_llamacpp': use_llamacpp,
            'gen_model': self.model_combo.currentText() if not use_llamacpp else 'llamacpp',
            'llamacpp_model_path': self.llamacpp_combo.currentText() if use_llamacpp else '',
            'gen_prompt': '',  # Prompt-based generation is now done via chat
            'description': self.desc_edit.text()
        }
