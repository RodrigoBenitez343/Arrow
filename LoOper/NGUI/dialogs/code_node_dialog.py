"""Modern Code Node dialog with chat-centric AI code generation.

Replaces the bloated splitter-based UI with a clean, chat-focused design
inspired by Agent Mode. Features include:
- LLM-powered code generation with continuous chat memory
- Live code editor panel
- Interactive terminal for testing
- Dependency management with auto-detection
"""

import ast
import json
import logging
import os
import re
import sys
import threading

from PyQt5.QtCore import Qt, QThread, pyqtSignal, QProcess, QTimer
from PyQt5.QtGui import QFont, QTextCursor
from PyQt5.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
    QFrame, QScrollArea, QSizePolicy, QTextEdit, QComboBox,
    QMessageBox, QApplication,
)

from .base_dialog import ModernDialog
from ..constants import (
    ACCENT_COLOR, BLOCK_COLOR, BLOCK_HOVER, CODE_NODE_COLOR,
    DARK_GREY, LIGHT_GREY, MEDIUM_GREY, TEXT_COLOR, SIDE_PANEL_BG, BAR_BG,
)
from ..i18n import _
from .agentic_loop_controller import AgenticLoopController, LoopPhase, IterationRecord, _resolve_python

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# AI imports
# ---------------------------------------------------------------------------
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
try:
    from AI.consult import OllamaClient
    from AI.model_cache import get_cached_models, get_real_cached_models, get_model_cache
except ImportError:
    OllamaClient = None
    get_cached_models = lambda: []
    get_real_cached_models = lambda timeout=5.0: []
    get_model_cache = lambda: None

try:
    from .llm_dialogs import get_llamacpp_models
except ImportError:
    get_llamacpp_models = lambda: []

from ..nodes_resources.base_node import get_default_api_url


# ---------------------------------------------------------------------------
# System prompt for the coding agent
# ---------------------------------------------------------------------------
CODING_AGENT_SYSTEM = (
    "You are an expert Python developer integrated into a workflow automation tool. "
    "The user will ask you to write, modify, or debug Python code.\n\n"
    "RULES:\n"
    "1. ALWAYS respond with the final code inside a Python fenced code block: ```python\\n...\\n```\n"
    "2. You may include explanations OUTSIDE the code block.\n"
    "3. The code runs in a local environment where:\n"
    "   - 'input_data' and 'args' variables are available from the previous node\n"
    "   - Standard libraries (os, sys, json, csv, re, math, etc.) are available\n"
    "4. Assign the result to a variable named 'result' for downstream use.\n"
    "5. Keep responses concise and focused on the code.\n"
    "6. If asked to fix errors from a previous run, analyze the error and provide corrected code."
)


