"""Constants for the Code Node Studio coding agent.

Kept deliberately small.  There is no spec/ladder machinery here anymore:
the panel runs a flat, little-coder-style loop (lean prompt, a handful of
protocol "tools", bounded turns, error-driven retries) against the node's
workspace.  Everything below is just budgets, steering knobs and the
palette-matched stylesheet.
"""
import re

from NGUI.constants import (
    ACCENT_COLOR,
    ACCENT_HOVER,
    ACCENT_SOFT,
    BLOCK_HOVER,
    BTN_PRIMARY_TEXT,
    CARD_BG,
    CONTROL_BG,
    CONTROL_HOVER,
    DANGER_COLOR,
    DARK_GREY,
    HAIRLINE,
    MEDIUM_GREY,
    RADIUS_MD,
    RADIUS_SM,
    TEXT_COLOR,
    TEXT_MUTED,
    TEXT_SECONDARY,
    WELL_BG,
)

VAR_TYPES = ['string', 'int', 'float', 'bool', 'list', 'dict', 'any']

# Port names that never become code variables (mirrors code_ops.py).
RESERVED_INPUT_PORTS = {'input', 'args', 'code', 'input_data', 'os', 'print',
                        'None', ''}
RESERVED_OUTPUT_PORTS = {'output', 'error'}

MAIN_FILE = 'script.py'        # executed entry file of the node workspace

# --- project files carried inside the node/chain (text only) ---
# A code node is a small project: script.py is the entry script, every other
# file lives beside it (importable modules, data, assets-as-text).  Files are
# stored in the node property 'project_files' so a chain travels whole.
PROJECT_MAX_FILE_BYTES = 200_000      # skip any single larger file
PROJECT_MAX_TOTAL_BYTES = 2_000_000   # hard cap for the whole node project
PROJECT_SKIP_DIRS = {'__pycache__', '.git', '.venv', 'venv', 'node_modules',
                     '.idea', '.vscode', '.mypy_cache', '.pytest_cache'}
PROJECT_SKIP_EXTS = {'.pyc', '.pyo', '.pyd', '.exe', '.dll', '.so', '.dylib',
                     '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.ico',
                     '.zip', '.7z', '.tar', '.gz', '.pdf', '.db', '.sqlite',
                     '.gguf', '.onnx', '.pt', '.bin'}

# --- loop budgets (small models: bounded turns beat open-ended autonomy) ---
MAX_CHAT_TURNS = 6             # aider-style ring memory (last exchanges only)
MAX_WORK_ROUNDS = 5            # applied main-code passes per request
MAX_TOOL_TURNS = 6             # investigation turns (GREP / NEED FILE / re-asks)
MAX_CHAT_STREAM_CHARS = 16000  # hard cap per reply (degenerate-repetition guard)
MAX_FILES_PER_REPLY = 8        # sane max of ### FILE: blocks in one reply

# --- failure-adaptive sampler (see chat_stream._adapt_sampler) ---
# A re-asked turn must never replay the same token sequence: truncation grows
# the output budget, repetition raises the anti-repeat penalty, anything else
# wobbles the temperature along an explore/focus cycle.
STEER_PENALTY_BASE = 1.15      # llama.cpp repeat_penalty baseline
STEER_PENALTY_STEP = 0.1       # escalation per steering correction
STEER_PENALTY_MAX = 1.6
STEER_TEMP_BASE = 0.3
STEER_EMPTY_CYCLE = (0.7, 0.15, 0.9)
STEER_MAXTOK_STEP = 1.6        # truncation retry: output budget growth per try
STEER_MAXTOK_MAX = 3.0         # ceiling of that growth (ctx-clamped later)

# Scaffolding the studio itself injects - a reply that starts by echoing these
# headers is a small model copying the prompt instead of producing its answer.
_ECHO_PREFIXES = (
    '### declared input ports', '### declared output ports',
    '### connected inputs', '### workspace files', '### script.py',
    '### node script.py', '### node code', '### code outline',
    '### last run', '### last grep results', '### request', '### file:',
)

