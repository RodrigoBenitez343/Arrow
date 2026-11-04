"""builder_panel.py — the chain analyst docked beside the canvas.

The panel is a READ-ONLY assistant for the chain's BEHAVIOUR.  It cannot
create, edit, wire, delete or record anything; it explains what the chain on
the canvas does, from the chain's own topology (``player/builder/describe.py``).

Two things it writes, and neither changes what the chain does:

* **content the user hands it** — with a single node selected, the paperclip
  opens a system file dialog and writes the chosen file into the field that
  node reads at run time (``player/builder/content.py``);
* **the chain's own flow metadata** (``player/builder/flow.py``) — the summary,
  the section boxes and the per-step sentences, stored in the chain file so a
  loose chain can describe itself to the agent later.  The same metadata is
  drawn on the canvas as boxes, and a smoke test rings each executed node in
  walk order while the chat explains it.  Nothing is executed: it is a visual
  aid, not a run.

Every failure is a HARD error: it is logged, shown in the chat and raised to a
dialog.  Nothing degrades silently.
"""

import logging
import os
import threading
import traceback

from PyQt5.QtCore import QObject, Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..constants import (
    ACCENT_COLOR,
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
from .mitosis_loader import MitosisLoader

logger = logging.getLogger(__name__)

#: Same width as the agent overlay's chat column.
PANEL_WIDTH = 500


class _GuiProxy(QObject):
    """Marshal one callable onto the GUI thread and wait for its result."""

    _job = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self._job.connect(self._run, Qt.BlockingQueuedConnection)

    def _run(self, job):
        fn, result, error = job
        try:
            result[0] = fn()
        except BaseException as exc:        # surfaced to the caller's thread
            error[0] = exc

    def call(self, fn):
        if QThread.currentThread() is self.thread():
            return fn()
        result, error = [None], [None]
        self._job.emit((fn, result, error))
        if error[0] is not None:
            raise error[0]
        return result[0]


def _prose(flow):
    """The wording of a flow object, ignoring ids' bookkeeping and stamps.

    What a reader sees: the summary, each box's title/note/members and each
    node's sentence.  Two descriptions that compare equal here are the same
    description, so "re-described" can be reported honestly instead of
    claiming a rewrite that changed nothing.
    """
    flow = flow if isinstance(flow, dict) else {}
    return (
        str(flow.get('summary') or ''),
        tuple((str(s.get('title') or ''), str(s.get('note') or ''),
               tuple(str(nid) for nid in s.get('nodes') or ()))
              for s in flow.get('sections') or ()),
        tuple((str(n.get('node') or ''), str(n.get('does') or ''))
              for n in flow.get('nodes') or ()),
        tuple((str(s.get('node') or ''), str(s.get('does') or ''))
              for s in flow.get('steps') or ()),
    )


class _TurnWorker(QThread):
    """One analyst turn on the worker thread."""

    finished_turn = pyqtSignal(object)
    status = pyqtSignal(str)
    trace = pyqtSignal(str)
    reply_delta = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, ctx, query, router, stop_event, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._query = query
        self._router = router
        self._stop = stop_event

    def run(self):
        try:
            payload = self._router.route(
                self._query,
                progress_cb=self.status.emit,
                trace=self.trace.emit,
                on_reply_delta=self.reply_delta.emit,
            )
        except BaseException as exc:        # a hard failure for the panel
            logger.exception('builder turn failed')
            self.failed.emit(str(exc) or exc.__class__.__name__)
            return
        self.finished_turn.emit(dict(payload or {}))


class _DescribeWorker(QThread):
    """Re-describe the open chain ONE CONNECTION AT A TIME.

    A single turn over the whole canvas needs the whole canvas in one prompt,
    which is too much for a small local model.  This runs
    ``staged.describe``: a node pass with no model, then one small call per
    wire, then the assembly.  Slow, and every wire gets its own sentence.
    """

    progress = pyqtSignal(str)
    done = pyqtSignal(object)
    failed = pyqtSignal(str, str)

    def __init__(self, ctx, stop_event, parent=None):
        super().__init__(parent)
        self._ctx = ctx
        self._stop = stop_event

    def run(self):
        try:
            from player.builder import reasoning, snapshot as bsnapshot
            from player.builder import settings as bsettings
            from player.builder import staged
            # The canvas is read on the GUI thread; everything after it is a
            # plain snapshot this thread owns.
            snap = self._ctx['run_on_gui'](
                lambda: bsnapshot.build(self._ctx['graph_manager']()))
            result = staged.describe(
                snap, reasoning.generate, bsettings.load(),
                progress=self.progress.emit, cancel=self._stop.is_set)
        except BaseException as exc:        # a hard failure for the panel
            logger.exception('staged describe failed')
            self.failed.emit(str(exc) or exc.__class__.__name__,
                             traceback.format_exc())
            return
        self.done.emit(result)


class _IngestWorker(QThread):
    """Read a source and write it into the selected node."""

    done = pyqtSignal(object)
    failed = pyqtSignal(str, str)

    def __init__(self, job, parent=None):
        super().__init__(parent)
        self._job = job

    def run(self):
        try:
            self.done.emit(self._job())
        except BaseException as exc:
            logger.exception('builder content ingest failed')
            self.failed.emit(str(exc) or exc.__class__.__name__,
                             traceback.format_exc())


class BuilderPanel(QWidget):
    """Side-docked chain analyst (agent-mode look; canvas stays visible)."""

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self._gui = _GuiProxy()
        self._stop_event = threading.Event()
        self._worker = None
        self._ingest_worker = None
        self._reply_bubble = None
        self._reply_text = ''
        self._log_bubble = None
        self._log_lines = []
        # Chat is per CHAIN, not per app session: the tail the model sees is
        # keyed by ``graph_view._chain_epoch``, and the visible chat is cleared
        # when the canvas becomes a different chain.  Otherwise every new chain
        # inherits the previous chain's questions and answers.
        self._sessions = {}
        self._session_id = None
        self._session_timer = QTimer(self)
        self._session_timer.setInterval(800)
        self._session_timer.timeout.connect(self._sync_session)
        self._session_timer.start()
        self._status_phrases = []
        self._status_index = 0
        self._status_until = 0.0
        # Visual walkthrough (a smoke test that runs NOTHING): the timer steps
        # one entry of ``player/builder/flow.py``'s script at a time.
        self._walk_steps = []
        self._walk_index = 0
        self._walk_timer = QTimer(self)
        self._walk_timer.setInterval(1800)
        self._walk_timer.timeout.connect(self._walk_next)

        self.setFixedWidth(PANEL_WIDTH)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.setAcceptDrops(True)
        self._build_ui()

    # ------------------------------------------------------------- UI

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(0)

        self._card = QFrame(self)
        self._card.setObjectName("builderCard")
        self._card.setStyleSheet(
            "QFrame#builderCard {"
            f" background: {BG_RGBA};"
            f" border: 1px solid {HAIRLINE};"
            f" border-radius: {RADIUS_LG}px;"
            "}"
            "QFrame#builderCard QLabel { background: transparent; }"
        )
        layout.addWidget(self._card, 1)

        card = QVBoxLayout(self._card)
        card.setContentsMargins(0, 0, 0, 0)
        card.setSpacing(0)

        header = QFrame(self._card)
        header.setObjectName("builderHeader")
        header.setStyleSheet(
            "QFrame#builderHeader {"
            f" background: {HEADER_RGBA};"
            f" border-top-left-radius: {RADIUS_LG}px;"
            f" border-top-right-radius: {RADIUS_LG}px;"
            "}"
        )
        header.setFixedHeight(44)
        hlay = QHBoxLayout(header)
        hlay.setContentsMargins(14, 6, 8, 6)
        hlay.setSpacing(8)
        title = QLabel("Builder", header)
        title.setStyleSheet(
            f"color: {TEXT_COLOR}; font-weight: 600; font-size: 13px;")
        hlay.addWidget(title)
        subtitle = QLabel(_("reads the chain"), header)
        subtitle.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")
        hlay.addWidget(subtitle)
        hlay.addStretch(1)
        self._clear_btn = HoverGlowButton(
            "ERASER", size=32, icon=14, parent=header)
        self._clear_btn.setToolTip(
            _("Clear the boxes and the walk highlight (the chain is untouched)"))
        self._clear_btn.clicked.connect(self._on_clear_annotations)
        hlay.addWidget(self._clear_btn)
        self._describe_btn = HoverGlowButton(
            "NOTES", size=32, icon=14, parent=header)
        self._describe_btn.setToolTip(_(
            "Re-describe this chain now, one connection at a time: one small "
            "model call per wire, so a local model can do the whole chain"))
        self._describe_btn.clicked.connect(self._on_describe)
        hlay.addWidget(self._describe_btn)
        self._walk_btn = HoverGlowButton(
            "EYE", size=32, icon=14, parent=header)
        self._walk_btn.setToolTip(_(
            "Walk the chain node by node on the canvas — no model is asked, "
            "and nothing is run"))
        self._walk_btn.clicked.connect(self._on_walk)
        hlay.addWidget(self._walk_btn)
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

        self._settings_row = self._build_settings(self._card)
        card.addWidget(self._settings_row)

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

        self._status_row = QFrame(self._card)
        self._status_row.setStyleSheet("QFrame { background: transparent; }")
        srow = QHBoxLayout(self._status_row)
        srow.setContentsMargins(14, 2, 14, 4)
        srow.setSpacing(8)
        self._loader = MitosisLoader(self._status_row, size=72)
        srow.addWidget(self._loader, 0, Qt.AlignVCenter)
        self._status_label = QLabel('', self._status_row)
        self._status_label.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 11px;"
            f" font-family: {FONT_FAMILY};")
        srow.addWidget(self._status_label, 1)
        card.addWidget(self._status_row)
        self._status_row.setVisible(False)
        self._phase_timer = QTimer(self)
        self._phase_timer.setInterval(1600)
        self._phase_timer.timeout.connect(self._advance_phase)

        bar = QFrame(self._card)
        bar.setStyleSheet("QFrame { background: transparent; }")
        blay = QHBoxLayout(bar)
        blay.setContentsMargins(12, 8, 12, 12)
        blay.setSpacing(8)
        self._input = QLineEdit(bar)
        self._input.setPlaceholderText(
            _("Ask what this chain does, how a node behaves, what a port carries…"))
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
        self._attach_btn = HoverGlowButton(
            "PAPERCLIP", size=32, icon=14, parent=bar)
        self._attach_btn.setToolTip(
            _("Load a file into the selected node"))
        self._attach_btn.clicked.connect(self._on_attach)
        blay.addWidget(self._attach_btn)
        blay.addWidget(self._input, 1)
        self._send_btn = HoverGlowButton("SEND", size=32, icon=14, parent=bar)
        self._send_btn.setToolTip(_("Send"))
        self._send_btn.clicked.connect(self._on_send)
        blay.addWidget(self._send_btn)
        card.addWidget(bar)

        self._append('system', _(
            "Ask what this chain does, how a node behaves, or what a port "
            "carries. I read the canvas — I never change it. Ask me to box "
            "the sections or to smoke-test the chain and I draw on the "
            "canvas: boxes around each part, one ring per step, nothing "
            "executed. With one node selected, the paperclip loads a file "
            "into the field that node reads."))

    def _settings_label(self, text):
        label = QLabel(text)
        label.setStyleSheet(
            f"color: {TEXT_MUTED}; font-size: 11px;"
            f" font-family: {FONT_FAMILY};")
        return label

    def _build_settings(self, parent):
        """Engine / model of the builder's own model call."""
        from player.builder import settings as bsettings
        cfg = bsettings.load()
        row = QFrame(parent)
        row.setObjectName("builderSettings")
        row.setStyleSheet("QFrame#builderSettings { background: transparent; }")
        grid = QGridLayout(row)
        grid.setContentsMargins(14, 4, 14, 6)
        grid.setSpacing(6)
        combo_style = (
            f"QComboBox {{ background: {WELL_BG};"
            f" color: {TEXT_COLOR}; border: 1px solid {HAIRLINE};"
            f" border-radius: {RADIUS_SM}px; padding: 3px 6px;"
            f" font-size: 11px; font-family: {FONT_FAMILY}; }}"
        )
        grid.addWidget(self._settings_label(_("Engine")), 0, 0)
        self._engine_combo = QComboBox(row)
        self._engine_combo.setStyleSheet(combo_style)
        try:
            from NGUI.dialogs.engine_options import engine_items
            engines = [(eid, label) for eid, label, _on in engine_items()]
        except ImportError:
            engines = list(bsettings.ENGINES)
        for engine_id, label in engines:
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

        self._engine_combo.currentIndexChanged.connect(self._on_engine_changed)
        self._model_combo.activated.connect(
            lambda _index: self._on_model_changed(self._model_combo.currentText()))
        if self._model_combo.lineEdit() is not None:
            self._model_combo.lineEdit().editingFinished.connect(
                lambda: self._on_model_changed(self._model_combo.currentText()))

        row.setVisible(False)
        return row

    # --------------------------------------------------------- settings

    def _toggle_settings(self):
        shown = self._settings_row.isHidden()
        self._settings_row.setVisible(shown)
        if shown:
            # AI Settings edits the same file ("Descriptions — the model that
            # writes them"), so the panel re-reads before showing its row.
            self._guard_settings(self._reload_settings)

    def _reload_settings(self):
        """Show the STORED provider/model, whoever wrote it last."""
        from player.builder import settings as bsettings
        cfg = bsettings.load()
        index = self._engine_combo.findData(cfg.get('engine'))
        self._engine_combo.blockSignals(True)
        if index >= 0:
            self._engine_combo.setCurrentIndex(index)
        self._engine_combo.blockSignals(False)
        self._model_combo.blockSignals(True)
        self._model_combo.setCurrentText(str(cfg.get('model') or ''))
        self._model_combo.blockSignals(False)

    def _save_settings(self, values):
        from player.builder import settings as bsettings
        bsettings.save(values)

    def _on_engine_changed(self, _index):
        """Save the engine AND retarget the model that no longer fits it.

        A model carries its provider in the prefix, so keeping ``groq:…``
        after switching the engine to DeepSeek made the switch a no-op: the
        prefix still routed to Groq.  A model that belongs to another provider
        is dropped (the new provider's default takes over) instead.
        """
        from NGUI.dialogs.engine_options import models_for_engine, store_model

        engine = str(self._engine_combo.currentData() or '')
        model = (self._model_combo.currentText() or '').strip()
        if model and not models_for_engine([model], engine):
            model = ''
            self._model_combo.blockSignals(True)
            self._model_combo.clear()
            self._model_combo.blockSignals(False)
        self._guard_settings(lambda: self._save_settings(
            {'engine': engine, 'model': store_model(engine, model)}))
        # The engine decides which provider's models are offered: listing the
        # gateway's whole merged list let an Ollama tag sit under glama.
        self._refill_model_combo()

    def _on_model_changed(self, text):
        """Persist a model pick, ALWAYS in the form the gateway routes.

        A bare ``deepseek-v4-pro`` under a cloud engine is prefixed with that
        engine (the engine is the provider the user chose); an empty box is not
        a choice — clearing the combo for a Refresh used to wipe the stored
        model and leave the turn routing to a default that does not exist.
        """
        from player.builder import settings as bsettings
        from NGUI.dialogs.engine_options import store_model

        text = str(text or '').strip()
        engine = str(self._engine_combo.currentData() or '')
        if not text:
            try:
                if str(bsettings.load().get('model') or '').strip():
                    return
            except Exception:
                pass
        self._guard_settings(lambda: self._save_settings(
            {'model': store_model(engine, text)}))

    def _guard_settings(self, action):
        try:
            action()
        except Exception as exc:
            self._hard_error(_('Builder settings'), str(exc))

    def _refill_model_combo(self):
        """Show only the models the SELECTED engine serves."""
        from NGUI.dialogs.engine_options import engine_id, models_for_engine
        names = models_for_engine(
            getattr(self, '_gateway_models', None) or [],
            engine_id(self._engine_combo))
        current = (self._model_combo.currentText() or '').strip()
        self._model_combo.blockSignals(True)
        try:
            self._model_combo.clear()
            for name in names:
                self._model_combo.addItem(name)
            if current:
                # Keep what was chosen even when this engine's list does not
                # carry it: an edited combo is still the user's model, and a
                # blank one silently falls back to a default that may not exist.
                self._model_combo.setCurrentText(current)
        finally:
            self._model_combo.blockSignals(False)

    def _refresh_models(self):
        try:
            from player.builder import reasoning as breasoning
            names = breasoning.list_models()
        except Exception as exc:
            self._hard_error(_('Builder model list'), str(exc))
            return
        self._gateway_models = list(names)
        self._refill_model_combo()
        shown = [self._model_combo.itemText(i)
                 for i in range(self._model_combo.count())]
        self._append('tool', _("models: %s") % (', '.join(shown) or _('none')))

    # ------------------------------------------------------------ chat

    def _append_bubble(self, text, is_user=False, muted=False, danger=False):
        bubble = QLabel(text)
        bubble.setWordWrap(True)
        bubble.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bubble.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        color = DANGER_COLOR if danger else (TEXT_MUTED if muted else TEXT_COLOR)
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
        return bubble

    def _scroll_to_bottom(self):
        def _scroll():
            bar = self._chat_scroll.verticalScrollBar()
            bar.setValue(bar.maximum())
        QTimer.singleShot(0, _scroll)

    def _append(self, kind, text):
        if kind == 'user':
            self._append_bubble(text, is_user=True)
        elif kind == 'tool':
            self._append_bubble(text, muted=True)
        elif kind == 'error':
            self._append_bubble(text, danger=True)
        else:
            self._append_bubble(text)

    # --------------------------------------------------- processing row

    def _phrases(self):
        return [
            _("Reading the canvas…"),
            _("Resolving the ports…"),
            _("Walking the execution order…"),
            _("Writing the description…"),
        ]

    def _start_busy(self, text=None):
        self._status_phrases = self._phrases()
        self._status_index = 0
        self._status_until = 0.0
        self._status_row.setVisible(True)
        self._loader.start()
        self._set_status(text or self._status_phrases[0], sticky=False)
        self._phase_timer.start()

    def _stop_busy(self):
        self._phase_timer.stop()
        self._loader.stop()
        self._status_row.setVisible(False)

    def _set_status(self, text, sticky=True):
        import time
        self._status_label.setText(str(text))
        self._status_until = time.monotonic() + 1.5 if sticky else 0.0

    def _advance_phase(self):
        import time
        if self._status_row.isHidden() or not self._status_phrases:
            return
        if time.monotonic() < self._status_until:
            return
        self._status_index = (self._status_index + 1) % len(self._status_phrases)
        self._status_label.setText(self._status_phrases[self._status_index])

    def _on_status(self, event):
        event = str(event or '')
        if event == 'reading':
            self._set_status(_("Reading the canvas…"))
        elif event.startswith('tool:'):
            self._set_status(_("Running %s…") % event[5:])

    def _on_trace(self, message):
        text = str(message or '')
        if not text or text.startswith(('you:', 'round ', 'model:',
                                        'selection:')):
            return
        self._log_lines.append(text)
        if self._log_bubble is None:
            self._log_bubble = self._append_bubble(text, muted=True)
        else:
            self._log_bubble.setText('\n'.join(self._log_lines))
            self._scroll_to_bottom()

    def _on_reply_delta(self, text):
        text = str(text or '')
        if not text.strip():
            return
        self._reply_text = text
        if self._reply_bubble is None:
            self._reply_bubble = self._append_bubble(text)
        else:
            self._reply_bubble.setText(text)
            self._scroll_to_bottom()

    # -------------------------------------------------------- selection

    def _selected_nodes(self):
        return list(self.main_window.graph_view.node_graph.selected_nodes()
                    or [])

    # ---------------------------------------------------------- content

    def _on_attach(self):
        """Paperclip: browse the system and write the file into the node."""
        if self._single_selected() is None:
            return
        paths, _filter = QFileDialog.getOpenFileNames(
            self, _('Load content into the node'), '',
            _('All files (*.*)'))
        if not paths:
            return
        self._start_ingest([('file', path) for path in paths])

    def _single_selected(self):
        nodes = self._selected_nodes()
        if len(nodes) != 1:
            self._hard_error(
                _('Load content'),
                _('Select exactly one node first — the content is written into '
                  'that node\'s own field. (%d selected)') % len(nodes))
            return None
        return nodes[0]

    def _start_ingest(self, sources):
        node = self._single_selected()
        if node is None:
            return
        if self._ingest_worker is not None and self._ingest_worker.isRunning():
            self._hard_error(_('Load content'),
                             _('A content load is already running.'))
            return
        node_id = str(getattr(node, 'id', '') or '')
        slot = None        # the slot follows the source's kind (default_slot)
        ctx = self._make_ctx()
        self._append('tool', _("loading %d source(s) into %s…")
                     % (len(sources), node_id))

        def _job():
            from player.builder import content as bcontent
            written = []
            for kind, value in sources:
                if kind == 'file':
                    written.extend(bcontent.ingest_file(ctx, node_id, value, slot))
                elif kind == 'folder':
                    written.extend(bcontent.ingest_folder(ctx, node_id, value, slot))
                else:
                    written.extend(bcontent.ingest_text(ctx, node_id, value, slot))
            return written

        self._ingest_worker = _IngestWorker(_job, parent=self)
        self._ingest_worker.done.connect(self._on_ingest_done)
        self._ingest_worker.failed.connect(self._on_ingest_failed)
        self._ingest_worker.start()

    def _on_ingest_done(self, results):
        self._ingest_worker = None
        for result in (results or []):
            self._append('system', result.line())

    def _on_ingest_failed(self, message, detail):
        self._ingest_worker = None
        logger.error('content ingest failed: %s\n%s', message, detail)
        self._append('error', _("Content not loaded: %s") % message)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Critical)
        box.setWindowTitle(_('Content not loaded'))
        box.setText(str(message))
        box.setDetailedText(str(detail))
        box.exec_()

    # ------------------------------------------------ drag and drop

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() or event.mimeData().hasText():
            event.acceptProposedAction()

    def dropEvent(self, event):
        sources = []
        mime = event.mimeData()
        if mime.hasUrls():
            for url in mime.urls():
                path = url.toLocalFile()
                if not path:
                    continue
                sources.append(('folder', path) if os.path.isdir(path)
                               else ('file', path))
        elif mime.hasText() and mime.text().strip():
            sources.append(('text', mime.text()))
        if not sources:
            return
        event.acceptProposedAction()
        self._start_ingest(sources)

    # ------------------------------------------------------------- turn

    def _on_send(self):
        text = self._input.text().strip()
        self._input.clear()
        if not text:
            return
        self._sync_session()
        self._append('user', text)
        self._remember('user', text)
        # Every message is a turn.  The walkthrough and the description are the
        # header buttons (deterministic, one click), so the chat never routes
        # to them and never has to guess what the user wanted instead.
        self._start_turn(text)

    def _make_ctx(self):
        """The read-side ctx; every canvas access marshals to the GUI thread."""
        graph_view = self.main_window.graph_view

        def _graph_manager():
            return graph_view.graph_manager

        def _selection_ids():
            nodes = list(graph_view.node_graph.selected_nodes() or [])
            return [str(getattr(n, 'id', '')) for n in nodes
                    if getattr(n, 'id', '')]

        def _stored_flow():
            graph = getattr(self.main_window, 'graph_view', None)
            getter = getattr(graph, 'get_flow', None)
            if callable(getter):
                return dict(getter() or {})
            return {}

        return {
            'graph_manager': lambda: self._gui.call(_graph_manager),
            'run_on_gui': self._gui.call,
            'stop': self._stop_event.is_set,
            'selection_node_ids': lambda: self._gui.call(_selection_ids),
            'stored_flow': _stored_flow,
            'history': list(self._session_history()),
        }

    def _start_turn(self, message):
        if self._worker is not None and self._worker.isRunning():
            self._hard_error(_('Builder'),
                             _('Still answering — wait for the current reply.'))
            return
        # A new question ends the previous walkthrough: its bubbles belong to
        # the old answer and its ring would sit on an unrelated node.
        self._stop_walk()
        from player.builder.assistant import BuilderAssistant
        self._stop_event.clear()
        self._reply_bubble = None
        self._reply_text = ''
        self._log_bubble = None
        self._log_lines = []
        self._start_busy(_("Reading the canvas…"))
        ctx = self._make_ctx()
        self._worker = _TurnWorker(
            ctx, message, BuilderAssistant(ctx), self._stop_event, parent=self)
        self._worker.finished_turn.connect(self._on_turn_finished)
        self._worker.failed.connect(self._on_turn_failed)
        self._worker.status.connect(self._on_status)
        self._worker.trace.connect(self._on_trace)
        self._worker.reply_delta.connect(self._on_reply_delta)
        self._worker.start()

    def _on_turn_finished(self, payload):
        payload = payload if isinstance(payload, dict) else {'reply': str(payload or '')}
        text = str(payload.get('reply') or '')
        self._remember('assistant', text)
        final = self._reply_text.strip() or text.strip()
        if final:
            if self._reply_bubble is not None:
                self._reply_bubble.setText(final)
            else:
                self._append_bubble(final)
        self._reply_bubble = None
        self._reply_text = ''
        self._log_bubble = None
        self._log_lines = []
        self._stop_event.set()
        self._stop_busy()
        self._annotate(payload)

    def _on_turn_failed(self, message):
        self._reply_bubble = None
        self._reply_text = ''
        self._log_bubble = None
        self._log_lines = []
        self._stop_event.set()
        self._stop_busy()
        self._append('error', _("Builder failed: %s") % message)
        self._hard_error(_('Builder failed'), message)

    def _on_stop(self):
        self._stop_event.set()
        self._stop_walk()
        self._stop_busy()

    def _remember(self, role, text):
        history = self._session_history()
        history.append({'role': str(role), 'text': str(text or '')[:800]})
        del history[:-10]

    # ------------------------------------------------------- sessions

    def _chain_id(self):
        """Identity of the chain on the canvas (a new one is a new session).

        ``graph_view._chain_epoch`` is bumped when a chain is opened or the
        canvas is cleared, which is exactly when the conversation must not
        carry over.  Saving an unsaved chain does NOT bump it, so naming a
        chain mid-conversation keeps the session.
        """
        graph = getattr(self.main_window, 'graph_view', None)
        return int(getattr(graph, '_chain_epoch', 0) or 0)

    def _session_history(self):
        if self._session_id is None:
            self._session_id = self._chain_id()
        return self._sessions.setdefault(self._session_id, [])

    def _sync_session(self):
        """Start a fresh chat when the canvas holds a different chain."""
        if self._session_id is None:
            self._session_id = self._chain_id()
            self._sessions.setdefault(self._session_id, [])
            return
        chain_id = self._chain_id()
        if chain_id == self._session_id:
            return
        if self._worker is not None and self._worker.isRunning():
            return                  # finish the turn, then rotate
        self._session_id = chain_id
        self._sessions.setdefault(chain_id, [])
        self._reset_chat()

    def _reset_chat(self):
        """Drop the visible conversation, keep the panel and its settings."""
        self._walk_timer.stop()
        self._walk_steps = []
        self._walk_index = 0
        while self._chat_layout.count():
            item = self._chat_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._reply_bubble = None
        self._reply_text = ''
        self._log_bubble = None
        self._log_lines = []
        cm = getattr(getattr(self.main_window, 'graph_view', None),
                     'config_manager', None)
        path = str(getattr(cm, '_current_chain_file', '') or '')
        label = os.path.basename(path) if path else _('unsaved chain')
        self._append('system', _(
            "New session — %s. I read this chain's canvas and I never change "
            "it.") % label)

    # ------------------------------------------------------ annotations

    def _overlay(self):
        """The canvas annotation layer, or None (with a dialog) when absent."""
        graph_view = self.main_window.graph_view
        getter = getattr(graph_view, 'flow_overlay', None)
        if not callable(getter):
            self._hard_error(_('Canvas annotations'), _(
                'this canvas cannot be annotated: its graph view has no flow '
                'overlay'))
            return None
        return getter()

    def _config(self):
        cfg = getattr(self.main_window.graph_view, 'config_manager', None)
        if cfg is None:
            raise RuntimeError('the canvas has no chain configuration')
        return cfg

    def _graph_view(self):
        graph = getattr(self.main_window, 'graph_view', None)
        if graph is None:
            raise RuntimeError('the canvas is not available')
        return graph

    def _snapshot(self):
        """The live canvas, read on the GUI thread (the panel's own thread)."""
        from player.builder import snapshot as bsnapshot
        return bsnapshot.build(self.main_window.graph_view.graph_manager)

    def _on_describe(self):
        """Re-describe the OPEN chain NOW, one connection at a time.

        Nodes first (their own settings, no model), then one small model call
        per wire, then the boxes and the summary.  No full-canvas prompt is
        ever built, so a small local model can do the whole chain; it takes
        one call per connection and the panel says which one is running.
        """
        if self._worker is not None and self._worker.isRunning():
            self._hard_error(_('Describe chain'), _(
                'Still answering — wait for the current reply.'))
            return
        self._stop_walk()
        self._stop_event.clear()
        self._reply_bubble = None
        self._reply_text = ''
        self._append('system', _(
            'describing one connection at a time — a model call per wire, so '
            'this is slow on a small model and every wire still gets its own '
            'sentence'))
        self._start_busy(_('Reading the canvas…'))
        self._worker = _DescribeWorker(self._make_ctx(), self._stop_event,
                                       parent=self)
        self._worker.progress.connect(self._on_describe_progress)
        self._worker.done.connect(self._on_describe_done)
        self._worker.failed.connect(self._on_describe_failed)
        self._worker.start()

    def _on_describe_progress(self, event):
        self._set_status(_('Describing %s…') % str(event or ''))

    def _on_describe_done(self, result):
        """Store what the pass described, and say what did not answer."""
        self._worker = None
        self._stop_event.set()
        self._stop_busy()
        result = result if isinstance(result, dict) else {}
        failed = list(result.get('failed') or ())
        try:
            self._store_flow(result.get('flow') or {})
        except Exception as exc:
            logger.exception('flow metadata rejected')
            self._hard_error(_('Flow metadata not stored'), str(exc))
            return
        self._append('system', _(
            'connections described: %d of %d (one model call each)')
            % (int(result.get('done') or 0), int(result.get('total') or 0)))
        if not failed:
            return
        lines = ['%s — %s' % (label, error) for label, error in failed[:6]]
        if len(failed) > 6:
            lines.append(_('… %d more (see the log)') % (len(failed) - 6))
        logger.warning('staged describe: %d connection(s) unanswered:\n%s',
                       len(failed), '\n'.join(lines))
        self._append('error', _(
            '%d connection(s) got no answer and kept their derived wording:\n%s')
            % (len(failed), '\n'.join(lines)))
        self._hard_error(_('Describe chain'), _(
            'The description was stored, but the model did not answer for %d '
            'connection(s). Those kept the wording derived from the canvas.\n\n'
            '%s') % (len(failed), '\n'.join(lines)))

    def _on_describe_failed(self, message, detail):
        """The pass itself failed: the canvas still describes the chain."""
        self._worker = None
        self._stop_event.set()
        self._stop_busy()
        logger.error('staged describe failed: %s\n%s', message, detail)
        self._append('error', _('Builder failed: %s') % message)
        self._hard_error(_('Builder failed'), message)
        try:
            self._describe_from_canvas(_(
                'the model call failed — described from the canvas instead'))
        except Exception as exc:
            logger.exception('describe failed')
            self._hard_error(_('Describe chain'), str(exc))

    def _describe_from_canvas(self, reason=''):
        """Describe the canvas deterministically (no model, no run).

        Every sentence comes from the node it describes, so this is both the
        fallback when a model answer carries no ``flow`` and the tool used when
        a model is not available at all.  Stored refs this canvas does not have
        are dropped rather than raised: describing again is the whole point.
        """
        getter = getattr(self._graph_view(), 'get_flow', None)
        stored = dict(getter() or {}) if callable(getter) else {}
        from player.builder import flow as bflow
        stored, dropped = bflow.prune(self._snapshot(), stored)
        if dropped:
            logger.warning('describe: dropped %d ref(s) this canvas does not '
                           'have', dropped)
        if reason:
            self._append('system', reason)
        self._store_flow(stored, force=True)

    def _annotate(self, payload):
        """The side effects of a chat turn: a change is written to the file.

        The edit itself already happened on the GUI thread (``tools.edit_node``
        / ``tools.wire_nodes``), so all that is left is making it permanent —
        exactly what the Describe button does for a description.  Writing the
        description or starting the walkthrough is not a turn's business: those
        are the panel's buttons.
        """
        if not payload.get('changed'):
            return
        config = getattr(getattr(self.main_window, 'graph_view', None),
                         'config_manager', None)
        path = str(getattr(config, '_current_chain_file', '') or '')
        if not (path and os.path.exists(path)):
            self._append('system', _(
                'nothing written yet: this canvas has no chain file — save the '
                'chain once and the change becomes permanent'))
            return
        self._graph_view().save_chain(path)
        self._append('system', _('chain written to %s') % os.path.basename(path))

    def _store_flow(self, raw, force=False):
        """Validate, keep in the chain and draw the boxes of one flow object.

        ``force`` (the canvas fallback) re-derives every sentence and note;
        a model's answer is merged instead — its own words always win over the
        derivation.  Either way the chain file is rewritten and the panel says
        whether the description CHANGED, so a no-op can never masquerade as a
        rewrite.
        """
        from player.builder import flow as bflow
        graph = self._graph_view()
        previous = _prose(getattr(graph, 'get_flow', lambda: {})() or {})
        snapshot = self._snapshot()
        metadata = bflow.check_nodes(snapshot, bflow.normalise(raw))
        # A model writes a paragraph; it will not enumerate every node.  Fill in
        # a description for everything it left out (its own words always win),
        # so the chain says what EACH node does instead of only the gist.
        metadata = bflow.complete(snapshot, metadata, force=force)
        # A section the model left without a note gets one derived from its own
        # nodes (and it is STORED, so the next reader sees it too): the box
        # prints the note, so a title-only section is a headline with no body.
        metadata['sections'] = bflow.sections(snapshot, metadata,
                                              force_notes=force)
        # The node's OWN description field is where the full sentence lives:
        # every node dialog shows it (ModernDialog.add_description) and the
        # save carries it, so the text sits on the node the user is looking at.
        self._write_descriptions(metadata)
        setter = getattr(graph, 'set_flow', None)
        if not callable(setter):
            raise RuntimeError(
                'this canvas cannot store flow metadata (no set_flow)')
        setter(metadata)
        overlay = self._overlay()
        if overlay is not None:
            overlay.show_sections(bflow.sections(snapshot, metadata))
        self._append('system', _(
            '%s (%d section(s), %d node(s), %d step(s)) — %s')
            % (_('flow metadata re-described') if force
               else _('flow metadata stored'), len(metadata['sections']),
               len(metadata['nodes']), len(metadata['steps']),
               _('the wording changed') if _prose(metadata) != previous
               else _('the wording came out IDENTICAL to what was stored')))
        # A chain that already has a file is updated NOW, so the description
        # stays permanent even if this session never saves again.
        path = str(getattr(self._config(), '_current_chain_file', '') or '')
        if path and os.path.exists(path):
            graph.save_chain(path)
            self._append('system', _('written to %s')
                         % os.path.basename(path))
        else:
            self._append('system', _(
                'nothing written: this canvas has no chain file yet — save the '
                'chain once and the description becomes permanent'))

    def _write_descriptions(self, metadata):
        """Copy each node's sentence into that node's ``description`` property.

        A node without the property raises: every type declares it, so a raise
        here means a type was added without one — a defect, not a fallback.
        """
        manager = self.main_window.graph_view.graph_manager
        written = 0
        for entry in metadata.get('nodes') or ():
            node = manager.get_node_by_id(entry['node'])
            if node is None:
                continue
            node.set_property('description', str(entry.get('does') or ''))
            written += 1
        if written:
            self._append('system', _(
                'description written into %d node(s) — open any node to read '
                'it in full') % written)

    def _on_walk(self):
        """Header button: the per-node highlight, with NO model at all.

        A walkthrough is deterministic — ``flow.steps`` builds every line from
        the node's own settings — so asking a model for one only bought a
        whole-canvas prompt, and that request is exactly what a small model
        refuses ("please reduce the length of the messages").
        """
        try:
            self._start_walk()
        except Exception as exc:
            logger.exception('walkthrough failed')
            self._hard_error(_('Smoke test'), str(exc))

    def _start_walk(self):
        """The visual smoke test: ring each executed node, explain it, run nothing."""
        from player.builder import flow as bflow
        snapshot = self._snapshot()
        getter = getattr(self._graph_view(), 'get_flow', None)
        stored = getter() if callable(getter) else {}
        metadata = bflow.normalise(stored or {})
        # A description written by an older session can name nodes this canvas
        # does not have; walking it would raise on the first dead ref instead of
        # showing the rest of the chain.
        metadata, dropped = bflow.prune(snapshot, metadata)
        if dropped:
            logger.warning('walkthrough: skipped %d ref(s) this canvas does '
                           'not have', dropped)
        steps = bflow.steps(snapshot, metadata)
        if not steps:
            self._hard_error(_('Smoke test'), _(
                'this chain has no execution step to walk: no node starts it'))
            return
        overlay = self._overlay()
        if overlay is None:
            return
        overlay.show_sections(bflow.sections(snapshot, metadata))
        self._walk_steps = steps
        self._walk_index = 0
        self._append('system', _(
            'visual walkthrough — %d step(s); nothing is executed') % len(steps))
        self._walk_timer.start()
        self._walk_next()

    def _walk_next(self):
        """Ring the next step and say what it does and how it connects."""
        if self._walk_index >= len(self._walk_steps):
            self._stop_walk(finished=True)
            return
        step = self._walk_steps[self._walk_index]
        self._walk_index += 1
        overlay = self._overlay()
        if overlay is None:
            self._stop_walk()
            return
        try:
            overlay.highlight([step['id']])
        except Exception as exc:
            self._hard_error(_('Smoke test'), str(exc))
            self._stop_walk()
            return
        body = '\n'.join('· ' + line for line in step['body'])
        self._append_bubble(_('Step %d/%d — %s [%s]\n%s')
                            % (step['index'], len(self._walk_steps),
                               step['title'], step['role'], body))

    def _stop_walk(self, finished=False):
        """End the walkthrough (the boxes stay: they are the chain's sections)."""
        self._walk_timer.stop()
        self._walk_steps = []
        self._walk_index = 0
        overlay = self._overlay()
        if overlay is not None:
            overlay.clear_highlights()
        if finished:
            self._append('system', _('walkthrough finished — nothing was run'))

    def _on_clear_annotations(self):
        """Header button: drop the boxes and the ring, leave the chain alone."""
        self._walk_timer.stop()
        self._walk_steps = []
        self._walk_index = 0
        overlay = self._overlay()
        if overlay is None:
            return
        overlay.clear()
        self._append('system', _('annotations cleared'))

    # ------------------------------------------------------- hard error

    def _hard_error(self, title, message):
        """Show a failure the user cannot miss (chat + dialog + log)."""
        logger.error('%s: %s', title, message)
        if not message:
            return
        self._append('error', _("%s: %s") % (title, message))
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Critical)
        box.setWindowTitle(str(title))
        box.setText(str(message))
        box.exec_()
