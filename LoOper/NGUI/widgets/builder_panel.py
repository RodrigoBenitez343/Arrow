"""builder_panel.py — the chat that scaffolds system chains in the canvas.

A side-docked panel in the agent-mode visual language (same surface,
bubbles, header and buttons as AgentOverlay): the chat sits on the RIGHT
of the window while the chain canvas stays visible and editable on the
left — the user watches the bot take shape and can edit it directly.

The builder is a MODE on the canvas, not a node: it creates Arrow's existing
nodes, wires them, edits their properties, asks the user to teach steps via
the recorders, and saves the result as a collection="System" chain.
"""

import logging
import os
import threading
import time

from PyQt5.QtCore import QObject, Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..constants import (
    ACCENT_COLOR,
    ACCENT_HOVER,
    BTN_PRIMARY_TEXT,
    DANGER_COLOR,
    FONT_FAMILY,
    HAIRLINE,
    RADIUS_LG,
    RADIUS_PILL,
    RADIUS_SM,
    TEXT_COLOR,
    TEXT_MUTED,
    WELL_BG,
)
from ..i18n import _
from .agent_overlay import (
    AGENT_BUBBLE_BG,
    AGENT_BUBBLE_BORDER,
    BG_RGBA,
    HEADER_RGBA,
    USER_BUBBLE_BG,
    USER_BUBBLE_BORDER,
)
from .hover_button import HoverGlowButton

logger = logging.getLogger(__name__)

#: Same width as the agent overlay's chat column, so the panel reads as the
#: same surface family.
PANEL_WIDTH = 500


class _Spinner(QWidget):
    """A small rotating arc that signals work in progress.

    Procedural (a QTimer advances the angle), font-independent and themed
    with the app accent — the same pattern as the overlay's SpectrogramBar.
    """

    def __init__(self, parent=None, size=14, color=ACCENT_COLOR, width=2):
        super().__init__(parent)
        self._color = color
        self._width = width
        self._angle = 0
        self.setFixedSize(size, size)
        self._timer = QTimer(self)
        self._timer.setInterval(90)
        self._timer.timeout.connect(self._tick)

    def start(self):
        if not self._timer.isActive():
            self._timer.start()

    def stop(self):
        self._timer.stop()
        self._angle = 0
        self.update()

    def _tick(self):
        self._angle = (self._angle + 30) % 360
        self.update()

    def paintEvent(self, event):
        try:
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            pen = QPen(QColor(self._color))
            pen.setWidth(self._width)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            r = self.rect().adjusted(self._width, self._width,
                                     -self._width, -self._width)
            p.drawArc(r, (90 - self._angle) * 16, 270 * 16)
        except Exception:
            pass


class _ChatCard:
    """Handle for an interactive card in the chat (buttons + status)."""

    def __init__(self, frame, edit, buttons, status):
        self.frame = frame
        self.edit = edit
        self.buttons = buttons
        self.status = status
        self.clicked = False

    def disable(self):
        for btn in self.buttons.values():
            try:
                btn.setEnabled(False)
            except Exception:
                pass

    def set_status(self, text):
        try:
            self.status.setText(str(text or ''))
            self.status.setVisible(bool(text))
        except Exception:
            pass


class _GuiProxy(QObject):
    """Marshal one callable onto the GUI thread and wait for its result.

    Canvas operations (NodeGraphQt) are NOT thread-safe; the worker thread
    blocks on a queued connection while the GUI thread executes the callable.
    """

    _job = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self._job.connect(self._run, Qt.BlockingQueuedConnection)

    def _run(self, job):
        fn, res, err = job
        try:
            res[0] = fn()
        except Exception as e:  # surfaced to the worker, never the loop
            err[0] = e

    def call(self, fn):
        # Already on the proxy's own thread (the GUI thread): a blocking
        # queued emit to itself trips Qt's deadlock detector ("Dead lock
        # detected while activating a BlockingQueuedConnection") and never
        # fills the result.  Run it inline instead — the canvas calls that
        # reach here from the GUI thread (_make_ctx's chains_dir, the turn's
        # start snapshot, _last_recorded_path) already touch widgets safely.
        if QThread.currentThread() is self.thread():
            return fn()
        res, err = [None], [None]
        self._job.emit((fn, res, err))
        if err[0] is not None:
            raise err[0]
        return res[0]


