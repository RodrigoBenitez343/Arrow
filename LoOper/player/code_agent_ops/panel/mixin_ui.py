"""CodeNodePanel part: window + UI construction + instance state.

Split into player/code_agent_ops/panel so each concern stays small;
NGUI/widgets/code_node_panel.py is the entry point and re-exports this
class.  App root = the LoOper/ directory (top-level packages NGUI/AI/player).
"""
import logging

from PyQt5.QtCore import Qt, QSize
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)

from NGUI.constants import DANGER_COLOR
from NGUI.i18n import _
from NGUI.icons import tabler_qicon
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
        # A plain QWidget subclass only paints its QSS background with this
        # attribute set; without it #CodeStudioRoot stayed transparent and the
        # lighter graph plane showed through empty areas (the chat column).
        self.setAttribute(Qt.WA_StyledBackground, True)
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
        self._chat_stop_ref = None  # Stop flag shared with stream workers
        self._last_run_record = None
        self._pending_run = False      # True while an agent auto-run is in flight

        # ---- agent loop state (flat, per-request) ----
        self._chat_history = []        # bounded ring of prior exchanges
        self._request_goal = None
        self._request_is_question = False   # Laya's verdict for this request
        self._intent_worker = None     # Laya intent classification thread
        self._rounds = 0               # applied main-code passes in this request
        self._tool_turns = 0           # investigation / corrective turns
        self._loop_aborted = False
        self._await_main = False       # next reply must edit the main script
        self._last_sent = None         # instruction of the turn in flight
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
        self._stream_started = False
        self._stream_timer = None
        self._live_frame = None        # in-flight assistant bubble (rewritten
        self._live_lbl = None          # in place as tokens arrive)
        self._pipeline_thread = None   # modular project pipeline (build requests)
        # Rendered chat log, replayed when the session is restored on a node
        # switch: ('chat', role, text) | ('assistant', text) |
        # ('run', title, ok, text)
        self._chat_log = []

        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _icon_btn(self, icon, tooltip, obj=None, color=None, icon_px=18,
                  btn_px=30):
        """Icon-only vector button (Tabler glyph) with a hover tooltip, the
        same compact idiom as the actions bar and chains-library tabs. No
        button in the studio carries text now, so glyphs stay crisp."""
        b = QPushButton()
        ic = tabler_qicon(icon, icon_px, color=color)
        if not ic.isNull():
            b.setIcon(ic)
            b.setIconSize(QSize(icon_px, icon_px))
        b.setToolTip(tooltip)
        b.setCursor(Qt.PointingHandCursor)
        b.setFixedSize(btn_px, btn_px)
        if obj:
            b.setObjectName(obj)
        return b

    def _build_ui(self):
        mono = QFont('Consolas', 9)
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)
        self.setObjectName('CodeStudioRoot')

        # ── One compact top bar: identity · engine/model · actions ──
        # The old two-row header (title card + a second engine/model/GGUF card)
        # read as stacked panels; it is now a single flat row. Config labels are
        # replaced by tooltips and the Ollama/llama.cpp model pickers swap in
        # place (see _apply_engine_visibility), so only one selector shows.
        bar = QHBoxLayout()
        bar.setSpacing(6)
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        self._title_lbl = QLabel(_('No Code node selected'))
        self._title_lbl.setObjectName('StudioTitle')
        self._sub_lbl = QLabel('')
        self._sub_lbl.setObjectName('StudioSub')
        title_col.addWidget(self._title_lbl)
        title_col.addWidget(self._sub_lbl)
        bar.addLayout(title_col, 1)

        self._engine_combo = QComboBox()
        try:
            from NGUI.dialogs.engine_options import fill_engine_combo
            fill_engine_combo(self._engine_combo)
        except Exception:
            self._engine_combo.addItems(['Ollama', 'llama.cpp'])
        self._engine_combo.setObjectName('StudioSelect')
        self._engine_combo.setFixedHeight(26)
        self._engine_combo.setFixedWidth(96)
        self._engine_combo.setToolTip(_('Inference engine'))
        self._engine_combo.currentTextChanged.connect(self._on_engine_changed)
        bar.addWidget(self._engine_combo)

        self._model_combo = QComboBox()
        self._model_combo.setObjectName('StudioSelect')
        self._model_combo.setEditable(True)
        self._model_combo.setFixedHeight(26)
        self._model_combo.setMinimumWidth(150)
        self._model_combo.setMaximumWidth(260)
        self._model_combo.setToolTip(_('Model'))
        bar.addWidget(self._model_combo)

        self._gguf_combo = QComboBox()
        self._gguf_combo.setObjectName('StudioSelect')
        self._gguf_combo.setEditable(True)
        self._gguf_combo.setFixedHeight(26)
        self._gguf_combo.setMinimumWidth(150)
        self._gguf_combo.setMaximumWidth(300)
        self._gguf_combo.setToolTip(_('GGUF model file'))
        bar.addWidget(self._gguf_combo)

        self._refresh_btn = self._icon_btn(
            'REFRESH', _('Refresh model list'), btn_px=28)
        self._refresh_btn.clicked.connect(self._refresh_models)
        bar.addWidget(self._refresh_btn)

        self._run_btn = self._icon_btn(
            'PLAYER_PLAY', _('Run the node against the last real '
                             'upstream data'), obj='primary', btn_px=32)
        self._run_btn.clicked.connect(self._on_run)
        bar.addWidget(self._run_btn)

        self._stop_btn = self._icon_btn(
            'PLAYER_STOP', _('Stop the agent / run'), obj='danger',
            color=DANGER_COLOR, btn_px=32)
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._on_stop)
        bar.addWidget(self._stop_btn)

        back_btn = self._icon_btn(
            'ARROW_BACK', _('Collapse the studio back to the graph'),
            btn_px=32)
        back_btn.clicked.connect(self._close_studio)
        bar.addWidget(back_btn)
        root.addLayout(bar)

        rule = QFrame()
        rule.setObjectName('StudioRule')
        rule.setFixedHeight(1)
        root.addWidget(rule)

        # ── Page stack (one pane visible at a time) ──
        self._stack = QStackedWidget()
        self._stack.setObjectName('StudioStack')
        root.addWidget(self._stack, 1)
        self._build_chat_page(mono)
        self._build_code_page(mono)
        self._build_terminal_page(mono)
        self._build_files_page()
        self._build_io_page()

        # ── Bottom tab strip (icon-only, like the chains library) ──
        tabs = QHBoxLayout()
        tabs.setContentsMargins(0, 0, 0, 0)
        tabs.setSpacing(4)
        self._pill_btns = {}
        tab_icons = {'chat': 'MESSAGE', 'code': 'CODE', 'terminal': 'TERMINAL',
                     'files': 'FOLDER', 'io': 'TRANSFER'}
        for key, label in self.PAGES:
            btn = self._icon_btn(tab_icons.get(key, 'CIRCLE'), _(label),
                                 obj='StudioTab', icon_px=20, btn_px=34)
            btn.setCheckable(True)
            btn.clicked.connect(lambda _c, k=key: self._switch_page(k))
            self._pill_btns[key] = btn
            tabs.addWidget(btn)
        tabs.addStretch(1)
        self._tok_lbl = QLabel('')
        self._tok_lbl.setObjectName('StudioSub')
        tabs.addWidget(self._tok_lbl)
        root.addLayout(tabs)

        self.setStyleSheet(_STUDIO_QSS)
        self._init_models()
        self._switch_page('chat')

    def _make_page(self):
        # A page is a plain container, not a bordered card: the studio root is
        # already one surface, and nesting StudioSection cards inside it was
        # the "too many panels" look.  The explicit object name keeps an ID
        # rule on the page so the host window's inherited QWidget background
        # (GRAPH_PLANE, lighter) can never bleed through.
        frame = QWidget()
        frame.setObjectName('StudioPage')
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(6)
        return frame, lay

    # ── Chat page ──
    def _build_chat_page(self, mono):
        page, lay = self._make_page()
        # A scrollable column of real widget bubbles (the agent-overlay
        # technique): QTextEdit's rich-text subset has no border-radius or
        # max-width, so its CSS "bubbles" never actually rendered.
        self._chat_scroll = QScrollArea()
        self._chat_scroll.setObjectName('ChatScroll')
        self._chat_scroll.setWidgetResizable(True)
        self._chat_scroll.setFrameShape(QFrame.NoFrame)
        self._chat_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName('ChatBody')
        self._chat_lay = QVBoxLayout(body)
        self._chat_lay.setContentsMargins(8, 8, 8, 8)
        self._chat_lay.setSpacing(6)
        self._chat_lay.setAlignment(Qt.AlignTop)
        self._chat_lay.addStretch(1)   # rows insert before this anchor
        self._chat_scroll.setWidget(body)
        lay.addWidget(self._chat_scroll, 1)

        input_row = QHBoxLayout()
        input_row.setSpacing(6)
        self._chat_input = QLineEdit()
        self._chat_input.setPlaceholderText(
            _('Tell the agent what to write or fix (Enter to send, /help)'))
        self._chat_input.returnPressed.connect(self._on_chat_send)
        input_row.addWidget(self._chat_input, 1)
        self._send_btn = self._icon_btn(
            'SEND', _('Send'), obj='primary', btn_px=32)
        self._send_btn.clicked.connect(self._on_chat_send)
        input_row.addWidget(self._send_btn)
        lay.addLayout(input_row)
        self._stack.addWidget(page)

    # ── Code page ──
    def _build_code_page(self, mono):
        page, lay = self._make_page()
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self._file_lbl = QLabel('')
        self._file_lbl.setObjectName('FileLabel')
        bar.addWidget(self._file_lbl, 1)
        save_btn = self._icon_btn(
            'CHECK', _('Save this file: helpers to disk, main file to the '
                       'node'), btn_px=30)
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
        bar.setSpacing(6)
        bar.addStretch(1)
        clear_btn = self._icon_btn('ERASER', _('Clear console'), btn_px=28)
        clear_btn.clicked.connect(lambda: self._console.clear())
        bar.addWidget(clear_btn)
        lay.addLayout(bar)

        self._console = QPlainTextEdit()
        self._console.setReadOnly(True)
        self._console.setMaximumBlockCount(2000)
        self._console.setFont(mono)
        lay.addWidget(self._console, 1)

        cmd_row = QHBoxLayout()
        cmd_row.setSpacing(6)
        self._term_input = QLineEdit()
        self._term_input.setPlaceholderText(
            _('$ command  (runs in the node folder; python/pip = node venv)'))
        self._term_input.returnPressed.connect(self._on_shell_send)
        cmd_row.addWidget(self._term_input, 1)
        shell_btn = self._icon_btn(
            'PLAYER_PLAY', _('Run command'), obj='primary', btn_px=32)
        shell_btn.clicked.connect(self._on_shell_send)
        cmd_row.addWidget(shell_btn)
        lay.addLayout(cmd_row)
        self._stack.addWidget(page)

    # ── Files page ──
    def _build_files_page(self):
        page, lay = self._make_page()
        bar = QHBoxLayout()
        bar.setSpacing(4)
        for icon, tip, slot in (
            ('FILE_PLUS', _('New file'), self._new_file),
            ('FOLDER_PLUS', _('New folder'), self._new_folder),
            ('FOLDER_OPEN', _('Open'), self._open_selected),
            ('PENCIL', _('Rename'), self._rename_selected),
            ('TRASH', _('Delete'), self._delete_selected),
            ('REFRESH', _('Refresh'), self._refresh_files),
        ):
            b = self._icon_btn(icon, tip, btn_px=28)
            b.clicked.connect(slot)
            bar.addWidget(b)
        bar.addStretch(1)
        hint = QLabel(_('script.py = entry script (executed)  \u00b7  '
                       'other files = project modules'))
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
        self._conn_view = QPlainTextEdit()
        self._conn_view.setReadOnly(True)
        self._conn_view.setMaximumHeight(64)
        self._conn_view.setPlaceholderText(
            _('Connected inputs (auto-injected as variables)'))
        lay.addWidget(self._conn_view)

        io_row = QHBoxLayout()
        io_row.setSpacing(6)
        self._in_vars_box = self._make_var_box(True)
        self._out_vars_box = self._make_var_box(False)
        io_row.addWidget(self._in_vars_box['frame'])
        io_row.addWidget(self._out_vars_box['frame'])
        lay.addLayout(io_row, 1)
        self._stack.addWidget(page)

    def _make_var_box(self, is_input):
        box = {}
        frame = QFrame()
        frame.setObjectName('StudioWell')
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(4)
        head = QHBoxLayout()
        title = _('Input variables') if is_input else _('Output variables')
        ttl = QLabel(title)
        ttl.setObjectName('StudioSub')
        head.addWidget(ttl, 1)
        add_btn = self._icon_btn(
            'PLUS', _('Add variable'), btn_px=24)
        add_btn.clicked.connect(lambda: self._add_var_row(is_input))
        head.addWidget(add_btn)
        lay.addLayout(head)
        rows_host = QVBoxLayout()
        rows_host.setSpacing(2)
        rows_host.addStretch(1)
        lay.addLayout(rows_host)
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
        type_combo.setObjectName('StudioSelect')
        type_combo.addItems(VAR_TYPES)
        type_combo.setCurrentText(vtype)
        remove_btn = self._icon_btn(
            'CIRCLE_X', _('Remove'), color=DANGER_COLOR, icon_px=16, btn_px=24)
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
        self._stack.setCurrentIndex(names.index(key))
        # Checked look comes from the QSS (#StudioTab:checked), so no per-call
        # inline stylesheet - the tab strip stays consistent with the library.
        for k, btn in self._pill_btns.items():
            btn.setChecked(k == key)
        if key == 'files':
            self._refresh_files()