# ---------------------------------------------------------------------------
# Thread: send a chat message to the AI and get a response
# ---------------------------------------------------------------------------
class CodeAgentThread(QThread):
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, conversation_prompt, model, api_url=None,
                 system=CODING_AGENT_SYSTEM, use_llamacpp=False):
        super().__init__()
        self.prompt = conversation_prompt
        self.model = model
        self.api_url = api_url
        self.system = system
        self.use_llamacpp = use_llamacpp

    def run(self):
        try:
            api_url = self.api_url or get_default_api_url()
            api_url = api_url.rstrip('/')

            if self.use_llamacpp:
                import requests
                # Support both messages format (list) and flat prompt format (str)
                messages = (
                    self.prompt
                    if isinstance(self.prompt, list)
                    else [{"role": "user", "content": self.prompt}]
                )
                payload = {
                    "model": self.model,
                    "messages": messages,
                    "system": self.system or "",
                    "temperature": 0.3,
                    "max_tokens": 4096,
                }
                resp = requests.post(
                    f"{api_url}/llamacpp/chat",
                    json=payload,
                    timeout=None,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    text = data.get('response', '')
                    self.finished.emit(text.strip())
                else:
                    self.error.emit(
                        f"llama.cpp error ({resp.status_code}): {resp.text}"
                    )
            else:
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
# Helpers
# ---------------------------------------------------------------------------
def _extract_code(text):
    """Return the content of the first ```python ... ``` block, or the whole text."""
    m = re.search(r'```python\s*\n(.*?)```', text, re.DOTALL)
    if m:
        return m.group(1).strip()
    m = re.search(r'```\s*\n(.*?)```', text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return ''


def _format_message(role, content):
    """Format a chat message as HTML for the conversation display."""
    if role == 'user':
        color = ACCENT_COLOR
        label = 'You'
        bg = '#1E2A2A'
        border_side = ACCENT_COLOR
    elif role == 'execution':
        color = '#888888'
        label = 'Execution'
        bg = '#1A1A2E'
        border_side = '#888888'
    else:
        color = '#FFB347'
        label = 'Assistant'
        bg = '#1E1E2E'
        border_side = '#FFB347'

    safe = content.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    safe = re.sub(r'```(\w*)\n(.*?)```', r'<pre><code>\2</code></pre>', safe, flags=re.DOTALL)
    safe = re.sub(r'`([^`]+)`', r'<code>\1</code>', safe)
    safe = safe.replace('\n', '<br>')

    return (
        f'<div style="margin:6px 0;padding:10px 14px;border-radius:8px;background:{bg};'
        f'border-left:3px solid {border_side};">'
        f'<b style="color:{color};font-size:11px;">{label}</b>'
        f'<div style="color:{TEXT_COLOR};margin-top:4px;font-size:13px;">{safe}</div></div>'
    )


def _auto_detect_dependencies(code):
    """Scan Python code for imports and suggest pip package names."""
    known_stdlib = {
        'os', 'sys', 'json', 'csv', 're', 'math', 'random', 'datetime',
        'collections', 'itertools', 'functools', 'pathlib', 'shutil',
        'subprocess', 'tempfile', 'time', 'typing', 'uuid', 'io', 'base64',
        'hashlib', 'hmac', 'binascii', 'textwrap', 'string', 'struct',
        'decimal', 'fractions', 'statistics', 'logging', 'traceback',
        'pprint', 'abc', 'argparse', 'ast', 'asyncio', 'concurrent',
        'configparser', 'contextlib', 'copy', 'cProfile', 'dataclasses',
        'difflib', 'dis', 'email', 'enum', 'filecmp', 'fileinput',
        'fnmatch', 'fractions', 'getopt', 'getpass', 'gettext', 'glob',
        'gzip', 'heapq', 'html', 'http', 'importlib', 'inspect',
        'io', 'ipaddress', 'json', 'keyword', 'linecache', 'locale',
        'lzma', 'mailbox', 'mailcap', 'marshal', 'mmap', 'modulefinder',
        'multiprocessing', 'netrc', 'nis', 'nntplib', 'numbers',
        'operator', 'optparse', 'os', 'ossaudiodev', 'pickle',
        'pickletools', 'pipes', 'pkgutil', 'platform', 'plistlib',
        'poplib', 'posix', 'pprint', 'profile', 'pstats', 'pty',
        'pwd', 'py_compile', 'pyclbr', 'pydoc', 'queue', 'quopri',
        'random', 're', 'readline', 'reprlib', 'resource', 'rlcompleter',
        'runpy', 'sched', 'secrets', 'select', 'selectors', 'shelve',
        'shlex', 'shutil', 'signal', 'site', 'smtpd', 'smtplib',
        'sndhdr', 'socket', 'socketserver', 'sqlite3', 'ssl', 'stat',
        'statistics', 'string', 'stringprep', 'struct', 'subprocess',
        'sunau', 'symtable', 'sys', 'sysconfig', 'syslog', 'tabnanny',
        'tarfile', 'telnetlib', 'tempfile', 'termios', 'test',
        'textwrap', 'threading', 'time', 'timeit', 'tkinter', 'token',
        'tokenize', 'trace', 'traceback', 'tracemalloc', 'tty',
        'turtle', 'turtledemo', 'types', 'typing', 'unicodedata',
        'unittest', 'urllib', 'uu', 'uuid', 'venv', 'warnings',
        'wave', 'weakref', 'webbrowser', 'winreg', 'winsound',
        'wsgiref', 'xdrlib', 'xml', 'xmlrpc', 'zipapp', 'zipfile',
        'zipimport', 'zlib', 'zoneinfo',
    }
    known_pip = {
        'requests': 'requests',
        'numpy': 'numpy',
        'pandas': 'pandas',
        'matplotlib': 'matplotlib',
        'PIL': 'pillow',
        'Pillow': 'pillow',
        'bs4': 'beautifulsoup4',
        'selenium': 'selenium',
        'flask': 'flask',
        'django': 'django',
        'scipy': 'scipy',
        'sklearn': 'scikit-learn',
        'tensorflow': 'tensorflow',
        'torch': 'torch',
        'cv2': 'opencv-python',
        'pyautogui': 'pyautogui',
        'pynput': 'pynput',
        'psutil': 'psutil',
        'pyyaml': 'pyyaml',
        'yaml': 'pyyaml',
        'dotenv': 'python-dotenv',
        'tqdm': 'tqdm',
        'click': 'click',
        'rich': 'rich',
        'jinja2': 'jinja2',
        'aiohttp': 'aiohttp',
        'fastapi': 'fastapi',
        'uvicorn': 'uvicorn',
        'pydantic': 'pydantic',
        'sqlalchemy': 'sqlalchemy',
        'redis': 'redis',
        'pymongo': 'pymongo',
        'paramiko': 'paramiko',
        'dnspython': 'dnspython',
    }

    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []

    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split('.')[0]
                if top not in known_stdlib:
                    pkg = known_pip.get(top, top)
                    found.add(pkg)
        elif isinstance(node, ast.ImportFrom):
            top = node.module.split('.')[0] if node.module else ''
            if top and top not in known_stdlib:
                pkg = known_pip.get(top, top)
                found.add(pkg)
    return sorted(found)


# ---------------------------------------------------------------------------
# Main Dialog
# ---------------------------------------------------------------------------
class CodeNodeDialog(ModernDialog):
    """Modern chat-centric Code Node configuration dialog."""

    _refresh_complete_signal = pyqtSignal(list, str)

    def __init__(self, parent=None, config=None):
        super().__init__(parent, title=_("Code Node"), help_topic="code-node-dialog",
                         show_help_button=True)
        self.setMinimumSize(780, 680)
        self.resize(880, 760)

        self._refresh_complete_signal.connect(self._on_refresh_complete)

        self.config = config or {}

        # Chat memory: persistent conversation for continuous context
        self._memory = self.config.get('memory_context', [])

        # Current code from AI or manual edit
        self.current_code = self.config.get('code', '')

        # Dependencies
        self._dependencies = list(self.config.get('dependencies', []))
        # Custom IO port declarations
        self._input_vars = list(self.config.get('input_vars', []))
        self._output_vars = list(self.config.get('output_vars', []))

        # Execution
        self._process = None
        self._run_timer = None

        # Active panel: 'code', 'terminal', 'dependencies'
        self._active_panel = 'code'

        # Agentic loop
        self._agentic_controller = None
        self._agentic_enabled = True
        self._max_iterations = 5

        # Engine selection
        self._use_llamacpp = self.config.get('use_llamacpp', False)

        # Build UI
        self._build_ui()

        # Load data
        self._init_models()

        # If we have existing memory, show it; otherwise show welcome
        if not self._memory:
            self._add_welcome_message()

    # ------------------------------------------------------------------ #
    #  UI Construction
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        """Build the modern chat-centric UI."""
        # We work within ModernDialog's content_layout (inside scroll area).
        # For this dialog we replace the scroll area approach with direct layout.
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(0)

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # ── Top toolbar ──
        top_bar = self._build_top_bar()
        root_layout.addWidget(top_bar)

        # ── Chat area (main content) ──
        chat_frame = self._build_chat_area()
        root_layout.addWidget(chat_frame, 1)

        # ── Bottom panel toggle + content ──
        panel_section = self._build_panel_section()
        root_layout.addWidget(panel_section, 0)

        # ── Input bar ──
        input_bar = self._build_input_bar()
        root_layout.addWidget(input_bar, 0)

        self.content_layout.addWidget(root)

    def _build_top_bar(self):
        """Top toolbar: title, model selector, save button."""
        bar = QFrame()
        bar.setObjectName("codeNodeTopBar")
        bar.setStyleSheet(
            f"QFrame#codeNodeTopBar {{ background-color: {BAR_BG}; "
            f"border-bottom: 1px solid rgba(255,255,255,0.06); }}"
        )
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 6, 16, 6)
        layout.setSpacing(10)

        # Engine selector
        engine_label = QLabel(_("Engine:"))
        engine_label.setStyleSheet(f"color: {TEXT_COLOR}; font-size: 12px; font-weight: 600;")
        layout.addWidget(engine_label)

        self.engine_combo = QComboBox()
        self.engine_combo.addItems(["Ollama", "llama.cpp"])
        self.engine_combo.setFixedHeight(28)
        self.engine_combo.setFixedWidth(90)
        self.engine_combo.setStyleSheet(
            f"QComboBox {{ background-color: {SIDE_PANEL_BG}; color: {TEXT_COLOR}; "
            f"border: 1px solid rgba(255,255,255,0.08); border-radius: 6px; "
            f"padding: 2px 8px; font-size: 12px; }}"
            f"QComboBox::drop-down {{ border: none; width: 20px; }}"
            f"QComboBox QAbstractItemView {{ background-color: {SIDE_PANEL_BG}; "
            f"color: {TEXT_COLOR}; selection-background-color: {ACCENT_COLOR}; "
            f"selection-color: #000; }}"
        )
        self.engine_combo.currentTextChanged.connect(self._on_engine_changed)
        layout.addWidget(self.engine_combo)

        # Ollama model selector
        self.model_label = QLabel(_("Model:"))
        self.model_label.setStyleSheet(f"color: {TEXT_COLOR}; font-size: 12px; font-weight: 600;")
        layout.addWidget(self.model_label)

        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.setFixedHeight(28)
        self.model_combo.setMinimumWidth(160)
        self.model_combo.setStyleSheet(
            f"QComboBox {{ background-color: {SIDE_PANEL_BG}; color: {TEXT_COLOR}; "
            f"border: 1px solid rgba(255,255,255,0.08); border-radius: 6px; "
            f"padding: 2px 10px; font-size: 12px; }}"
            f"QComboBox::drop-down {{ border: none; width: 20px; }}"
            f"QComboBox QAbstractItemView {{ background-color: {SIDE_PANEL_BG}; "
            f"color: {TEXT_COLOR}; selection-background-color: {ACCENT_COLOR}; "
            f"selection-color: #000; }}"
        )
        layout.addWidget(self.model_combo)

        # Refresh button (Ollama only)
        self.refresh_btn = QPushButton(_("R"))
        self.refresh_btn.setFixedSize(26, 26)
        self.refresh_btn.setToolTip(_("Refresh Ollama models"))
        self.refresh_btn.setCursor(Qt.PointingHandCursor)
        self.refresh_btn.setStyleSheet(
            f"QPushButton {{ background-color: transparent; color: {LIGHT_GREY}; "
            f"border: 1px solid rgba(255,255,255,0.10); border-radius: 6px; "
            f"font-size: 11px; font-weight: 600; }}"
            f"QPushButton:hover {{ color: {ACCENT_COLOR}; border-color: {ACCENT_COLOR}; }}"
        )
        self.refresh_btn.clicked.connect(self.refresh_models)
        layout.addWidget(self.refresh_btn)

        # llama.cpp GGUF selector (hidden by default)
        self.llamacpp_label = QLabel(_("GGUF:"))
        self.llamacpp_label.setStyleSheet(f"color: {TEXT_COLOR}; font-size: 12px; font-weight: 600;")
        self.llamacpp_label.setVisible(False)
        layout.addWidget(self.llamacpp_label)

        self.llamacpp_combo = QComboBox()
        self.llamacpp_combo.setEditable(True)
        self.llamacpp_combo.setFixedHeight(28)
        self.llamacpp_combo.setMinimumWidth(200)
        self.llamacpp_combo.setStyleSheet(
            f"QComboBox {{ background-color: {SIDE_PANEL_BG}; color: {TEXT_COLOR}; "
            f"border: 1px solid rgba(255,255,255,0.08); border-radius: 6px; "
            f"padding: 2px 10px; font-size: 12px; }}"
            f"QComboBox::drop-down {{ border: none; width: 20px; }}"
            f"QComboBox QAbstractItemView {{ background-color: {SIDE_PANEL_BG}; "
            f"color: {TEXT_COLOR}; selection-background-color: {ACCENT_COLOR}; "
            f"selection-color: #000; }}"
        )
        self.llamacpp_combo.setVisible(False)
        layout.addWidget(self.llamacpp_combo)

        # Agentic iteration status
        self._iteration_status_label = QLabel("")
        self._iteration_status_label.setStyleSheet(
            f"color: {ACCENT_COLOR}; font-size: 11px; font-weight: 600;"
        )
        layout.addWidget(self._iteration_status_label)

        layout.addStretch(1)

        # Description field
        desc_label = QLabel(_("Desc:"))
        desc_label.setStyleSheet(f"color: rgba(255,255,255,0.4); font-size: 11px;")
        layout.addWidget(desc_label)

        self.desc_edit = QLineEdit(self.config.get('description', ''))
        self.desc_edit.setPlaceholderText(_("context key"))
        self.desc_edit.setFixedWidth(140)
        self.desc_edit.setFixedHeight(26)
        self.desc_edit.setStyleSheet(
            f"QLineEdit {{ background-color: {SIDE_PANEL_BG}; color: {TEXT_COLOR}; "
            f"border: 1px solid rgba(255,255,255,0.08); border-radius: 6px; "
            f"padding: 2px 8px; font-size: 11px; }}"
            f"QLineEdit:focus {{ border-color: {ACCENT_COLOR}; }}"
        )
        layout.addWidget(self.desc_edit)

        # Agentic toggle
        self._agentic_toggle_btn = self._make_panel_toggle("Auto", '#F1C40F', True)
        self._agentic_toggle_btn.setToolTip(_("Toggle agentic code refinement loop"))
        self._agentic_toggle_btn.clicked.connect(self._toggle_agentic)
        layout.addWidget(self._agentic_toggle_btn)

        # Save button
        save_btn = QPushButton(_("Save"))
        save_btn.setFixedHeight(28)
        save_btn.setCursor(Qt.PointingHandCursor)
        save_btn.setStyleSheet(
            f"QPushButton {{ background-color: {ACCENT_COLOR}; color: #0D1117; "
            f"border: 0px; border-radius: 6px; padding: 0px 16px; "
            f"font-size: 12px; font-weight: 700; }}"
            f"QPushButton:hover {{ background-color: #00f0c6; }}"
        )
        save_btn.clicked.connect(self.accept)
        layout.addWidget(save_btn)

        return bar

    def _build_chat_area(self):
        """Chat conversation area with scroll."""
        chat_frame = QFrame()
        chat_frame.setObjectName("codeChatFrame")
        chat_frame.setStyleSheet(
            f"QFrame#codeChatFrame {{ background-color: {DARK_GREY}; }}"
        )
        chat_layout = QVBoxLayout(chat_frame)
        chat_layout.setContentsMargins(16, 8, 16, 8)
        chat_layout.setSpacing(6)

        self._chat_scroll = QScrollArea(chat_frame)
        self._chat_scroll.setWidgetResizable(True)
        self._chat_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._chat_scroll.setStyleSheet(
            "QScrollArea { background-color: transparent; border: 0px; }"
            f"QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0px; border: none; }}"
            f"QScrollBar::handle:vertical {{ background: rgba(255,255,255,0.10); min-height: 24px; border-radius: 4px; }}"
            f"QScrollBar::handle:vertical:hover {{ background: rgba(255,255,255,0.18); }}"
            f"QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}"
        )

        self._chat_container = QWidget()
        self._chat_container.setObjectName("codeChatContainer")
        self._chat_container.setStyleSheet(
            f"QWidget#codeChatContainer {{ background-color: transparent; }}"
        )
        self._chat_container_layout = QVBoxLayout(self._chat_container)
        self._chat_container_layout.setContentsMargins(0, 0, 0, 0)
        self._chat_container_layout.setSpacing(6)
        self._chat_container_layout.setAlignment(Qt.AlignTop)

        self._chat_scroll.setWidget(self._chat_container)
        chat_layout.addWidget(self._chat_scroll, 1)

        return chat_frame

    def _build_panel_section(self):
        """Bottom panel with pill toggles: Code / Terminal / Dependencies."""
        container = QWidget()
        container.setObjectName("codePanelContainer")
        container.setStyleSheet(
            f"QWidget#codePanelContainer {{ background-color: {SIDE_PANEL_BG}; "
            f"border-top: 1px solid rgba(255,255,255,0.06); }}"
        )
        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(6)

        # Pill toggle buttons
        toggle_row = QHBoxLayout()
        toggle_row.setSpacing(4)

        self._code_panel_btn = self._make_panel_toggle(_("Code"), '#F1C40F', True)
        self._code_panel_btn.clicked.connect(lambda: self._switch_panel('code'))
        toggle_row.addWidget(self._code_panel_btn)

        self._term_panel_btn = self._make_panel_toggle(_("Terminal"), '#00FF00', False)
        self._term_panel_btn.clicked.connect(lambda: self._switch_panel('terminal'))
        toggle_row.addWidget(self._term_panel_btn)

        self._deps_panel_btn = self._make_panel_toggle(_("Dependencies"), '#60A5FA', False)
        self._deps_panel_btn.clicked.connect(lambda: self._switch_panel('dependencies'))
        toggle_row.addWidget(self._deps_panel_btn)

        self._io_panel_btn = self._make_panel_toggle(_("IO"), '#A78BFA', False)
        self._io_panel_btn.clicked.connect(lambda: self._switch_panel('io'))
        toggle_row.addWidget(self._io_panel_btn)

        toggle_row.addStretch(1)
        layout.addLayout(toggle_row)

        # Panel content stack
        self._panel_stack = QWidget()
        self._panel_stack.setFixedHeight(180)
        stack_layout = QVBoxLayout(self._panel_stack)
        stack_layout.setContentsMargins(0, 0, 0, 0)
        stack_layout.setSpacing(0)

        # -- Code panel --
        self._code_panel = QWidget()
        code_panel_layout = QVBoxLayout(self._code_panel)
        code_panel_layout.setContentsMargins(0, 0, 0, 0)
        code_panel_layout.setSpacing(2)

        code_header = QHBoxLayout()
        code_header.setContentsMargins(0, 0, 0, 0)
        code_label = QLabel(_("Python Code"))
        code_label.setStyleSheet(f"color: rgba(255,255,255,0.5); font-size: 11px; font-weight: 600;")
        code_header.addWidget(code_label)
        code_header.addStretch(1)

        self._run_code_btn = self._make_small_button(_("Run"), '#22c55e')
        self._run_code_btn.clicked.connect(self._run_code)
        code_header.addWidget(self._run_code_btn)

        self._syntax_btn = self._make_small_button(_("Check \u2713"), '#60A5FA')
        self._syntax_btn.clicked.connect(self._syntax_check)
        code_header.addWidget(self._syntax_btn)

        self._kill_btn = self._make_small_button(_("Stop"), '#ef4444')
        self._kill_btn.setEnabled(False)
        self._kill_btn.clicked.connect(self._kill_process)
        code_header.addWidget(self._kill_btn)

        code_panel_layout.addLayout(code_header)

        self._code_edit = QPlainTextEdit()
        self._code_edit.setFont(QFont("Consolas", 10))
        self._code_edit.setStyleSheet(
            f"QPlainTextEdit {{ background-color: #1E1E1E; color: #D4D4D4; "
            f"border: 1px solid rgba(255,255,255,0.06); border-radius: 6px; }}"
        )
        self._code_edit.setPlainText(self.current_code)
        self._code_edit.textChanged.connect(self._on_code_changed)
        code_panel_layout.addWidget(self._code_edit, 1)
        stack_layout.addWidget(self._code_panel)

        # -- Terminal panel --
        self._term_panel = QWidget()
        self._term_panel.hide()
        term_panel_layout = QVBoxLayout(self._term_panel)
        term_panel_layout.setContentsMargins(0, 0, 0, 0)
        term_panel_layout.setSpacing(2)

        term_header = QHBoxLayout()
        term_header.setContentsMargins(0, 0, 0, 0)
        term_label = QLabel(_("Terminal Output"))
        term_label.setStyleSheet(f"color: rgba(255,255,255,0.5); font-size: 11px; font-weight: 600;")
        term_header.addWidget(term_label)
        term_header.addStretch(1)

        clear_term_btn = self._make_small_button(_("Clear"), '#888888')
        clear_term_btn.clicked.connect(self._clear_terminal)
        term_header.addWidget(clear_term_btn)

        run_tests_btn = self._make_small_button(_("Run All Tests"), '#22c55e')
        run_tests_btn.clicked.connect(self._run_all_tests)
        term_header.addWidget(run_tests_btn)

        term_panel_layout.addLayout(term_header)

        self._term_output = QPlainTextEdit()
        self._term_output.setReadOnly(True)
        self._term_output.setFont(QFont("Consolas", 10))
        self._term_output.setStyleSheet(
            f"QPlainTextEdit {{ background-color: #0C0C0C; color: #00FF00; "
            f"border: 1px solid rgba(255,255,255,0.06); border-radius: 6px; }}"
        )
        term_panel_layout.addWidget(self._term_output, 1)

        # Terminal command input
        cmd_row = QHBoxLayout()
        cmd_row.setContentsMargins(0, 4, 0, 0)
        cmd_row.setSpacing(4)
        self._term_input = QLineEdit()
        self._term_input.setPlaceholderText(_("Type a shell command or Python expression..."))
        self._term_input.setStyleSheet(
            f"QLineEdit {{ background-color: #0C0C0C; color: #00FF00; "
            f"border: 1px solid rgba(255,255,255,0.08); border-radius: 6px; "
            f"padding: 4px 8px; font-family: Consolas; font-size: 11px; }}"
            f"QLineEdit:focus {{ border-color: {ACCENT_COLOR}; }}"
        )
        self._term_input.returnPressed.connect(self._send_terminal_command)
        cmd_row.addWidget(self._term_input, 1)

        send_cmd_btn = QPushButton(_("$"))
        send_cmd_btn.setFixedSize(26, 26)
        send_cmd_btn.setToolTip(_("Send command"))
        send_cmd_btn.setStyleSheet(
            f"QPushButton {{ background-color: #0C0C0C; color: #00FF00; "
            f"border: 1px solid rgba(255,255,255,0.08); border-radius: 6px; "
            f"font-size: 12px; font-weight: 600; }}"
            f"QPushButton:hover {{ background-color: #1A1A1A; }}"
        )
        send_cmd_btn.clicked.connect(self._send_terminal_command)
        cmd_row.addWidget(send_cmd_btn)

        term_panel_layout.addLayout(cmd_row)

        # Test arguments for agentic loop
        test_args_layout = QHBoxLayout()
        test_args_layout.setContentsMargins(0, 4, 0, 0)
        test_args_layout.setSpacing(4)
        test_args_label = QLabel(_("Test Args:"))
        test_args_label.setStyleSheet(f"color: rgba(255,255,255,0.5); font-size: 10px; font-weight: 600;")
        test_args_layout.addWidget(test_args_label)
        self._test_args_edit = QPlainTextEdit()
        self._test_args_edit.setPlaceholderText(
            _('Test args (JSON array), e.g. [{"input": "test1"}, {"input": "test2"}]')
        )
        self._test_args_edit.setMaximumHeight(60)
        self._test_args_edit.setStyleSheet(
            f"QPlainTextEdit {{ background-color: #0C0C0C; color: #00FF00; "
            f"border: 1px solid rgba(255,255,255,0.08); border-radius: 6px; "
            f"padding: 4px 8px; font-family: Consolas; font-size: 10px; }}"
        )
        test_args_layout.addWidget(self._test_args_edit, 1)
        term_panel_layout.addLayout(test_args_layout)

        stack_layout.addWidget(self._term_panel)

        # -- Dependencies panel --
        self._deps_panel = QWidget()
        self._deps_panel.hide()
        deps_panel_layout = QVBoxLayout(self._deps_panel)
        deps_panel_layout.setContentsMargins(0, 0, 0, 0)
        deps_panel_layout.setSpacing(4)

        deps_header = QHBoxLayout()
        deps_header.setContentsMargins(0, 0, 0, 0)
        deps_label = QLabel(_("Extra Packages"))
        deps_label.setStyleSheet(f"color: rgba(255,255,255,0.5); font-size: 11px; font-weight: 600;")
        deps_header.addWidget(deps_label)
        deps_header.addStretch(1)

        auto_detect_btn = self._make_small_button(_("Auto-detect"), '#60A5FA')
        auto_detect_btn.clicked.connect(self._auto_detect_deps)
        deps_header.addWidget(auto_detect_btn)

        install_btn = self._make_small_button(_("Install All"), '#22c55e')
        install_btn.clicked.connect(self._install_dependencies)
        deps_header.addWidget(install_btn)

        deps_panel_layout.addLayout(deps_header)

        content_row = QHBoxLayout()
        content_row.setContentsMargins(0, 0, 0, 0)
        content_row.setSpacing(6)

        self._deps_list = QListWidget()
        self._deps_list.setStyleSheet(
            f"QListWidget {{ background-color: #1E1E1E; color: {TEXT_COLOR}; "
            f"border: 1px solid rgba(255,255,255,0.06); border-radius: 6px; "
            f"font-size: 12px; }}"
            f"QListWidget::item {{ padding: 4px 8px; }}"
            f"QListWidget::item:selected {{ background-color: {ACCENT_COLOR}; color: #000; }}"
        )
        content_row.addWidget(self._deps_list, 1)

        deps_controls = QVBoxLayout()
        deps_controls.setSpacing(4)
        self._dep_input = QLineEdit()
        self._dep_input.setPlaceholderText(_("package name"))
        self._dep_input.setStyleSheet(
            f"QLineEdit {{ background-color: {MEDIUM_GREY}; color: {TEXT_COLOR}; "
            f"border: 1px solid rgba(255,255,255,0.08); border-radius: 6px; "
            f"padding: 4px 8px; font-size: 11px; }}"
        )
        deps_controls.addWidget(self._dep_input)

        add_dep_btn = QPushButton(_("Add"))
        add_dep_btn.setFixedHeight(26)
        add_dep_btn.setStyleSheet(
            f"QPushButton {{ background-color: {ACCENT_COLOR}; color: #0D1117; "
            f"border: 0px; border-radius: 6px; font-size: 11px; font-weight: 600; }}"
        )
        add_dep_btn.clicked.connect(self._add_dependency)
        deps_controls.addWidget(add_dep_btn)

        remove_dep_btn = QPushButton(_("Remove"))
        remove_dep_btn.setFixedHeight(26)
        remove_dep_btn.setStyleSheet(
            f"QPushButton {{ background-color: transparent; color: #ef4444; "
            f"border: 1px solid #ef4444; border-radius: 6px; font-size: 11px; font-weight: 600; }}"
        )
        remove_dep_btn.clicked.connect(self._remove_dependency)
        deps_controls.addWidget(remove_dep_btn)

        deps_controls.addStretch(1)
        content_row.addLayout(deps_controls)

        deps_panel_layout.addLayout(content_row, 1)
        stack_layout.addWidget(self._deps_panel)

        # -- IO Variables panel --
        self._io_panel = QWidget()
        self._io_panel.hide()
        io_panel_layout = QVBoxLayout(self._io_panel)
        io_panel_layout.setContentsMargins(0, 0, 0, 0)
        io_panel_layout.setSpacing(6)

        # ── Input Variables section ──
        io_input_header = QHBoxLayout()
        io_input_header.setContentsMargins(0, 0, 0, 0)
        io_input_label = QLabel(_("Input Variables (appear as input ports on the node)"))
        io_input_label.setStyleSheet(f"color: rgba(255,255,255,0.5); font-size: 11px; font-weight: 600;")
        io_input_header.addWidget(io_input_label)
        io_input_header.addStretch(1)
        io_panel_layout.addLayout(io_input_header)

        self._io_input_container = QWidget()
        self._io_input_container.setStyleSheet(
            f"background-color: #1E1E1E; border: 1px solid rgba(255,255,255,0.06); border-radius: 6px;"
        )
        self._io_input_layout = QVBoxLayout(self._io_input_container)
        self._io_input_layout.setContentsMargins(8, 6, 8, 6)
        self._io_input_layout.setSpacing(4)
        self._io_input_layout.setAlignment(Qt.AlignTop)
        io_panel_layout.addWidget(self._io_input_container)

        add_input_var_btn = QPushButton(_("+ Add Input Variable"))
        add_input_var_btn.setFixedHeight(24)
        add_input_var_btn.setCursor(Qt.PointingHandCursor)
        add_input_var_btn.setStyleSheet(
            f"QPushButton {{ background-color: transparent; color: {ACCENT_COLOR}; "
            f"border: 1px dashed {ACCENT_COLOR}; border-radius: 4px; "
            f"font-size: 10px; font-weight: 600; padding: 0px 10px; }}"
            f"QPushButton:hover {{ background-color: rgba(0,224,184,0.08); }}"
        )
        add_input_var_btn.clicked.connect(lambda: self._add_input_var_row())
        io_panel_layout.addWidget(add_input_var_btn)

        # ── Output Variables section ──
        io_panel_layout.addSpacing(6)
        io_output_header = QHBoxLayout()
        io_output_header.setContentsMargins(0, 0, 0, 0)
        io_output_label = QLabel(_("Output Variables (appear as output ports on the node)"))
        io_output_label.setStyleSheet(f"color: rgba(255,255,255,0.5); font-size: 11px; font-weight: 600;")
        io_output_header.addWidget(io_output_label)
        io_output_header.addStretch(1)
        io_panel_layout.addLayout(io_output_header)

        self._io_output_container = QWidget()
        self._io_output_container.setStyleSheet(
            f"background-color: #1E1E1E; border: 1px solid rgba(255,255,255,0.06); border-radius: 6px;"
        )
        self._io_output_layout = QVBoxLayout(self._io_output_container)
        self._io_output_layout.setContentsMargins(8, 6, 8, 6)
        self._io_output_layout.setSpacing(4)
        self._io_output_layout.setAlignment(Qt.AlignTop)
        io_panel_layout.addWidget(self._io_output_container)

        add_output_var_btn = QPushButton(_("+ Add Output Variable"))
        add_output_var_btn.setFixedHeight(24)
        add_output_var_btn.setCursor(Qt.PointingHandCursor)
        add_output_var_btn.setStyleSheet(
            f"QPushButton {{ background-color: transparent; color: {ACCENT_COLOR}; "
            f"border: 1px dashed {ACCENT_COLOR}; border-radius: 4px; "
            f"font-size: 10px; font-weight: 600; padding: 0px 10px; }}"
            f"QPushButton:hover {{ background-color: rgba(0,224,184,0.08); }}"
        )
        add_output_var_btn.clicked.connect(lambda: self._add_output_var_row())
        io_panel_layout.addWidget(add_output_var_btn)

        io_panel_layout.addStretch(1)
        stack_layout.addWidget(self._io_panel)

        layout.addWidget(self._panel_stack)

        # Populate deps list
        for pkg in self._dependencies:
            self._deps_list.addItem(pkg)

        # Populate existing IO var rows
        for iv in self._input_vars:
            self._add_input_var_row(iv.get('name', ''), iv.get('type', 'string'))
        for ov in self._output_vars:
            self._add_output_var_row(ov.get('name', ''), ov.get('type', 'string'))

        return container

    def _build_input_bar(self):
        """Bottom input bar with text input, send, and run code."""
        input_frame = QFrame()
        input_frame.setObjectName("codeInputFrame")
        input_frame.setStyleSheet(
            f"QFrame#codeInputFrame {{ background-color: {BAR_BG}; "
            f"border-top: 1px solid rgba(255,255,255,0.06); }}"
        )
        layout = QHBoxLayout(input_frame)
        layout.setContentsMargins(16, 8, 16, 10)
        layout.setSpacing(8)

        self._input_field = QTextEdit()
        self._input_field.setPlaceholderText(_("Ask the AI to write or fix code..."))
        self._input_field.setMinimumHeight(40)
        self._input_field.setMaximumHeight(100)
        self._input_field.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._input_field.setStyleSheet(
            f"QTextEdit {{ background-color: {SIDE_PANEL_BG}; color: {TEXT_COLOR}; "
            f"border: 1px solid rgba(255,255,255,0.08); border-radius: 8px; "
            f"padding: 8px 12px; font-size: 13px; }}"
            f"QTextEdit:focus {{ border-color: {ACCENT_COLOR}; }}"
        )
        self._input_field.installEventFilter(self)
        layout.addWidget(self._input_field, 1)

        self._send_btn = QPushButton(_("Send"))
        self._send_btn.setFixedHeight(36)
        self._send_btn.setCursor(Qt.PointingHandCursor)
        self._send_btn.setStyleSheet(
            f"QPushButton {{ background-color: {ACCENT_COLOR}; color: #0D1117; "
            f"border: 0px; border-radius: 8px; padding: 8px 18px; "
            f"font-size: 13px; font-weight: 700; }}"
            f"QPushButton:hover {{ background-color: #00f0c6; }}"
        )
        self._send_btn.clicked.connect(self._send_message)
        layout.addWidget(self._send_btn, 0)

        return input_frame

    # ------------------------------------------------------------------ #
    #  Panel helpers
    # ------------------------------------------------------------------ #

    def _make_panel_toggle(self, text, color, active=False):
        """Create a pill-style panel toggle button."""
        btn = QPushButton(text)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFixedHeight(26)
        btn.setCheckable(True)
        btn.setChecked(active)
        active_bg = f"background-color: {color}; color: #000;"
        inactive_bg = f"background-color: transparent; color: {LIGHT_GREY}; border: 1px solid rgba(255,255,255,0.10);"
        btn.setStyleSheet(
            f"QPushButton {{ {active_bg if active else inactive_bg} "
            f"border-radius: 6px; padding: 0px 12px; font-size: 11px; font-weight: 600; }}"
            f"QPushButton:hover {{ background-color: rgba(255,255,255,0.10); }}"
        )
        return btn

    def _make_small_button(self, text, color):
        """Create a small action button."""
        btn = QPushButton(text)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFixedHeight(24)
        btn.setStyleSheet(
            f"QPushButton {{ background-color: transparent; color: {color}; "
            f"border: 1px solid {color}; border-radius: 4px; "
            f"padding: 0px 10px; font-size: 10px; font-weight: 600; }}"
            f"QPushButton:hover {{ background-color: rgba({','.join(str(int(color[i:i+2],16)) for i in (1,3,5))},0.15); }}"
            f"QPushButton:disabled {{ color: #555; border-color: #333; }}"
        )
        return btn

    def _switch_panel(self, panel):
        """Switch the active bottom panel."""
        self._active_panel = panel

        # Update button states
        for btn, p, color in [
            (self._code_panel_btn, 'code', '#F1C40F'),
            (self._term_panel_btn, 'terminal', '#00FF00'),
            (self._deps_panel_btn, 'dependencies', '#60A5FA'),
            (self._io_panel_btn, 'io', '#A78BFA'),
        ]:
            active = p == panel
            btn.setChecked(active)
            if active:
                btn.setStyleSheet(
                    f"QPushButton {{ background-color: {color}; color: #000; "
                    f"border-radius: 6px; padding: 0px 12px; font-size: 11px; font-weight: 600; }}"
                )
            else:
                btn.setStyleSheet(
                    f"QPushButton {{ background-color: transparent; color: {LIGHT_GREY}; "
                    f"border: 1px solid rgba(255,255,255,0.10); border-radius: 6px; "
                    f"padding: 0px 12px; font-size: 11px; font-weight: 600; }}"
                    f"QPushButton:hover {{ background-color: rgba(255,255,255,0.10); }}"
                )

        # Show/hide panels
        self._code_panel.setVisible(panel == 'code')
        self._term_panel.setVisible(panel == 'terminal')
        self._deps_panel.setVisible(panel == 'dependencies')
        self._io_panel.setVisible(panel == 'io')

    # ------------------------------------------------------------------ #
    #  Chat / Memory
    # ------------------------------------------------------------------ #

    def _add_welcome_message(self):
        """Add a welcome message to the chat."""
        welcome = (
            "Welcome to the Code Agent!\n\n"
            "Describe the Python code you'd like to write, and I'll generate it for you. "
            "You can iterate by describing fixes or enhancements. "
            "Use the **Code** panel to view/edit code, **Terminal** to run it, "
            "and **Dependencies** to manage packages."
        )
        self._append_to_memory("assistant", welcome)
        self._render_chat()

    def _append_to_memory(self, role, content):
        """Add a message to the conversation memory and re-render."""
        self._memory.append({"role": role, "content": content})

    def _build_conversation_prompt(self):
        """Format memory as a prompt string for the LLM, including execution context."""
        lines = []
        for msg in self._memory:
            if msg['role'] == 'user':
                lines.append(f"User: {msg['content']}")
            elif msg['role'] == 'assistant':
                lines.append(f"Assistant: {msg['content']}")
            elif msg['role'] == 'execution':
                lines.append(f"Execution Result: {msg['content']}")
        return "\n\n".join(lines)

    def _send_message(self):
        """Send user message to the AI."""
        text = self._input_field.toPlainText().strip()
        if not text:
            return

        self._input_field.clear()

        # Agentic loop branch
        if self._agentic_enabled:
            self._start_agentic_loop(text)
            return

        self._send_btn.setEnabled(False)
        self._send_btn.setText(_("..."))

        # Add user message
        self._append_to_memory("user", text)
        self._render_chat()
        self._scroll_chat_to_bottom()

        # Build conversation prompt
        prompt = self._build_conversation_prompt()
        use_llamacpp = (self.engine_combo.currentText() == "llama.cpp")
        if use_llamacpp:
            model = self.llamacpp_combo.currentText()
        else:
            model = self.model_combo.currentText()

        self._gen_thread = CodeAgentThread(prompt, model, use_llamacpp=use_llamacpp)
        self._gen_thread.finished.connect(self._on_ai_response)
        self._gen_thread.error.connect(self._on_ai_error)
        self._gen_thread.start()

    def _on_ai_response(self, response_text):
        """Handle AI response: add to memory, extract code, update editor."""
        self._send_btn.setEnabled(True)
        self._send_btn.setText(_("Send"))

        self._append_to_memory("assistant", response_text)
        self._render_chat()
        self._scroll_chat_to_bottom()

        # Extract code and update editor
        code = _extract_code(response_text)
        if code:
            self._code_edit.setPlainText(code)
            self.current_code = code

    def _on_ai_error(self, error_msg):
        """Handle AI error gracefully."""
        self._send_btn.setEnabled(True)
        self._send_btn.setText(_("Send"))
        self._append_to_memory("assistant", f"Error: {error_msg}")
        self._render_chat()
        self._scroll_chat_to_bottom()

    # ------------------------------------------------------------------
    #  Agentic loop methods
    # ------------------------------------------------------------------

    def _toggle_agentic(self):
        """Toggle agentic loop on/off."""
        self._agentic_enabled = self._agentic_toggle_btn.isChecked()
        self._iteration_status_label.setText(
            "Auto" if self._agentic_enabled else "Manual"
        )

    def _start_agentic_loop(self, user_request):
        """Start the agentic code refinement loop in background."""
        self._send_btn.setEnabled(False)
        self._send_btn.setText("\U0001f916")

        self._append_to_memory("user", user_request)
        self._render_chat()
        self._scroll_chat_to_bottom()

        use_llamacpp = (self.engine_combo.currentText() == "llama.cpp")
        model = self.llamacpp_combo.currentText() if use_llamacpp else self.model_combo.currentText()
        api_url = get_default_api_url()

        # Parse test args
        test_args = None
        try:
            raw = self._test_args_edit.toPlainText().strip()
            if raw:
                test_args = json.loads(raw)
        except Exception:
            pass

        if self._agentic_controller:
            self._agentic_controller.stop_loop()

        self._agentic_controller = AgenticLoopController()
        self._agentic_controller.phase_changed.connect(self._on_loop_phase)
        self._agentic_controller.code_updated.connect(self._on_loop_code)
        self._agentic_controller.status_message.connect(self._on_loop_status)
        self._agentic_controller.test_output.connect(self._on_loop_test_output)
        self._agentic_controller.iteration_done.connect(self._on_loop_iteration)
        self._agentic_controller.loop_finished.connect(self._on_loop_finished)
        self._agentic_controller.error_occurred.connect(self._on_ai_error)

        self._agentic_controller.start_loop(
            user_request=user_request,
            model=model,
            api_url=api_url,
            use_llamacpp=use_llamacpp,
            initial_code=self.current_code,
            max_iterations=self._max_iterations,
            test_args=test_args,
        )

    def _stop_agentic_loop(self):
        """Stop the running agentic loop gracefully."""
        if self._agentic_controller:
            self._agentic_controller.stop_loop()
            self._agentic_controller = None
        self._send_btn.setEnabled(True)
        self._send_btn.setText(_("Send"))
        self._iteration_status_label.setText("Stopped")

    def _on_loop_phase(self, phase, iteration):
        """Update status label when loop phase changes."""
        labels = {
            LoopPhase.DRAFTING: "Generating...",
            LoopPhase.SYNTAX_CHECK: "Checking syntax...",
            LoopPhase.TESTING: "Testing...",
            LoopPhase.ANALYZING: "Analyzing...",
            LoopPhase.REFINING: "Refining...",
        }
        label = labels.get(phase, "")
        icon = "\U0001f916" if phase not in (LoopPhase.COMPLETE, LoopPhase.FAILED, LoopPhase.IDLE) else ""
        self._iteration_status_label.setText(
            f"{icon} {f'{iteration}/{self._max_iterations}' if iteration else ''} {label}".strip()
        )

    def _on_loop_code(self, code):
        """Update code editor with generated code."""
        self._code_edit.setPlainText(code)
        self.current_code = code

    def _on_loop_status(self, message):
        """Append status message to chat."""
        if message:
            self._append_to_memory("assistant", f"_{message}_")
            self._render_chat()
            self._scroll_chat_to_bottom()

    def _on_loop_test_output(self, output, exit_code):
        """Append test output to the terminal panel."""
        self._append_terminal(output)
        if not self._term_panel.isVisible():
            self._switch_panel('terminal')

    def _on_loop_iteration(self, record):
        """Log iteration summary to chat memory."""
        check = "\u2713"
        cross = "\u2717"
        dash = "\u2014"
        summary = (
            f"**Iteration {record.iteration}** {dash} "
            f"Syntax: {check if record.syntax_ok else cross}, "
            f"Exit code: {record.exit_code}, "
            f"Ready: {check if record.ready else dash}"
        )
        self._append_to_memory("execution", summary)
        self._render_chat()
        self._scroll_chat_to_bottom()

    def _on_loop_finished(self, success, message):
        """Handle agentic loop completion."""
        self._send_btn.setEnabled(True)
        self._send_btn.setText(_("Send"))
        self._iteration_status_label.setText(
            "\u2705 Ready" if success else "\u274c Stopped"
        )
        check = "\u2713"
        cross = "\u2717"
        status_str = f"{check} Code ready" if success else f"{cross} Failed"
        self._append_to_memory(
            "assistant",
            f"**{status_str}**: {message}"
        )
        self._render_chat()
        self._scroll_chat_to_bottom()

    # ------------------------------------------------------------------
    #  Syntax check & test runner
    # ------------------------------------------------------------------

    def _syntax_check(self):
        """Run syntax check on the current code."""
        code = self._code_edit.toPlainText().strip()
        try:
            ast.parse(code)
            self._append_terminal("\u2713 Syntax check passed.\n")
        except SyntaxError as e:
            self._append_terminal(f"\u2717 Syntax error at line {e.lineno}: {e.msg}\n")
            self._switch_panel('terminal')

    def _run_all_tests(self):
        """Run the current code against all test arg sets defined in test_args_edit."""
        code = self._code_edit.toPlainText().strip()
        if not code:
            self._append_terminal("No code to test.\n")
            return

        raw = self._test_args_edit.toPlainText().strip()
        if not raw:
            self._append_terminal("No test args defined. Run manually or add JSON test args.\n")
            return

        try:
            test_sets = json.loads(raw)
            if not isinstance(test_sets, list):
                self._append_terminal("Test args must be a JSON array.\n")
                return
        except json.JSONDecodeError as e:
            self._append_terminal(f"Invalid JSON: {e}\n")
            return

        self._switch_panel('terminal')
        import subprocess
        import tempfile

        for i, args_set in enumerate(test_sets):
            self._append_terminal(f"--- Test {i + 1}/{len(test_sets)}: {json.dumps(args_set)[:80]} ---\n")
            # Build wrapped code that injects args variable
            wrapped = (
                f"import json\n"
                f"args = {json.dumps(args_set)}\n"
                f"input_data = [args]\n"
                f"{code}\n"
            )
            try:
                with tempfile.NamedTemporaryFile(
                    mode='w', suffix='.py', delete=False, encoding='utf-8'
                ) as f:
                    f.write(wrapped)
                    tmp = f.name
                result = subprocess.run(
                    [sys.executable, tmp],
                    capture_output=True, text=True, timeout=30
                )
                if result.stdout:
                    self._append_terminal(result.stdout)
                if result.stderr:
                    self._append_terminal(result.stderr)
                self._append_terminal(f"Exit code: {result.returncode}\n")
                try:
                    os.unlink(tmp)
                except Exception:
                    pass
            except subprocess.TimeoutExpired:
                self._append_terminal("[Test timed out]\n")
            except Exception as e:
                self._append_terminal(f"[Error: {e}]\n")

    def _render_chat(self):
        """Render the full chat history in the scroll area."""
        # Clear existing bubbles
        while self._chat_container_layout.count() > 1:
            item = self._chat_container_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for msg in self._memory:
            bubble = self._create_chat_bubble(msg['role'], msg['content'])
            if bubble:
                align = Qt.AlignRight if msg['role'] == 'user' else Qt.AlignLeft
                self._chat_container_layout.addWidget(bubble, 0, align)

    def _create_chat_bubble(self, role, content):
        """Create a styled chat bubble widget."""
        if role == 'user':
            bg = '#1E2A2A'
            border = ACCENT_COLOR
        elif role == 'execution':
            bg = '#1A1A2E'
            border = '#888888'
        else:
            bg = SIDE_PANEL_BG
            border = '#FFB347'

        bubble = QFrame()
        bubble.setStyleSheet(
            f"QFrame {{ background-color: {bg}; border: 1px solid rgba(255,255,255,0.06); "
            f"border-left: 3px solid {border}; border-radius: 8px; }}"
        )
        bl = QVBoxLayout(bubble)
        bl.setContentsMargins(12, 8, 12, 8)
        bl.setSpacing(2)

        label = QLabel(content)
        label.setWordWrap(True)
        label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        font = label.font()
        font.setPixelSize(13)
        label.setFont(font)
        label.setStyleSheet(f"color: {TEXT_COLOR};")

        # Set max width based on scroll area
        try:
            max_w = max(240, int(self._chat_scroll.viewport().width() * 0.85))
            label.setFixedWidth(max_w)
        except Exception:
            label.setFixedWidth(400)

        bl.addWidget(label)
        return bubble

    def _scroll_chat_to_bottom(self):
        """Scroll the chat to the bottom."""
        try:
            bar = self._chat_scroll.verticalScrollBar()
            bar.setValue(bar.maximum())
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    #  Model management
    # ------------------------------------------------------------------ #

    def _init_models(self):
        """Populate model combos and set engine selection."""
        # Ollama models
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

        ollama_model = self.config.get('gen_model', 'llama3.2:latest')
        self.model_combo.setCurrentText(ollama_model)

        # llama.cpp GGUF models
        gguf_models = get_llamacpp_models()
        if gguf_models:
            self.llamacpp_combo.addItems(gguf_models)
        else:
            self.llamacpp_combo.setPlaceholderText("No GGUF models found")
        llamacpp_model = self.config.get('llamacpp_model_path', '')
        if llamacpp_model:
            self.llamacpp_combo.setCurrentText(llamacpp_model)

        # Apply engine selection from config
        use_llamacpp = self.config.get('use_llamacpp', False)
        if isinstance(use_llamacpp, str):
            use_llamacpp = use_llamacpp.lower() in ('true', '1', 'yes', 'on')
        self._use_llamacpp = use_llamacpp
        if use_llamacpp:
            self.engine_combo.setCurrentText("llama.cpp")
        self._apply_engine_visibility()

    def _on_engine_changed(self, engine):
        """Handle engine selection change."""
        use_llamacpp = (engine == "llama.cpp")
        self._use_llamacpp = use_llamacpp
        self._apply_engine_visibility()

        # Refresh GGUF list when switching to llama.cpp
        if use_llamacpp:
            current_llamacpp = self.llamacpp_combo.currentText()
            gguf_models = get_llamacpp_models()
            self.llamacpp_combo.clear()
            if gguf_models:
                self.llamacpp_combo.addItems(gguf_models)
                if current_llamacpp in gguf_models:
                    self.llamacpp_combo.setCurrentText(current_llamacpp)
            else:
                self.llamacpp_combo.setPlaceholderText("No GGUF models found")

    def _apply_engine_visibility(self):
        """Show/hide model widgets based on engine selection."""
        use_llamacpp = self._use_llamacpp
        self.model_label.setVisible(not use_llamacpp)
        self.model_combo.setVisible(not use_llamacpp)
        self.refresh_btn.setVisible(not use_llamacpp)
        self.llamacpp_label.setVisible(use_llamacpp)
        self.llamacpp_combo.setVisible(use_llamacpp)

    def refresh_models(self):
        """Refresh Ollama model list asynchronously."""
        api_url = get_default_api_url()
        current_model = self.model_combo.currentText()
        self.refresh_btn.setEnabled(False)
        self.refresh_btn.setText("...")

        def _do_refresh():
            try:
                cache = get_model_cache()
                if cache:
                    cache.refresh_models(api_url)
                models = get_real_cached_models(timeout=5.0)
            except Exception:
                models = []
            self._refresh_complete_signal.emit(models, current_model)

        threading.Thread(target=_do_refresh, daemon=True).start()

    def _on_refresh_complete(self, models, current_model):
        """Called on main thread when model refresh finishes."""
        self.refresh_btn.setEnabled(True)
        self.refresh_btn.setText("R")
        if models:
            self.model_combo.clear()
            self.model_combo.addItems(models)
            if current_model in models:
                self.model_combo.setCurrentText(current_model)

    # ------------------------------------------------------------------ #
    #  Code execution
    # ------------------------------------------------------------------ #

    def _run_code(self):
        """Execute the current code in a subprocess."""
        code = self._code_edit.toPlainText().strip()
        if not code:
            return

        temp_file = os.path.join(
            os.environ.get('TEMP', os.getcwd()),
            f'_looper_code_node_{os.getpid()}.py'
        )
        try:
            with open(temp_file, 'w', encoding='utf-8') as f:
                f.write(code)
        except Exception as e:
            self._append_terminal(f"Error writing temp file: {e}\n")
            return

        self._append_terminal(f"> python {os.path.basename(temp_file)}\n")
        self._run_code_btn.setEnabled(False)
        self._kill_btn.setEnabled(True)

        self._process = QProcess(self)
        self._process.setProcessChannelMode(QProcess.MergedChannels)
        timeout = self.config.get('timeout', 30)

        self._run_timer = QTimer(self)
        self._run_timer.setSingleShot(True)
        self._run_timer.timeout.connect(self._on_run_timeout)

        stdout_buffer = []

        def on_ready():
            data = self._process.readAllStandardOutput().data().decode('utf-8', errors='replace')
            if data:
                stdout_buffer.append(data)

        def on_finished(exit_code, exit_status):
            self._process = None
            self._run_code_btn.setEnabled(True)
            self._kill_btn.setEnabled(False)
            self._run_timer.stop()

            output_text = ''.join(stdout_buffer)
            if output_text:
                self._append_terminal(output_text)

            if exit_code == 0:
                self._append_terminal(_("\n[Process exited with code 0]\n"))
            else:
                self._append_terminal(_("\n[Process exited with code {}]\n").format(exit_code))

            # Add execution result to memory for context
            result_preview = output_text.strip()[:500] if output_text.strip() else "(no output)"
            execution_note = f"Ran code (exit code {exit_code}). Output: {result_preview}"
            self._append_to_memory("execution", execution_note)

            # Cleanup
            try:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
            except Exception:
                pass

        self._process.readyReadStandardOutput.connect(on_ready)
        self._process.readyReadStandardError.connect(on_ready)
        self._process.finished.connect(on_finished)

        self._process.start(_resolve_python(), [temp_file])

        if timeout > 0:
            self._run_timer.start(timeout * 1000)

    def _on_run_timeout(self):
        """Handle code execution timeout."""
        if self._process and self._process.state() == QProcess.Running:
            self._append_terminal(_("\n[Timeout reached, terminating...]\n"))
            self._process.kill()
            self._append_to_memory("execution",
                                    "Code execution timed out and was terminated.")

    def _kill_process(self):
        """Kill the running process."""
        if self._process and self._process.state() == QProcess.Running:
            self._append_terminal(_("\n[Process terminated by user]\n"))
            self._process.kill()
            self._run_code_btn.setEnabled(True)
            self._kill_btn.setEnabled(False)
            self._append_to_memory("execution", "Code execution terminated by user.")

    def _append_terminal(self, text):
        """Append text to the terminal output."""
        cursor = self._term_output.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(text)
        self._term_output.setTextCursor(cursor)
        self._term_output.verticalScrollBar().setValue(
            self._term_output.verticalScrollBar().maximum()
        )

    def _clear_terminal(self):
        """Clear the terminal output."""
        self._term_output.clear()

    def _send_terminal_command(self):
        """Send a typed command to the terminal output as execution feedback."""
        cmd = self._term_input.text().strip()
        if not cmd:
            return
        self._term_input.clear()
        self._append_terminal(f"$ {cmd}\n")

        # Execute as Python expression or shell command
        try:
            import subprocess
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=10
            )
            if result.stdout:
                self._append_terminal(result.stdout)
            if result.stderr:
                self._append_terminal(result.stderr)
            if result.returncode != 0:
                self._append_terminal(_("\n[Exit code: {}]\n").format(result.returncode))
        except subprocess.TimeoutExpired:
            self._append_terminal(_("\n[Command timed out]\n"))
        except Exception as e:
            self._append_terminal(f"\n[Error: {e}]\n")

    # ------------------------------------------------------------------ #
    #  Dependency management
    # ------------------------------------------------------------------ #

    def _auto_detect_deps(self):
        """Scan code for imports and suggest missing packages."""
        code = self._code_edit.toPlainText().strip()
        if not code:
            return

        detected = _auto_detect_dependencies(code)
        existing = set(self._dependencies)

        added = 0
        for pkg in detected:
            if pkg not in existing:
                self._dependencies.append(pkg)
                self._deps_list.addItem(pkg)
                existing.add(pkg)
                added += 1

        if added > 0:
            self._append_terminal(_("Auto-detected and added {} package(s)\n").format(added))
        else:
            self._append_terminal(_("No new dependencies detected.\n"))

    def _add_dependency(self):
        """Add a manual dependency."""
        pkg = self._dep_input.text().strip()
        if not pkg:
            return
        self._dep_input.clear()
        if pkg not in self._dependencies:
            self._dependencies.append(pkg)
            self._deps_list.addItem(pkg)

    def _remove_dependency(self):
        """Remove selected dependency."""
        selected = self._deps_list.currentRow()
        if selected >= 0 and selected < len(self._dependencies):
            self._dependencies.pop(selected)
            self._deps_list.takeItem(selected)

    # ------------------------------------------------------------------ #
    #  Custom IO port variable rows
    # ------------------------------------------------------------------ #

    VAR_TYPES = ['string', 'int', 'float', 'bool', 'list', 'dict', 'any']

    def _build_var_row(self, name='', vtype='string', is_input=True):
        """Create a single row widget for a variable declaration."""
        row = QFrame()
        row.setStyleSheet(
            "QFrame { background-color: rgba(255,255,255,0.03); border-radius: 4px; }"
        )
        rl = QHBoxLayout(row)
        rl.setContentsMargins(6, 3, 6, 3)
        rl.setSpacing(6)

        name_field = QLineEdit(name)
        name_field.setPlaceholderText(_("variable name"))
        name_field.setFixedHeight(24)
        name_field.setStyleSheet(
            f"QLineEdit {{ background-color: #0C0C0C; color: {TEXT_COLOR}; "
            f"border: 1px solid rgba(255,255,255,0.08); border-radius: 4px; "
            f"padding: 2px 6px; font-size: 11px; font-family: Consolas; }}"
            f"QLineEdit:focus {{ border-color: {ACCENT_COLOR}; }}"
        )
        rl.addWidget(name_field, 1)

        type_combo = QComboBox()
        type_combo.addItems(self.VAR_TYPES)
        type_combo.setCurrentText(vtype)
        type_combo.setFixedHeight(24)
        type_combo.setFixedWidth(80)
        type_combo.setStyleSheet(
            f"QComboBox {{ background-color: #0C0C0C; color: {TEXT_COLOR}; "
            f"border: 1px solid rgba(255,255,255,0.08); border-radius: 4px; "
            f"padding: 2px 4px; font-size: 10px; }}"
            f"QComboBox::drop-down {{ border: none; width: 16px; }}"
            f"QComboBox QAbstractItemView {{ background-color: #0C0C0C; "
            f"color: {TEXT_COLOR}; selection-background-color: {ACCENT_COLOR}; "
            f"selection-color: #000; }}"
        )
        rl.addWidget(type_combo)

        remove_btn = QPushButton(_("\u00d7"))
        remove_btn.setFixedSize(22, 22)
        remove_btn.setCursor(Qt.PointingHandCursor)
        remove_btn.setToolTip(_("Remove this variable"))
        remove_btn.setStyleSheet(
            f"QPushButton {{ background-color: transparent; color: #ef4444; "
            f"border: 1px solid transparent; border-radius: 4px; "
            f"font-size: 14px; font-weight: 600; }}"
            f"QPushButton:hover {{ background-color: rgba(239,68,68,0.15); "
            f"border-color: #ef4444; }}"
        )
        remove_btn.clicked.connect(lambda: self._remove_var_row(row, is_input))
        rl.addWidget(remove_btn)

        return row

    def _add_input_var_row(self, name='', vtype='string'):
        """Add an input variable row to the IO panel."""
        row = self._build_var_row(name, vtype, is_input=True)
        self._io_input_layout.insertWidget(
            self._io_input_layout.count(), row, 0, Qt.AlignTop
        )

    def _add_output_var_row(self, name='', vtype='string'):
        """Add an output variable row to the IO panel."""
        row = self._build_var_row(name, vtype, is_input=False)
        self._io_output_layout.insertWidget(
            self._io_output_layout.count(), row, 0, Qt.AlignTop
        )

    def _remove_var_row(self, row, is_input):
        """Remove a variable row widget."""
        try:
            row.setParent(None)
            row.deleteLater()
        except Exception:
            pass

    def _collect_var_rows(self, container_layout):
        """Collect (name, type) from all row widgets in the given layout."""
        result = []
        for i in range(container_layout.count()):
            item = container_layout.itemAt(i)
            if item and item.widget():
                row = item.widget()
                # Find name field and type combo within the row
                name_field = row.findChild(QLineEdit)
                type_combo = row.findChild(QComboBox)
                if name_field and type_combo:
                    n = name_field.text().strip()
                    if n:
                        result.append({'name': n, 'type': type_combo.currentText()})
        return result

    def _install_dependencies(self):
        """Install all dependencies via pip in a subprocess."""
        if not self._dependencies:
            self._append_terminal(_("No dependencies to install.\n"))
            return

        self._append_terminal(_("Installing dependencies: {}\n").format(
            ', '.join(self._dependencies)
        ))

        # Switch to terminal panel to show output
        self._switch_panel('terminal')

        try:
            import subprocess
            result = subprocess.run(
                [_resolve_python(), "-m", "pip", "install"] + self._dependencies,
                capture_output=True, text=True, timeout=120
            )
            if result.stdout:
                self._append_terminal(result.stdout)
            if result.stderr:
                self._append_terminal(result.stderr)
            if result.returncode == 0:
                self._append_terminal(_("\n[All dependencies installed successfully]\n"))
            else:
                self._append_terminal(_("\n[Installation completed with exit code {}]\n").format(
                    result.returncode
                ))
        except subprocess.TimeoutExpired:
            self._append_terminal(_("\n[Installation timed out]\n"))
        except Exception as e:
            self._append_terminal(f"\n[Error installing dependencies: {e}]\n")

    # ------------------------------------------------------------------ #
    #  Code change tracking
    # ------------------------------------------------------------------ #

    def _on_code_changed(self):
        self.current_code = self._code_edit.toPlainText()

    # ------------------------------------------------------------------ #
    #  Event filter (Enter to send)
    # ------------------------------------------------------------------ #

    def eventFilter(self, obj, event):
        from PyQt5.QtCore import QEvent
        from PyQt5.QtGui import QKeyEvent
        if obj is self._input_field and event.type() == QEvent.KeyPress:
            ke = QKeyEvent(event)
            if ke.key() in (Qt.Key_Return, Qt.Key_Enter):
                if ke.modifiers() & Qt.ShiftModifier:
                    return False
                self._send_message()
                return True
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------ #
    #  Config
    # ------------------------------------------------------------------ #

    def get_config(self):
        """Return the dialog configuration for saving."""
        use_llamacpp = (self.engine_combo.currentText() == "llama.cpp")
        # Collect current IO var declarations from the UI
        input_vars = self._collect_var_rows(self._io_input_layout)
        output_vars = self._collect_var_rows(self._io_output_layout)
        return {
            'code': self._code_edit.toPlainText(),
            'file_path': '',
            'execute_on_input': True,
            'output_variable': self.config.get('output_variable', 'result'),
            'timeout': self.config.get('timeout', 30),
            'use_llamacpp': use_llamacpp,
            'gen_model': self.model_combo.currentText() if not use_llamacpp else 'llamacpp',
            'llamacpp_model_path': self.llamacpp_combo.currentText() if use_llamacpp else '',
            'gen_prompt': '',
            'description': self.desc_edit.text(),
            'dependencies': self._dependencies,
            'memory_context': self._memory,
            'agentic_enabled': self._agentic_enabled,
            'max_iterations': self._max_iterations,
            'iteration_history': (
                self._agentic_controller.get_history()
                if self._agentic_controller else []
            ),
            'test_args': self._test_args_edit.toPlainText().strip(),
            'input_vars': input_vars,
            'output_vars': output_vars,
        }

    def closeEvent(self, event):
        """Clean up agentic loop on dialog close."""
        self._stop_agentic_loop()
        super().closeEvent(event)