class _BuilderWorker(QThread):
    """Run ONE build turn: needle session (or legacy markers) + tool exec."""

    finished_turn = pyqtSignal(str)
    status = pyqtSignal(str)
    trace = pyqtSignal(str)

    def __init__(self, ctx, query, router, stop_event, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._query = query
        self._router = router
        self._stop = stop_event

    def _note(self, event):
        """Router progress ping -> queued to the GUI thread (thread-safe)."""
        try:
            self.status.emit(str(event))
        except Exception:
            pass

    def _trace(self, message):
        """A builder trace line -> queued to the GUI thread (thread-safe)."""
        try:
            self.trace.emit(str(message))
        except Exception:
            pass

    def run(self):
        try:
            if self._router is not None:
                self._note('thinking')
                payload = self._router.route(
                    self._query, progress_cb=self._note, trace=self._trace)
                text = str(payload.get('text') or '').strip()
                if not payload.get('ok'):
                    text = text or '(needle error: %s)' % payload.get('error')
                if not text:
                    text = '(the builder produced no output this turn)'
                self.finished_turn.emit(text)
                return
            self.finished_turn.emit(self._legacy_turn())
        except Exception as e:
            logger.exception('builder turn failed')
            self.finished_turn.emit('(builder turn failed: %s)' % e)

    def _legacy_turn(self):
        """needle.exe missing: strict marker directives only."""
        from player.builder.devtools import (
            execute_builder_call,
            parse_legacy_directives,
        )
        directives = parse_legacy_directives(self._query)
        if not directives:
            return (
                '(Needle 2 engine not found and no ### BUILDER: directives '
                'were given. Supported fallback lines: create_node, wire, '
                'edit — see the builder help.)'
            )
        out = []
        for name, args in directives:
            out.append(execute_builder_call(name, args, self._ctx))
        return '\n\n'.join(out)


class BuilderPanel(QWidget):
    """Side-docked builder chat (agent-mode look; canvas stays visible)."""

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self._gui = _GuiProxy()
        self._state = {}          # shared mutable turn state (description...)
        self._pos_counter = 0
        self._stop_event = threading.Event()
        self._worker = None
        self._pending_ask = None  # (kind, event, answer-holder)
        # Recent exchanges handed to the assistant each turn (its memory).
        self._history = []
        # Processing indicator state (spinner + live step text).
        self._status_phrases = []
        self._status_index = 0
        self._status_until = 0.0
        # Record card state: the node-attached recorder runs while the
        # worker waits in its ask; this timer advances the card when the
        # recording file lands.
        self._record_kind = ''
        self._record_node_id = ''
        self._record_card = None
        self._record_hold = None
        self._record_timer = QTimer(self)
        self._record_timer.setInterval(700)
        self._record_timer.timeout.connect(self._poll_recording)

        self.setFixedWidth(PANEL_WIDTH)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self._build_ui()

    # ------------------------------------------------------------- UI

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(0)

        # The card floats over the canvas like the agent overlay: same
        # surface, same radius, hairline border.
        self._card = QFrame(self)
        self._card.setStyleSheet(
            "QFrame#builderCard {"
            f" background: {BG_RGBA};"
            f" border: 1px solid {HAIRLINE};"
            f" border-radius: {RADIUS_LG}px;"
            "}"
            "QFrame#builderCard QLabel { background: transparent; }"
        )
        self._card.setObjectName("builderCard")
        layout.addWidget(self._card, 1)

        card = QVBoxLayout(self._card)
        card.setContentsMargins(0, 0, 0, 0)
        card.setSpacing(0)

        # Header (same surface language as the overlay header).
        header = QFrame(self._card)
        header.setStyleSheet(
            "QFrame#builderHeader {"
            f" background: {HEADER_RGBA};"
            f" border-top-left-radius: {RADIUS_LG}px;"
            f" border-top-right-radius: {RADIUS_LG}px;"
            "}"
        )
        header.setObjectName("builderHeader")
        header.setFixedHeight(44)
        hlay = QHBoxLayout(header)
        hlay.setContentsMargins(14, 6, 8, 6)
        hlay.setSpacing(8)
        title = QLabel("Builder", header)
        title.setStyleSheet(
            f"color: {TEXT_COLOR}; font-weight: 600; font-size: 13px;")
        hlay.addWidget(title)
        subtitle = QLabel(_("scaffolds the canvas"), header)
        subtitle.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 11px;")
        hlay.addWidget(subtitle)
        hlay.addStretch(1)
        self._settings_btn = HoverGlowButton(
            "ADJUSTMENTS", size=32, icon=14, parent=header)
        self._settings_btn.setToolTip(_("Builder settings"))
        self._settings_btn.clicked.connect(self._toggle_settings)
        hlay.addWidget(self._settings_btn)
        self._stop_btn = HoverGlowButton(
            "PLAYER_STOP", size=32, icon=14, parent=header)
        self._stop_btn.setToolTip(_("Stop"))
        self._stop_btn.clicked.connect(self._on_stop)
        hlay.addWidget(self._stop_btn)
        card.addWidget(header)

        # Settings subpanel (hidden until the gear is clicked): the model and
        # engine the builder reasons with.
        self._settings_row = self._build_settings(self._card)
        card.addWidget(self._settings_row)

        # Chat: scrollable bubble column (same bubble colors as agent mode).
        self._chat_scroll = QScrollArea(self._card)
        self._chat_scroll.setWidgetResizable(True)
        self._chat_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._chat_scroll.setFrameShape(QFrame.NoFrame)
        self._chat_scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }"
        )
        chat_host = QWidget()
        self._chat_layout = QVBoxLayout(chat_host)
        self._chat_layout.setContentsMargins(12, 10, 12, 10)
        self._chat_layout.setSpacing(8)
        self._chat_layout.setAlignment(Qt.AlignTop)
        self._chat_scroll.setWidget(chat_host)
        card.addWidget(self._chat_scroll, 1)

        # Processing status: spinner + live step text (hidden when idle).
        self._status_row = QFrame(self._card)
        self._status_row.setStyleSheet("QFrame { background: transparent; }")
        srow = QHBoxLayout(self._status_row)
        srow.setContentsMargins(14, 2, 14, 4)
        srow.setSpacing(8)
        self._spinner = _Spinner(self._status_row)
        srow.addWidget(self._spinner, 0, Qt.AlignVCenter)
        self._status_label = QLabel('', self._status_row)
        self._status_label.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 11px;"
            f" font-family: {FONT_FAMILY};")
        srow.addWidget(self._status_label, 1)
        card.addWidget(self._status_row)
        self._status_row.setVisible(False)
        # Generic phase rotation that keeps the text moving between the
        # router's real step events (see _advance_phase).
        self._phase_timer = QTimer(self)
        self._phase_timer.setInterval(1600)
        self._phase_timer.timeout.connect(self._advance_phase)

        # Input bar (well background + pill focus, like the overlay input).
        bar = QFrame(self._card)
        bar.setStyleSheet("QFrame { background: transparent; }")
        blay = QHBoxLayout(bar)
        blay.setContentsMargins(12, 8, 12, 12)
        blay.setSpacing(8)
        self._input = QLineEdit(bar)
        self._input.setPlaceholderText(
            _("Ask about the chain, or describe what to build..."))
        self._input.setStyleSheet(
            f"QLineEdit {{"
            f" background: {WELL_BG}; color: {TEXT_COLOR};"
            f" border: 1px solid {HAIRLINE};"
            f" border-radius: {RADIUS_PILL}px;"
            f" padding: 8px 12px; font-size: 12px;"
            f" font-family: {FONT_FAMILY};"
            f"}}"
            f"QLineEdit:focus {{ border-color: {ACCENT_COLOR}; }}"
        )
        self._input.returnPressed.connect(self._on_send)
        blay.addWidget(self._input, 1)
        self._send_btn = HoverGlowButton(
            "SEND", size=32, icon=14, parent=bar)
        self._send_btn.setToolTip(_("Send"))
        self._send_btn.clicked.connect(self._on_send)
        blay.addWidget(self._send_btn)
        card.addWidget(bar)

        self._append('system',
                     _("Ask me about this chain or any node — select nodes "
                       "to focus me, and tell me to prompt, configure or "
                       "fill them. Ask for a skill and I build the ladder: "
                       "input → steps → output with a shared context pool."))

    # ------------------------------------------------------------- settings

    def _settings_label(self, text):
        label = QLabel(text)
        label.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 11px;"
            f" font-family: {FONT_FAMILY};")
        return label

    def _build_settings(self, parent):
        """The builder's model / engine subpanel (hidden until toggled)."""
        from player.builder import settings as bsettings
        cfg = bsettings.load()
        row = QFrame(parent)
        row.setStyleSheet("QFrame { background: transparent; }")
        grid = QGridLayout(row)
        grid.setContentsMargins(14, 4, 14, 6)
        grid.setSpacing(6)
        combo_style = (
            f"QComboBox, QSpinBox {{ background: {WELL_BG};"
            f" color: {TEXT_COLOR}; border: 1px solid {HAIRLINE};"
            f" border-radius: {RADIUS_SM}px; padding: 3px 6px;"
            f" font-size: 11px; font-family: {FONT_FAMILY}; }}"
            f"QCheckBox {{ color: {TEXT_COLOR}; font-size: 11px;"
            f" font-family: {FONT_FAMILY}; }}"
        )
        row.setStyleSheet("QFrame#builderSettings {"
                          " background: transparent; }")
        row.setObjectName("builderSettings")

        grid.addWidget(self._settings_label(_("Engine")), 0, 0)
        self._engine_combo = QComboBox(row)
        self._engine_combo.setStyleSheet(combo_style)
        # The shared engine list: llama.cpp, Ollama, every BYOK provider
        # (pool listed disabled).  Fallback keeps the legacy pair.
        try:
            from NGUI.dialogs.engine_options import engine_items
            _engines = [(eid, label) for eid, label, _en in engine_items()]
        except Exception:
            _engines = list(bsettings.ENGINES)
        for engine_id, label in _engines:
            self._engine_combo.addItem(label, engine_id)
        index = self._engine_combo.findData(cfg.get('engine'))
        if index >= 0:
            self._engine_combo.setCurrentIndex(index)
        grid.addWidget(self._engine_combo, 0, 1, 1, 2)

        grid.addWidget(self._settings_label(_("Model")), 1, 0)
        self._model_combo = QComboBox(row)
        self._model_combo.setStyleSheet(combo_style)
        self._model_combo.setEditable(True)
        self._model_combo.setInsertPolicy(QComboBox.NoInsert)
        model = str(cfg.get('model') or '')
        if model:
            self._model_combo.addItem(model)
        self._model_combo.setCurrentText(model)
        grid.addWidget(self._model_combo, 1, 1)
        refresh = QPushButton(_("Refresh"), row)
        refresh.setToolTip(_("List the models the local gateway serves"))
        refresh.clicked.connect(self._refresh_models)
        grid.addWidget(refresh, 1, 2)

        self._reasoning_check = QCheckBox(_("Reasoning LLM"), row)
        self._reasoning_check.setChecked(bool(cfg.get('reasoning', True)))
        self._reasoning_check.setToolTip(_(
            "Ask the model for the next step before Laya picks the action"))
        grid.addWidget(self._reasoning_check, 2, 0, 1, 3)

        grid.addWidget(self._settings_label(_("Max steps")), 3, 0)
        self._steps_spin = QSpinBox(row)
        self._steps_spin.setStyleSheet(combo_style)
        self._steps_spin.setRange(1, 20)
        self._steps_spin.setValue(int(cfg.get('max_steps') or 6))
        grid.addWidget(self._steps_spin, 3, 1, 1, 2)

        # Connect only after the widgets hold their persisted values, so
        # populating the panel never writes the settings back.
        self._engine_combo.currentIndexChanged.connect(self._on_engine_changed)
        self._model_combo.activated.connect(
            lambda _i: self._on_model_changed(self._model_combo.currentText()))
        if self._model_combo.lineEdit() is not None:
            self._model_combo.lineEdit().editingFinished.connect(
                lambda: self._on_model_changed(self._model_combo.currentText()))
        self._reasoning_check.toggled.connect(self._on_reasoning_toggled)
        self._steps_spin.valueChanged.connect(self._on_steps_changed)

        row.setVisible(False)
        return row

    def _toggle_settings(self):
        self._settings_row.setVisible(self._settings_row.isHidden())

    def _save_settings(self, values):
        from player.builder import settings as bsettings
        bsettings.save(values)

    def _on_engine_changed(self, _index):
        self._save_settings({'engine': self._engine_combo.currentData()})

    def _on_model_changed(self, text):
        self._save_settings({'model': str(text or '').strip()})

    def _on_reasoning_toggled(self, checked):
        self._save_settings({'reasoning': bool(checked)})

    def _on_steps_changed(self, value):
        self._save_settings({'max_steps': int(value)})

    def _refresh_models(self):
        from player.builder import reasoning as breasoning
        names = breasoning.list_models()
        current = (self._model_combo.currentText() or '').strip()
        self._model_combo.clear()
        for name in names:
            self._model_combo.addItem(name)
        if current:
            self._model_combo.setCurrentText(current)
        self._append('tool', _("models: %s")
                     % (', '.join(names) or _('none listed')))

    # ------------------------------------------------------------- chat

    def _append_bubble(self, text, is_user, muted=False, danger=False):
        bubble = QLabel(text)
        bubble.setWordWrap(True)
        bubble.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bubble.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        color = DANGER_COLOR if danger else (TEXT_MUTED if muted else TEXT_COLOR)
        # The prompt and the answer share one bubble layout — only the tint
        # differs.  The old user branch built a style but never applied it and
        # pushed the label right inside a stretched row, so sent prompts showed
        # as bare text at the right edge while answers filled the frame.
        bg, border = ((USER_BUBBLE_BG, USER_BUBBLE_BORDER) if is_user
                      else (AGENT_BUBBLE_BG, AGENT_BUBBLE_BORDER))
        bubble.setStyleSheet(
            f"QLabel {{ background: {bg};"
            f" border: 1px solid {border};"
            f" color: {color}; border-radius: {RADIUS_SM}px;"
            f" padding: 8px 10px; font-size: 12px;"
            f" font-family: {FONT_FAMILY}; }}"
        )
        self._chat_layout.addWidget(bubble)
        self._scroll_to_bottom()

    def _scroll_to_bottom(self):
        def _scroll():
            bar = self._chat_scroll.verticalScrollBar()
            bar.setValue(bar.maximum())
        QTimer.singleShot(0, _scroll)

    def _append_card(self, title, body='', options=None, on_choice=None,
                     editable=None, editable_hint=''):
        """An interactive card in the chat: title, body, an optional
        description field and option buttons.

        Clicking one option calls ``on_choice(value, card)`` once and
        disables the row (the handler may update the status line).  Returns
        the ``_ChatCard`` handle.
        """
        frame = QFrame()
        frame.setObjectName('chatCard')
        frame.setStyleSheet(
            "QFrame#chatCard {"
            f" background: {AGENT_BUBBLE_BG};"
            f" border: 1px solid {AGENT_BUBBLE_BORDER};"
            f" border-radius: {RADIUS_SM}px;"
            "}"
            "QFrame#chatCard QLabel { background: transparent; }"
        )
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(6)
        heading = QLabel(str(title))
        heading.setWordWrap(True)
        heading.setStyleSheet(
            f"color: {TEXT_COLOR}; font-size: 12px; font-weight: 600;"
            f" font-family: {FONT_FAMILY};")
        lay.addWidget(heading)
        if body:
            description = QLabel(str(body))
            description.setWordWrap(True)
            description.setStyleSheet(
                f"color: {TEXT_MUTED}; font-size: 11px;"
                f" font-family: {FONT_FAMILY};")
            lay.addWidget(description)
        edit = None
        if editable is not None:
            edit = QLineEdit(frame)
            edit.setText(str(editable))
            if editable_hint:
                edit.setPlaceholderText(str(editable_hint))
                edit.setToolTip(str(editable_hint))
            edit.setStyleSheet(
                f"QLineEdit {{ background: {WELL_BG}; color: {TEXT_COLOR};"
                f" border: 1px solid {HAIRLINE};"
                f" border-radius: {RADIUS_SM}px; padding: 5px 8px;"
                f" font-size: 11px; font-family: {FONT_FAMILY}; }}"
                f"QLineEdit:focus {{ border-color: {ACCENT_COLOR}; }}")
            lay.addWidget(edit)
        status = QLabel('')
        status.setWordWrap(True)
        status.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 11px;"
            f" font-family: {FONT_FAMILY};")
        status.setVisible(False)
        row = QHBoxLayout()
        row.setSpacing(6)
        buttons = {}
        card = _ChatCard(frame, edit, buttons, status)

        def _click(value):
            if card.clicked:
                return
            card.clicked = True
            card.disable()
            if callable(on_choice):
                try:
                    on_choice(value, card)
                except Exception:
                    logger.exception('builder card action failed')

        for label, value, primary in (options or []):
            btn = QPushButton(str(label), frame)
            btn.setCursor(Qt.PointingHandCursor)
            if primary:
                btn.setStyleSheet(
                    f"QPushButton {{ background: {ACCENT_COLOR};"
                    f" color: {BTN_PRIMARY_TEXT}; border: none;"
                    f" border-radius: {RADIUS_SM}px; padding: 4px 12px;"
                    f" font-size: 11px; font-weight: 600;"
                    f" font-family: {FONT_FAMILY}; }}"
                    f"QPushButton:hover {{ background: {ACCENT_HOVER}; }}"
                    f"QPushButton:disabled {{ background: {WELL_BG};"
                    f" color: {TEXT_MUTED}; }}")
            else:
                btn.setStyleSheet(
                    f"QPushButton {{ background: {WELL_BG};"
                    f" color: {TEXT_COLOR}; border: 1px solid {HAIRLINE};"
                    f" border-radius: {RADIUS_SM}px; padding: 4px 10px;"
                    f" font-size: 11px; font-family: {FONT_FAMILY}; }}"
                    f"QPushButton:hover {{ border-color: {ACCENT_COLOR}; }}"
                    f"QPushButton:disabled {{ color: {TEXT_MUTED}; }}")
            btn.clicked.connect(lambda _=False, v=value: _click(v))
            buttons[value] = btn
            row.addWidget(btn)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(status)
        self._chat_layout.addWidget(frame)
        self._scroll_to_bottom()
        return card

    def _append(self, kind, text):
        if kind == 'user':
            self._append_bubble(text, is_user=True)
        elif kind == 'tool':
            self._append_bubble(text, is_user=False, muted=True)
        elif kind == 'error':
            self._append_bubble(text, is_user=False, danger=True)
        else:
            self._append_bubble(text, is_user=False)

    def _on_toggle(self, checked):
        self._append('system', _("Builder mode is ON — describe the bot.") if
                     checked else
                     _("Builder mode is OFF — the panel will not scaffold."))

    # ------------------------------------------------- processing status

    def _phrases(self):
        return [
            _("Reading the graph…"),
            _("Reasoning the outcome…"),
            _("Analyzing approaches…"),
            _("Checking the canvas…"),
        ]

    def _start_busy(self, text=None):
        """Show the spinner + live status row for the duration of a turn."""
        self._status_phrases = self._phrases()
        self._status_index = 0
        self._status_until = 0.0
        self._status_row.setVisible(True)
        self._spinner.start()
        self._set_status(text or self._status_phrases[0], sticky=False)
        self._phase_timer.start()

    def _stop_busy(self):
        self._phase_timer.stop()
        self._spinner.stop()
        self._status_row.setVisible(False)

    def _set_status(self, text, sticky=True):
        self._status_label.setText(str(text))
        # A real step stays readable for a moment before the generic rotation
        # resumes, so updates are seen rather than instantly overwritten.
        self._status_until = time.monotonic() + 1.5 if sticky else 0.0

    def _advance_phase(self):
        if self._status_row.isHidden() or not self._status_phrases:
            return
        if time.monotonic() < self._status_until:
            return
        self._status_index = (self._status_index + 1) % len(self._status_phrases)
        self._status_label.setText(self._status_phrases[self._status_index])

    def _tool_phrase(self, name):
        return {
            'read_graph': _("Reading the graph…"),
            'create_node': _("Placing a node…"),
            'wire': _("Wiring the nodes…"),
            'edit_node': _("Editing node properties…"),
            'set_description': _("Writing the chain description…"),
            'save_chain': _("Saving the system chain…"),
            'ask_user': _("Waiting for your answer…"),
            'read_node': _("Reading the node…"),
            'build_skill': _("Building the skill ladder…"),
        }.get(name, _("Working…"))

    def _on_status(self, event):
        """Router progress ping (GUI thread): update the live step text."""
        ev = str(event or '')
        if ev.startswith('tool:'):
            self._set_status(self._tool_phrase(ev[5:]), sticky=True)
        elif ev == 'thinking':
            self._set_status(_("Reasoning the outcome…"), sticky=True)

    def _on_trace(self, message):
        """A builder trace line (goal, Laya verdict, stored args, needle
        reasoning, question) — shown dimmed so the build stays legible."""
        text = str(message or '')
        if text:
            self._append('tool', text)

    def _on_stop(self):
        self._stop_event.set()
        self._stop_busy()
        self._record_timer.stop()
        self._record_hold = None
        if self._pending_ask is not None:
            kind, evt, holder = self._pending_ask
            holder[0] = ''
            evt.set()

    def _on_send(self):
        text = self._input.text().strip()
        self._input.clear()
        if not text:
            return
        # A pending ask consumes the line as the answer.
        if self._pending_ask is not None:
            kind, evt, holder = self._pending_ask
            holder[0] = text
            evt.set()
            return
        self._append('user', text)
        self._remember('user', text)
        self._start_turn(text)

    # ------------------------------------------------------------- turn

    def _make_ctx(self):
        """The build-side ctx: every canvas access marshals to the GUI thread."""
        graph_view = self.main_window.graph_view

        def _graph_manager():
            return graph_view.graph_manager

        def _config_manager():
            return graph_view.config_manager

        def _chains_dir():
            return str(graph_view.chains_folder or '')

        def _selection_text():
            """The user's selected nodes as a readable detail block."""
            try:
                nodes = list(graph_view.node_graph.selected_nodes() or [])
            except Exception:
                nodes = []
            if not nodes:
                return ''
            try:
                from player.builder.devtools import describe_node
            except Exception:
                return ''
            blocks = []
            for node in nodes[:3]:
                try:
                    blocks.append(describe_node(node))
                except Exception:
                    continue
            return '\n\n'.join(blocks)

        return {
            'graph_manager': lambda: self._gui.call(_graph_manager),
            'config_manager': lambda: self._gui.call(_config_manager),
            'chains_dir': self._gui.call(_chains_dir),
            'state': self._state,
            'ask': self._ask,
            'position_hint': self._position_hint,
            # Every canvas OP (not just the manager getter) runs on the GUI
            # thread — node and port objects included.  ask_user is the one
            # exception the executor makes: it blocks on the user.
            'run_on_gui': self._gui.call,
            'stop': self._stop_event.is_set,
            # The assistant's turn context: the nodes the user selected
            # (its focus) and the recent exchange tail (its memory).
            'selection_text': lambda: self._gui.call(_selection_text),
            'history': list(self._history),
        }

    def _position_hint(self):
        self._pos_counter += 1
        col = (self._pos_counter - 1) % 4
        row = (self._pos_counter - 1) // 4
        return [40.0 + col * 320.0, 40.0 + row * 220.0]

    def _start_turn(self, message):
        if self._worker is not None and self._worker.isRunning():
            self._append('system', _("Still building — wait for the current "
                                     "turn to finish."))
            return
        self._stop_event.clear()
        self._start_busy(_("Reading the graph…"))
        ctx = self._make_ctx()
        # The assistant reads the canvas + the user's selection itself, so
        # a turn is just the raw message.
        router = self._make_router(ctx)
        self._worker = _BuilderWorker(
            ctx, message, router, self._stop_event, parent=self)
        self._worker.finished_turn.connect(self._on_turn_finished)
        self._worker.status.connect(self._on_status)
        self._worker.trace.connect(self._on_trace)
        self._worker.start()

    def _make_router(self, ctx):
        """The panel's brain is the building assistant — the composer and
        the teach flow stay importable modules, off this path by design."""
        try:
            from player.builder.assistant import BuilderAssistant
            return BuilderAssistant(ctx)
        except Exception:
            return None

    def _on_turn_finished(self, text):
        self._remember('assistant', text)
        for line in str(text or '').splitlines():
            if not line.strip():
                continue
            self._append('tool' if line.startswith(('created', 'wired',
                                                    'set ', 'saved',
                                                    'built', 'Canvas',
                                                    'user said',
                                                    'bound ')) else
                         ('error' if line.startswith('(') else 'system'),
                         line)
        self._stop_event.set()
        self._stop_busy()

    def _remember(self, role, text):
        """Keep the last exchanges for the assistant's turn context."""
        try:
            self._history.append({'role': str(role),
                                  'text': str(text or '')[:800]})
            del self._history[:-10]
        except Exception:
            pass

    # ------------------------------------------------------------- ask

    def _ask(self, question, kind):
        """Block the worker until the user answers (chat line or recording)."""
        evt = threading.Event()
        holder = [None]
        self._gui.call(lambda: self._begin_ask(question, kind, evt, holder))
        while not evt.wait(0.2):
            if self._stop_event.is_set():
                return ''
        return self._gui.call(lambda: self._take_ask_answer(kind, holder))

    def _begin_ask(self, question, kind, evt, holder):
        self._pending_ask = (kind, evt, holder)
        if kind == 'teach_card':
            payload = (question if isinstance(question, dict)
                       else {'step': str(question or '')})
            step = str(payload.get('step') or '')
            attempt = str(payload.get('attempt') or '').strip()
            body = _('No tool does this yet. Teach me: record it in your '
                     'browser or on your desktop — or let me build it.')
            if attempt:
                body = _('I tried it myself first — %s') % attempt[:280] \
                    + '\n' + body
            self._append_card(
                title=_('How do I: %s?') % step[:140],
                body=body,
                options=[(_('Record web'), 'web', True),
                         (_('Record desktop'), 'desktop', False),
                         (_('Build LLM step'), 'llm', False),
                         (_('Build a brain'), 'brain', False),
                         (_('Skip'), 'skip', False)],
                on_choice=lambda value, _card: self._resolve_ask(value))
            return
        if kind in ('record_sequence', 'record_web_sequence'):
            self._begin_record_card(str(question or ''), kind)
            return
        kind_text = {
            'answer': _("your answer"),
            'confirm': _("yes / no"),
        }.get(kind, '')
        self._append('system', _("%s\n(Reply with %s.)") % (
            question, kind_text))

    def _resolve_ask(self, value):
        """Answer the pending ask (the worker picks it up on its poll)."""
        if self._pending_ask is None:
            return
        _kind, evt, holder = self._pending_ask
        holder[0] = str(value or '')
        evt.set()

    def _begin_record_card(self, question, kind):
        """The record ask as a card: Start runs the recorder on the
        placeholder node; the card advances when the file lands."""
        node_id = str(self._state.get('last_node_id') or '')
        prefill = ''
        node = None
        try:
            node = self.main_window.graph_view.graph_manager.get_node_by_id(
                node_id)
        except Exception:
            node = None
        if node is not None:
            try:
                prefill = str(node.get_property('description') or '')
            except Exception:
                prefill = ''
            if not prefill:
                try:
                    prefill = str(node.name() or '')
                except Exception:
                    prefill = ''
        self._record_kind = kind
        self._record_node_id = node_id
        self._record_hold = None
        self._record_card = self._append_card(
            title=question or _('Record the step'),
            body=_('Click Start — the recorder opens and captures your '
                   'actions. Press ESC when you finish; the tool is wrapped '
                   'the moment it is saved.'),
            options=[(_('Start recording'), 'start', True),
                     (_('I cannot record it'), 'cancel', False)],
            on_choice=self._on_record_choice,
            editable=prefill,
            editable_hint=_('What does this tool do? (the orchestrator '
                            'reads this)'))

    def _on_record_choice(self, value, card):
        if value != 'start':
            self._record_timer.stop()
            self._record_card = None
            self._record_hold = None
            self._resolve_ask('')
            return
        description = ''
        if card is not None and card.edit is not None:
            description = card.edit.text().strip()
        if self._start_recording(self._record_node_id,
                                 self._record_kind == 'record_web_sequence',
                                 description):
            card.set_status(_('Recording… press ESC when you are done.'))
            self._record_timer.start()
        else:
            card.set_status(_("Could not start the recorder — use the "
                              "node's Record button, then type DONE."))

    def _start_recording(self, node_id, is_web, description=''):
        """Start the node-attached recorder; True when it launched."""
        try:
            graph_view = self.main_window.graph_view
            node = (graph_view.graph_manager.get_node_by_id(node_id)
                    if node_id else None)
            if node is None:
                return False
            if description:
                node.set_property('description', description)
            if is_web:
                graph_view.record_web_session(node)
                self._record_hold = {'web': True, 'node': node}
                return True
            from player.builder.node_templates import slugify
            name = slugify(node.name() or 'step') or 'step'
            node.set_property('sequence_file', name + '.json')
            seq_dir = str(getattr(graph_view, 'sequences_folder', '') or '')
            watch = os.path.join(seq_dir, name + '.json') if seq_dir else ''
            pre_mtime = None
            try:
                if watch and os.path.exists(watch):
                    pre_mtime = os.path.getmtime(watch)
            except Exception:
                pre_mtime = None
            self._record_hold = {'web': False, 'node': node,
                                 'watch': watch, 'pre_mtime': pre_mtime}
            self.main_window.start_recording(name)
            return True
        except Exception:
            logger.exception('could not start recording')
            return False

    def _poll_recording(self):
        """Timer tick: advance the record card once the recording lands."""
        hold = self._record_hold or {}
        if not hold:
            self._record_timer.stop()
            return
        try:
            if hold.get('web'):
                node = hold.get('node')
                path = (str(node.get_property('session_file') or '')
                        if node is not None else '')
                if path:
                    self._finish_recording()
                return
            watch = str(hold.get('watch') or '')
            if watch and os.path.exists(watch):
                if hold.get('pre_mtime') is None:
                    self._finish_recording()
                    return
                try:
                    if os.path.getmtime(watch) != hold.get('pre_mtime'):
                        self._finish_recording()
                except Exception:
                    pass
        except Exception:
            logger.debug('record poll failed', exc_info=True)

    def _finish_recording(self):
        self._record_timer.stop()
        hold = self._record_hold or {}
        self._record_hold = None
        card = self._record_card
        self._record_card = None
        node = hold.get('node')
        if card is not None and card.edit is not None and node is not None:
            try:
                text = card.edit.text().strip()
                if text:
                    node.set_property('description', text)
            except Exception:
                pass
        if card is not None:
            card.set_status(_('Recorded — wrapping it into a tool…'))
        self._resolve_ask('')

    def _take_ask_answer(self, kind, holder):
        if self._pending_ask is not None:
            self._pending_ask = None
        answer = str(holder[0] or '').strip()
        if kind in ('record_sequence', 'record_web_sequence'):
            # The builder creates the node FIRST: its recorded file path is
            # the real answer, whatever the user typed.
            path = self._last_recorded_path()
            return path if path else (answer or '')
        return answer

    def _last_recorded_path(self):
        """sequence_file of the most recently created node, or ''."""
        last_id = self._state.get('last_node_id') or ''
        try:
            graph_view = self.main_window.graph_view

            def _read():
                if not last_id:
                    return ''
                node = graph_view.graph_manager.get_node_by_id(last_id)
                if node is None:
                    return ''
                try:
                    return str(node.get_property('sequence_file')
                               or node.get_property('session_file') or '')
                except Exception:
                    return ''
            return self._gui.call(_read)
        except Exception:
            return ''