# GUI/persistent-loop hints: such code is skipped by the auto-run so a window
# never pops open mid-editing (mirrors code_ops._is_persistent_loop).
_GUI_HINTS = (
    'import tkinter', 'from tkinter', 'tkinter', 'mainloop(', 'tk.',
    'import pyqt', 'from pyqt', 'pyside', 'exec_', 'import wx', 'from wx',
    'pygame', 'dearpygui', 'kivy', 'plt.show(', 'matplotlib.pyplot.show',
    'pywebview', 'cefpython', 'while true',
)

# Single-line shell launcher (e.g. someone typed `python sticky_notes.py`
# as the node code) - never valid Python; detected to give a real hint.
_SHELL_CMD_RE = re.compile(r'^\s*(?:python|python3|pythonw|py)\s+',
                           re.IGNORECASE)

# --- context budgets (chars of text shown to the model per turn) ---
_MAX_CODE_CTX = 2200        # chars of main code per message (prefill cost)
_MAX_DIGEST_HEAD = 200
_MAX_DIGEST_TAIL = 200
_MAX_RUN_STDOUT = 1500
_MAX_RUN_STDERR = 3000
_MAX_RUN_RESULT = 800
_MAX_HELPER_CTX = 500       # chars per extra workspace file shown to the model
_MAX_HELPERS_IN_CTX = 2
_MAX_HISTORY_TURNS = 2      # prior exchanges folded into the next context
_MAX_HISTORY_CHARS = 320

