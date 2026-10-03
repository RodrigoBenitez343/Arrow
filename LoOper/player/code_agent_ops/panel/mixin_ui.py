"""CodeNodePanel part: window + UI construction + instance state.

Split into player/code_agent_ops/panel so each concern stays small;
NGUI/widgets/code_node_panel.py is the entry point and re-exports this
class.  App root = the LoOper/ directory (top-level packages NGUI/AI/player).
"""
import logging

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QTextEdit,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)

from NGUI.constants import (
    ACCENT_COLOR,
    BTN_PRIMARY_TEXT,
    CONTROL_BG,
    HAIRLINE,
    RADIUS_SM,
    TEXT_COLOR,
)
from NGUI.i18n import _
from player.code_agent_ops.constants import (
    MAIN_FILE,
    STEER_PENALTY_BASE,
    STEER_TEMP_BASE,
    VAR_TYPES,
    _STUDIO_QSS,
)

logger = logging.getLogger(__name__)


class UiMixin(object):
    """Chat-first, single-window editor for the selected Code node."""

    PAGES = [('chat', 'Chat'), ('code', 'Code'), ('terminal', 'Terminal'),
             ('files', 'Files'), ('io', 'IO')]

    def __init__(self, main_window=None):
        super().__init__()
        self._mw = main_window
        self._node = None
        self._config = {}
        self._connected_inputs = []
        self._loaded_node_id = None

        # ---- per-node agent sessions ----
        # ONE dock panel serves every Code node; each node owns its own
        # agent session (history + console + last run + tool results), saved
        # on switch-away and restored on return.  ``_node_token`` is bumped on
        # every switch; workers stamp the token of the node that started them
        # and their completion handlers drop (or route away) results whose
        # token no longer matches, so a late-finished thread can never mutate
        # the newly loaded node.  ``_orphan_threads`` keeps detached workers
        # alive until they finish so Qt never destroys a running QThread.
        self._sessions = {}
        self._node_token = 0
        self._orphan_threads = []

        # ---- threading / run ----
        self._run_thread = None
        self._chat_thread = None
        self._shell_thread = None
        self._stop_ref = None
        self._last_run_record = None
        self._pending_run = False      # True while an agent auto-run is in flight

        # ---- agent loop state (flat, per-request) ----
        self._chat_history = []        # bounded ring of prior exchanges
        self._request_goal = None
        self._rounds = 0               # applied main-code passes in this request
        self._tool_turns = 0           # investigation / corrective turns
        self._loop_aborted = False
        self._await_main = False       # next reply must edit the main script
        self._last_sent = None         # (instruction, show_protocol) in flight
        self._last_fail_kind = 'none'
        self._last_applied_main = None # dup detection across fix turns
        self._last_tool_results = None # last dev-tool output shown to the model
        self._invest_worker = None     # Needle 2 investigation thread
        self._steer = {'penalty': STEER_PENALTY_BASE,
                       'temp': STEER_TEMP_BASE, 'steers': 0,
                       'maxtok': 1.0, 'tries': 0}

        # ---- editor / IO ----
        self._in_rows = []
        self._out_rows = []
        self._active_file = MAIN_FILE
        self._main_code = ''

        # ---- live streaming ----
        self._stream_text = ''
        self._stream_start_pos = None
        self._stream_started = False
        self._stream_timer = None

        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        mono = QFont('Consolas', 9)
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)
        self.setObjectName('CodeStudioRoot')

        # ── Header: title, engine/model, run ──
        head = QFrame()
        head.setObjectName('StudioSection')
        hv = QVBoxLayout(head)
        hv.setContentsMargins(10, 6, 8, 4)
        hv.setSpacing(4)

        hl = QHBoxLayout()
        hl.setSpacing(8)
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        self._title_lbl = QLabel(_('No Code node selected'))
        self._title_lbl.setObjectName('StudioTitle')
        self._sub_lbl = QLabel('')
        self._sub_lbl.setObjectName('StudioSub')
        title_col.addWidget(self._title_lbl)
        title_col.addWidget(self._sub_lbl)
        hl.addLayout(title_col, 1)
        self._run_btn = QPushButton(_('Run'))
        self._run_btn.setObjectName('primary')
        self._run_btn.setToolTip(_('Run the node against the last real '
                                   'upstream data'))
        self._run_btn.clicked.connect(self._on_run)
        hl.addWidget(self._run_btn)
        self._stop_btn = QPushButton(_('Stop'))
        self._stop_btn.setObjectName('danger')
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._on_stop)
        hl.addWidget(self._stop_btn)
        back_btn = QPushButton('\u2190 ' + _('Graph'))
        back_btn.setToolTip(_('Collapse the studio back to the graph'))
        back_btn.clicked.connect(self._close_studio)
        hl.addWidget(back_btn)
        hv.addLayout(hl)

        # Row 2: engine + model (Ollama / llama.cpp) - the only config the
        # chat needs; the selected model is stored on the node when code is
        # applied.
        sel = QHBoxLayout()
        sel.setSpacing(6)
        self._engine_lbl = QLabel(_('Engine:'))
        sel.addWidget(self._engine_lbl)
        self._engine_combo = QComboBox()
        self._engine_combo.addItems(['Ollama', 'llama.cpp'])
        self._engine_combo.setFixedHeight(24)
        self._engine_combo.setFixedWidth(110)
        self._engine_combo.currentTextChanged.connect(self._on_engine_changed)
        sel.addWidget(self._engine_combo)
        self._model_lbl = QLabel(_('Model:'))
        sel.addWidget(self._model_lbl)
        self._model_combo = QComboBox()
        self._model_combo.setEditable(True)
        self._model_combo.setFixedHeight(24)
        self._model_combo.setMinimumWidth(180)
        sel.addWidget(self._model_combo)
        self._refresh_btn = QPushButton('\u21bb')
        self._refresh_btn.setToolTip(_('Refresh model list'))
        self._refresh_btn.setFixedSize(24, 24)
        self._refresh_btn.clicked.connect(self._refresh_models)
        sel.addWidget(self._refresh_btn)
        self._gguf_lbl = QLabel(_('GGUF:'))
        sel.addWidget(self._gguf_lbl)
        self._gguf_combo = QComboBox()
        self._gguf_combo.setEditable(True)
        self._gguf_combo.setFixedHeight(24)
        self._gguf_combo.setMinimumWidth(220)
        sel.addWidget(self._gguf_combo)
        sel.addStretch(1)
        hv.addLayout(sel)
        root.addWidget(head)

        # ── Page stack (one pane visible at a time) ──
        self._stack = QStackedWidget()
        root.addWidget(self._stack, 1)
        self._build_chat_page(mono)
        self._build_code_page(mono)
        self._build_terminal_page(mono)
        self._build_files_page()
        self._build_io_page()

        # ── Bottom pill tab strip ──
        pills = QHBoxLayout()
        pills.setContentsMargins(2, 2, 2, 2)
        pills.setSpacing(4)
        self._pill_btns = {}
        for key, label in self.PAGES:
            btn = QPushButton(_(label))
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(24)
            btn.clicked.connect(lambda _c, k=key: self._switch_page(k))
            self._pill_btns[key] = btn
            pills.addWidget(btn)
        pills.addStretch(1)
        self._tok_lbl = QLabel('')
        self._tok_lbl.setObjectName('StudioSub')
        pills.addWidget(self._tok_lbl)
        root.addLayout(pills)

        self.setStyleSheet(_STUDIO_QSS)
        self._init_models()
        self._switch_page('chat')

    def _make_page(self):
        frame = QFrame()
        frame.setObjectName('StudioSection')
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(4)
        return frame, lay

    # ── Chat page ──
    def _build_chat_page(self, mono):
        page, lay = self._make_page()
        self._chat_view = QTextEdit()
        self._chat_view.setReadOnly(True)
        try:
            self._chat_view.document().setMaximumBlockCount(600)
        except Exception:
            pass
        lay.addWidget(self._chat_view, 1)

        input_row = QHBoxLayout()
        self._chat_input = QLineEdit()
        self._chat_input.setPlaceholderText(
            _('Tell the agent what to write or fix (Enter to send, /help)'))
        self._chat_input.returnPressed.connect(self._on_chat_send)
        input_row.addWidget(self._chat_input, 1)
        self._send_btn = QPushButton(_('Send'))
        self._send_btn.setObjectName('primary')
        self._send_btn.clicked.connect(self._on_chat_send)
        input_row.addWidget(self._send_btn)
        lay.addLayout(input_row)
        self._stack.addWidget(page)

    # ── Code page ──
    def _build_code_page(self, mono):
        page, lay = self._make_page()
        bar = QHBoxLayout()
        self._file_lbl = QLabel('')
        self._file_lbl.setObjectName('FileLabel')
        bar.addWidget(self._file_lbl, 1)
        save_btn = QPushButton(_('Save file'))
        save_btn.setToolTip(_('Save this file: helpers to disk, main file to '
                              'the node'))
        save_btn.clicked.connect(self._save_open_file)
        bar.addWidget(save_btn)
        lay.addLayout(bar)

        self._code_edit = QPlainTextEdit()
        self._code_edit.setFont(mono)
        self._code_edit.setPlaceholderText(_('# Write your Python code here'))
        self._code_edit.textChanged.connect(self._on_editor_changed)
        lay.addWidget(self._code_edit, 1)
        self._stack.addWidget(page)

    # ── Terminal page ──
    def _build_terminal_page(self, mono):
        page, lay = self._make_page()
        bar = QHBoxLayout()
        lbl = QLabel(_('Run console / shell'))
        lbl.setObjectName('SectionLabel')
        bar.addWidget(lbl, 1)
        clear_btn = QPushButton(_('Clear'))
        clear_btn.clicked.connect(lambda: self._console.clear())
        bar.addWidget(clear_btn)
        lay.addLayout(bar)

        self._console = QPlainTextEdit()
        self._console.setReadOnly(True)
        self._console.setMaximumBlockCount(2000)
        self._console.setFont(mono)
        lay.addWidget(self._console, 1)

        cmd_row = QHBoxLayout()
        self._term_input = QLineEdit()
        self._term_input.setPlaceholderText(
            _('$ command  (runs in the node folder; python/pip = node venv)'))
        self._term_input.returnPressed.connect(self._on_shell_send)
        cmd_row.addWidget(self._term_input, 1)
        shell_btn = QPushButton(_('Run cmd'))
        shell_btn.setObjectName('primary')
        shell_btn.clicked.connect(self._on_shell_send)
        cmd_row.addWidget(shell_btn)
        lay.addLayout(cmd_row)
        self._stack.addWidget(page)

    # ── Files page ──
    def _build_files_page(self):
        page, lay = self._make_page()
        bar = QHBoxLayout()
        for text, slot in (
            ('+ File', self._new_file),
            ('+ Folder', self._new_folder),
            ('Open', self._open_selected),
            ('Rename', self._rename_selected),
            ('Delete', self._delete_selected),
            ('Refresh', self._refresh_files),
        ):
            b = QPushButton(_(text))
            b.setFixedHeight(22)
            b.clicked.connect(slot)
            bar.addWidget(b)
        bar.addStretch(1)
        hint = QLabel(_('script.py = executed main file'))
        hint.setObjectName('StudioSub')
        bar.addWidget(hint)
        lay.addLayout(bar)

        self._files_tree = QTreeWidget()
        self._files_tree.setHeaderHidden(True)
        self._files_tree.setColumnCount(1)
        self._files_tree.itemDoubleClicked.connect(
            lambda _it, _c: self._open_selected())
        lay.addWidget(self._files_tree, 1)
        self._stack.addWidget(page)

    # ── IO page ──
    def _build_io_page(self):
        page, lay = self._make_page()
        conn_lbl = QLabel(_('Connected inputs (auto-injected as variables)'))
        conn_lbl.setObjectName('SectionLabel')
        lay.addWidget(conn_lbl)
        self._conn_view = QPlainTextEdit()
        self._conn_view.setReadOnly(True)
        self._conn_view.setMaximumHeight(64)
        lay.addWidget(self._conn_view)

        io_row = QHBoxLayout()
        self._in_vars_box = self._make_var_box(True)
        self._out_vars_box = self._make_var_box(False)
        io_row.addWidget(self._in_vars_box['frame'])
        io_row.addWidget(self._out_vars_box['frame'])
        lay.addLayout(io_row, 1)
        self._stack.addWidget(page)

    def _make_var_box(self, is_input):
        box = {}
        frame = QFrame()
        frame.setObjectName('StudioSection')
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(4)
        title = _('Input variables') if is_input else _('Output variables')
        lay.addWidget(QLabel(title))
        rows_host = QVBoxLayout()
        rows_host.setSpacing(2)
        rows_host.addStretch(1)
        lay.addLayout(rows_host)
        add_btn = QPushButton(_('+ add'))
        add_btn.setFixedHeight(22)
        add_btn.clicked.connect(lambda: self._add_var_row(is_input))
        lay.addWidget(add_btn)
        box['frame'] = frame
        box['rows_host'] = rows_host
        if is_input:
            self._in_rows_host = rows_host
        else:
            self._out_rows_host = rows_host
        return box

    def _add_var_row(self, is_input, name='', vtype='string'):
        row = QFrame()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(4)
        name_field = QLineEdit(name)
        name_field.setPlaceholderText(_('variable name'))
        type_combo = QComboBox()
        type_combo.addItems(VAR_TYPES)
        type_combo.setCurrentText(vtype)
        remove_btn = QPushButton('\u00d7')
        remove_btn.setFixedWidth(22)
        remove_btn.clicked.connect(lambda: self._remove_var_row(row, is_input))
        rl.addWidget(name_field, 1)
        rl.addWidget(type_combo)
        rl.addWidget(remove_btn)
        host = self._in_rows_host if is_input else self._out_rows_host
        host.insertWidget(host.count() - 1, row)
        rows = self._in_rows if is_input else self._out_rows
        rows.append((name_field, type_combo))

    def _remove_var_row(self, row, is_input):
        rows = self._in_rows if is_input else self._out_rows
        for i, (nf, _tc) in enumerate(rows):
            try:
                if nf.parent() is row:
                    rows.pop(i)
                    break
            except Exception:
                continue
        host = self._in_rows_host if is_input else self._out_rows_host
        try:
            host.removeWidget(row)
            row.deleteLater()
        except Exception:
            pass

    def _collect_rows(self, rows):
        out = []
        for name_field, type_combo in rows:
            try:
                n = name_field.text().strip()
            except Exception:
                n = ''
            if n:
                out.append({'name': n, 'type': type_combo.currentText()})
        return out

    def _switch_page(self, key):
        names = [k for k, _l in self.PAGES]
        if key not in names:
            key = 'chat'
        idx = names.index(key)
        self._stack.setCurrentIndex(idx)
        for k, btn in self._pill_btns.items():
            active = k == key
            btn.setChecked(active)
            btn.setProperty('checked', active)
            btn.setStyleSheet(
                f'QPushButton {{ background-color: '
                f"{ACCENT_COLOR if active else CONTROL_BG}; "
                f'color: {BTN_PRIMARY_TEXT if active else TEXT_COLOR}; '
                f'border-radius: {RADIUS_SM}px; padding: 0px 14px; font-size: 11px; '
                f"font-weight: 600; border: 1px solid "
                f"{'transparent' if active else HAIRLINE}; }}"
            )
        if key == 'files':
            self._refresh_files()