# Stylesheet matching the app's plane system (canvas -> card -> well) and its
# single cyan accent, so the studio reads like the rest of the NGUI. Buttons use
# the same translucent-white-on-card idiom as the actions bar / agent overlay
# (no solid boxy fills), and the tab strip uses a soft accent checked state.
_STUDIO_QSS = f"""
#CodeStudioRoot {{ background: {DARK_GREY}; }}
/* The host window paints its descendants with QWidget {{ GRAPH_PLANE }}, a
   lighter grey; these ID rules keep every studio surface on the page plane. */
QWidget#StudioPage {{ background: {DARK_GREY}; }}
QStackedWidget#StudioStack {{ background: {DARK_GREY}; }}
/* Labels and plain frames must be transparent: the app-wide QWidget rule paints
   a DARK_GREY box behind them, which reads as a dark mat inside the CARD_BG
   sections (the "wrong background box around the text"). */
QLabel {{ color: {TEXT_SECONDARY}; background: transparent; font-size: 11px; }}
QFrame {{ background: transparent; }}
QLabel#StudioTitle {{ color: {TEXT_COLOR}; font-size: 13px; font-weight: 700; }}
QLabel#StudioSub {{ color: {TEXT_MUTED}; font-size: 10px; }}
QLabel#FileLabel {{ color: {ACCENT_COLOR}; font-size: 11px; font-family: Consolas; }}
QFrame#StudioRule {{ background: {HAIRLINE}; max-height: 1px; border: none; }}
QPushButton {{
    background-color: rgba(255, 255, 255, 0.05); color: {TEXT_COLOR};
    border: 1px solid {HAIRLINE}; border-radius: {RADIUS_SM}px;
    padding: 4px 10px; font-size: 11px;
}}
QPushButton:hover {{ background-color: rgba(255, 255, 255, 0.09); border-color: {ACCENT_COLOR}; }}
QPushButton:pressed {{ background-color: rgba(255, 255, 255, 0.13); }}
QPushButton:disabled {{ color: {TEXT_MUTED}; border-color: {HAIRLINE}; background: transparent; }}
QPushButton#primary {{
    background-color: {ACCENT_SOFT}; color: {TEXT_COLOR};
    border: 1px solid {ACCENT_COLOR};
}}
QPushButton#primary:hover {{ background-color: rgba(34, 211, 238, 0.22); }}
QPushButton#danger {{
    background-color: transparent; color: {DANGER_COLOR};
    border: 1px solid {DANGER_COLOR};
}}
QPushButton#danger:hover {{ background-color: rgba(239, 68, 68, 0.14); }}
QPushButton#danger:disabled {{ border-color: {HAIRLINE}; color: {TEXT_MUTED}; }}
QPushButton#StudioTab {{ padding: 0; border-radius: {RADIUS_SM}px; }}
QPushButton#StudioTab:checked {{ background-color: {ACCENT_SOFT}; border: 1px solid {ACCENT_COLOR}; }}
QComboBox#StudioSelect {{
    background-color: {WELL_BG}; color: {TEXT_COLOR};
    border: 1px solid {HAIRLINE}; border-radius: {RADIUS_SM}px;
    padding: 2px 8px; font-size: 11px;
}}
QComboBox#StudioSelect:hover, QComboBox#StudioSelect:focus {{ border-color: {ACCENT_COLOR}; }}
QComboBox#StudioSelect::drop-down {{ border: none; width: 16px; }}
QComboBox QAbstractItemView {{
    background-color: {MEDIUM_GREY}; color: {TEXT_COLOR};
    selection-background-color: {ACCENT_COLOR}; selection-color: {BTN_PRIMARY_TEXT};
    border: 1px solid {HAIRLINE};
}}
QLineEdit, QPlainTextEdit, QTextEdit, QTreeWidget {{
    background-color: {WELL_BG}; color: {TEXT_COLOR};
    border: 1px solid {HAIRLINE}; border-radius: {RADIUS_SM}px;
    selection-background-color: {ACCENT_COLOR}; selection-color: {BTN_PRIMARY_TEXT};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QTreeWidget:focus {{ border: 1px solid {ACCENT_COLOR}; }}
QPlainTextEdit {{ font-family: Consolas; }}
QFrame#StudioWell {{ background: {WELL_BG}; border: 1px solid {HAIRLINE}; border-radius: {RADIUS_MD}px; }}
/* Chat: real widget bubbles (QTextEdit rich text cannot do border-radius).
   The conversation holder uses the PAGE colour (not WELL_BG) so the tinted
   bubbles composite over the same plane as the rest of the studio instead of
   sitting in a differently-coloured mat. */
QScrollArea#ChatScroll {{ background: {DARK_GREY}; border: 1px solid {HAIRLINE};
    border-radius: {RADIUS_MD}px; }}
/* Transparent children: painting them would square off the rounded corners
   and hide the holder's own surface. */
QScrollArea#ChatScroll > QWidget {{ background: transparent; }}
QScrollArea#ChatScroll > QWidget > QWidget {{ background: transparent; }}
QWidget#ChatBody {{ background: transparent; }}
QWidget#ChatRow {{ background: transparent; }}
QFrame#ChatBubble {{ background: rgba(255, 255, 255, 0.05);
    border: 1px solid rgba(255, 255, 255, 0.07); border-radius: {RADIUS_MD}px; }}
QFrame#ChatBubbleUser {{ background: rgba(34, 211, 238, 0.16);
    border: 1px solid rgba(34, 211, 238, 0.28); border-radius: {RADIUS_MD}px; }}
QFrame#ChatBubbleError {{ background: rgba(239, 68, 68, 0.10);
    border: 1px solid rgba(239, 68, 68, 0.30); border-radius: {RADIUS_MD}px; }}
QFrame#ChatBubbleInfo {{ background: rgba(255, 255, 255, 0.04);
    border: 1px solid {HAIRLINE}; border-radius: {RADIUS_MD}px; }}
QLabel#ChatRole {{ color: {TEXT_MUTED}; background: transparent;
    font-size: 9px; font-weight: 600; }}
QLabel#ChatText {{ color: {TEXT_COLOR}; background: transparent; font-size: 11px; }}
QLabel#ChatCode {{ color: {TEXT_SECONDARY}; background: {DARK_GREY};
    border: 1px solid {HAIRLINE}; border-radius: {RADIUS_SM}px;
    font-family: Consolas; font-size: 11px; padding: 6px 8px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {BLOCK_HOVER}; border-radius: 5px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {ACCENT_COLOR}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QTreeWidget::item {{ padding: 3px 4px; }}
QTreeWidget::item:selected {{ background-color: {ACCENT_COLOR}; color: {BTN_PRIMARY_TEXT}; }}
"""
