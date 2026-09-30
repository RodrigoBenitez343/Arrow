"""Always-on-top text prompt shared by every Input-node prompt site.

The prompt is built on :class:`~NGUI.dialogs.base_dialog.ModernDialog` — the
same frameless dark container, title bar and accent buttons every node
properties dialog uses — so a runtime prompt never looks like a stray native
window.

Two details make it behave like a real modal dialog during chain playback:

* it is top-most and re-activates itself once the native window exists, so it
  cannot stay hidden behind the browser a web sequence opened;
* the window that had the foreground *before* the prompt is put back
  afterwards.  The prompt is modal and takes the foreground, and the Input
  node then types the resolved text into the window the chain is driving (a
  browser a web sequence focused) - without the restore those keystrokes go
  to whatever app kept the focus instead.

A prompt is also safe to ask from any thread.  Chains run on a plain Python
thread (scheduler / web playback), and building a widget there made Qt warn
("QObject::startTimer: Timers can only be used with threads started with
QThread") and then kill the process outright - no Python traceback, the GUI
just vanished.  Off-thread callers therefore get the dialog on the GUI thread
and block until it is answered; with no GUI at all the prompt is refused
instead of crashing.
"""
import ctypes
import json
import logging
import os
import threading
import time

from PyQt5.QtCore import Qt, QObject, QThread, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from NGUI.dialogs.base_dialog import ModernDialog

logger = logging.getLogger(__name__)


def _foreground_hwnd():
    """Handle of the window that has the foreground right now (0 if none)."""
    try:
        return ctypes.windll.user32.GetForegroundWindow() or 0
    except Exception:
        return 0


def _restore_foreground(hwnd, timeout=0.5):
    """Give the foreground back to the window that had it before the prompt.

    Waits (bounded) until the switch has actually happened, so the caller may
    type straight away without racing the activation.
    """
    if not hwnd:
        return
    try:
        user32 = ctypes.windll.user32
        if not user32.IsWindow(hwnd):
            return
        # Un-minimise ONLY: SW_RESTORE also drops a MAXIMISED window back to
        # its restored size, which would resize the browser the chain drives.
        try:
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        except Exception:
            pass
        user32.SetForegroundWindow(hwnd)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if user32.GetForegroundWindow() == hwnd:
                return
            time.sleep(0.01)
        # Our process no longer owns the foreground, so Windows refused the
        # call: borrow the right from the thread that does, then let go.
        fg = user32.GetForegroundWindow()
        tid_fg = user32.GetWindowThreadProcessId(fg, None) if fg else 0
        tid_me = ctypes.windll.kernel32.GetCurrentThreadId()
        if not tid_fg or tid_fg == tid_me:
            return
        user32.AttachThreadInput(tid_me, tid_fg, True)
        user32.SetForegroundWindow(hwnd)
        user32.AttachThreadInput(tid_me, tid_fg, False)
    except Exception:
        pass


class InputTextDialog(ModernDialog):
    """Single-line prompt wearing the app's dialog chrome.

    Same contract as ``QInputDialog.getText`` through :func:`ask_text`:
    ``text()`` returns what was typed, ``OK`` accepts and the window close
    button / Cancel reject.
    """

    def __init__(self, prompt, title="Input Required", default=""):
        super().__init__(
            None, title=title, help_topic="node-dialogs", show_help_button=False
        )
        self.setModal(True)
        # Always-on-top: playback runs with the chain's browser in front.
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.resize(420, 240)

        self.prompt_label = QLabel(prompt or "")
        self.prompt_label.setWordWrap(True)
        self.content_layout.addWidget(self.prompt_label)

        self._edit = QLineEdit(default or "")
        # Return must behave exactly like OK, so a typed answer can never look
        # "ignored" just because the button was not clicked.
        self._edit.returnPressed.connect(self.accept)
        self.content_layout.addWidget(self._edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        ok_btn = buttons.button(QDialogButtonBox.Ok)
        ok_btn.setText("OK")
        # The same "primary" accent the other dialogs give their OK button.
        ok_btn.setProperty("class", "primary")
        ok_btn.setDefault(True)
        ok_btn.setAutoDefault(True)
        buttons.button(QDialogButtonBox.Cancel).setText("Cancel")
        # Enter means OK here: Cancel must not become the dialog's default
        # button (which is what a focused auto-default button silently does),
        # or a submitted answer is thrown away instead of returned.
        cancel_btn = buttons.button(QDialogButtonBox.Cancel)
        cancel_btn.setAutoDefault(False)
        cancel_btn.setDefault(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.content_layout.addWidget(buttons)

        self._edit.setFocus()

    def text(self):
        return self._edit.text()

    def showEvent(self, event):
        """Take the field focus once the native window exists.

        A frameless always-on-top prompt can otherwise open with the title
        bar's close button holding focus, and then the answer is typed into a
        window that is not listening.
        """
        super().showEvent(event)
        QTimer.singleShot(0, self._edit.setFocus)

    def keyPressEvent(self, event):
        # Return/Enter accept from ANY focus position (a button, the scroll
        # area), not only from inside the text field.
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.accept()
            return
        super().keyPressEvent(event)


def _prompt_on_gui_thread(prompt, title, default):
    """Build and run the modal prompt - GUI thread only."""
    dlg = InputTextDialog(prompt, title=title, default=default)
    # The window is only addressable once exec_() has realised it, and Windows
    # needs a beat after that to apply the activation, so the foreground is
    # re-asserted twice (immediate + deferred) - otherwise keyboard focus
    # stays on the window that was covering the prompt.
    for _delay in (0, 80):
        QTimer.singleShot(_delay, lambda: (dlg.raise_(), dlg.activateWindow()))
    prev = _foreground_hwnd()
    ok = dlg.exec_() == QDialog.Accepted
    _restore_foreground(prev)
    text = dlg.text()
    # One line per prompt so a prompt that never answered (no line at all) is
    # distinguishable from one answered empty (ok=True, 0 chars).
    logger.info(
        "[INPUT] prompt closed: ok=%s, %d chars", ok, len(text)
    )
    return text, ok


class _PromptBridge(QObject):
    """Routes one prompt from a worker thread to the GUI thread.

    ``ask`` runs on the calling (worker) thread and blocks until the GUI
    thread has answered, so the caller keeps the blocking contract of a modal
    dialog without ever touching a widget itself.
    """

    _request = pyqtSignal(str, str, str)  # prompt, title, default

    def __init__(self):
        super().__init__()
        self._result = None
        # Move BEFORE connecting: PyQt binds the connection's slot proxy to the
        # receiver's thread at connect() time, so connecting first would leave
        # the proxy on the worker thread and every emit would deadlock
        # ("Dead lock detected while activating a BlockingQueuedConnection").
        self.moveToThread(QApplication.instance().thread())
        self._request.connect(self._run, Qt.BlockingQueuedConnection)

    def _run(self, prompt, title, default):
        # The answer rides on this attribute, NOT on a signal argument: PyQt
        # converts a list parameter to QVariantList, i.e. the slot would only
        # ever fill a copy and the caller would read an empty result.
        self._result = _prompt_on_gui_thread(prompt, title, default)

    def ask(self, prompt, title, default):
        self._result = None
        self._request.emit(prompt, title, default)
        return self._result


_bridge = None
_bridge_lock = threading.Lock()


def _ask_off_gui_thread(prompt, title, default):
    """Ask from a worker thread; None when there is no GUI to ask on."""
    global _bridge
    if QApplication.instance() is None:
        return None
    try:
        with _bridge_lock:
            if _bridge is None:
                _bridge = _PromptBridge()
            bridge = _bridge
        return bridge.ask(prompt, title, default)
    except Exception:
        logger.exception("[INPUT] could not route the prompt to the GUI thread")
        return None


def ask_text(prompt, title="Input Required", default=""):
    """Modal, always-on-top single-line prompt.

    Same contract as ``QInputDialog.getText``: returns ``(text, ok)``.

    Thread-safe: an off-GUI caller (scheduler / web chain playback runs the
    chain on a plain Python thread) gets the dialog on the GUI thread and
    blocks until it is answered.  Refusing is deliberate when no GUI exists -
    building widgets off the GUI thread is what killed the app silently.
    """
    app = QApplication.instance()
    if app is None or QThread.currentThread() is not app.thread():
        result = _ask_off_gui_thread(prompt, title, default)
        if result is None:
            logger.warning(
                "[INPUT] no GUI thread available for prompt %r - returning empty",
                prompt,
            )
            return "", False
        return result
    return _prompt_on_gui_thread(prompt, title, default)


# ---------------------------------------------------------------------------
# Rich question prompt (ask-user v2): yes/no buttons, choice buttons,
# text + file attachment — accepted-media aware.
# ---------------------------------------------------------------------------

_IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif'}


def _attachment_kind(path):
    """Classify an attached file for the ask-user v2 protocol."""
    ext = os.path.splitext(str(path or ''))[1].lower()
    if ext in _IMAGE_EXTS:
        return 'image'
    if ext:
        return 'document'
    return 'text'


class InputQuestionDialog(ModernDialog):
    """Rich runtime question: buttons for yes/no or choices, else a text
    field with an optional file attachment (only for accepted media)."""

    def __init__(self, prompt, kind="text", choices=None, accepts=None, default=""):
        super().__init__(
            None, title="Input Required", help_topic="node-dialogs", show_help_button=False
        )
        self.setModal(True)
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.resize(460, 330 if str(kind) == "text" else 280)
        self.kind = str(kind or "text")
        self.choices = list(choices or [])
        self.accepts = dict(accepts or {"text": True})
        self._value = None
        self._attachments = []

        label = QLabel(prompt or "")
        label.setWordWrap(True)
        self.content_layout.addWidget(label)

        if self.kind == "yes_no":
            row = QHBoxLayout()
            for _text, _val in (("Yes", "yes"), ("No", "no")):
                b = QPushButton(_text)
                if _val == "yes":
                    b.setProperty("class", "primary")
                b.setAutoDefault(False)
                b.clicked.connect(lambda _=False, v=_val: self._pick(v))
                row.addWidget(b)
            self.content_layout.addLayout(row)
        elif self.kind == "choice" and self.choices:
            # EVERY option must be LISTED and reachable: a long list (countries,
            # roles) overflowed the fixed-height dialog, and hiding it behind a
            # dropdown stopped it being listed at all.  The options therefore
            # live in a SCROLL AREA - as many as fit are visible, the rest
            # scroll, and nothing is cut or hidden.
            area = QScrollArea()
            area.setWidgetResizable(True)
            area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            holder = QWidget()
            vbox = QVBoxLayout(holder)
            vbox.setContentsMargins(0, 0, 0, 0)
            vbox.setSpacing(4)
            for c in self.choices:
                _label = str(c.get('label') or c.get('value') or '')
                _val = str(c.get('value') or c.get('label') or '')
                b = QPushButton(_label)
                b.setAutoDefault(False)
                b.clicked.connect(lambda _=False, v=_val: self._pick(v))
                vbox.addWidget(b)
            vbox.addStretch()
            area.setWidget(holder)
            self.content_layout.addWidget(area, 1)
            self.resize(460, 460)
        else:
            self._edit = QLineEdit(default or "")
            self._edit.returnPressed.connect(self.accept)
            self.content_layout.addWidget(self._edit)
            if self.accepts.get('images') or self.accepts.get('documents'):
                row = QHBoxLayout()
                self._attach_btn = QPushButton("Attach file...")
                self._attach_btn.setAutoDefault(False)
                self._attach_btn.clicked.connect(self._pick_file)
                row.addWidget(self._attach_btn)
                self._attach_label = QLabel("")
                self._attach_label.setWordWrap(True)
                row.addWidget(self._attach_label, 1)
                self.content_layout.addLayout(row)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        ok_btn = buttons.button(QDialogButtonBox.Ok)
        ok_btn.setText("OK")
        ok_btn.setProperty("class", "primary")
        ok_btn.setDefault(True)
        ok_btn.setAutoDefault(True)
        cancel_btn = buttons.button(QDialogButtonBox.Cancel)
        cancel_btn.setText("Cancel")
        cancel_btn.setAutoDefault(False)
        cancel_btn.setDefault(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.content_layout.addWidget(buttons)

        if self.kind == "text" and hasattr(self, '_edit'):
            self._edit.setFocus()

    def _pick(self, value):
        self._value = value
        self.accept()

    def _pick_file(self):
        filters = []
        if self.accepts.get('documents'):
            filters.append("Documents (*.pdf *.docx *.txt *.md *.csv *.json *.log)")
        if self.accepts.get('images'):
            filters.append("Images (*.png *.jpg *.jpeg *.webp *.bmp*.gif)")
        filters.append("All files (*)")
        path, _ = QFileDialog.getOpenFileName(
            self, "Attach file", "", ";;".join(filters),
        )
        if path:
            self._attachments = [{
                'kind': _attachment_kind(path),
                'path': path,
                'name': os.path.basename(path),
            }]
            self._attach_label.setText(os.path.basename(path))

    def result_payload(self):
        """(value, attachments) — None means cancelled."""
        if self.kind in ("yes_no", "choice"):
            return (self._value, [])
        text = self._edit.text() if hasattr(self, '_edit') else ''
        return (text, list(self._attachments))

    def showEvent(self, event):
        super().showEvent(event)
        if self.kind == "text" and hasattr(self, '_edit'):
            QTimer.singleShot(0, self._edit.setFocus)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.accept()
            return
        super().keyPressEvent(event)


def _prompt_question_on_gui_thread(request):
    """Build and run the rich modal prompt - GUI thread only."""
    dlg = InputQuestionDialog(
        request.get('question', ''),
        kind=request.get('kind', 'text'),
        choices=request.get('choices') or [],
        accepts=request.get('accepts') or {"text": True},
        default=request.get('default', ''),
    )
    for _delay in (0, 80):
        QTimer.singleShot(_delay, lambda: (dlg.raise_(), dlg.activateWindow()))
    prev = _foreground_hwnd()
    ok = dlg.exec_() == QDialog.Accepted
    _restore_foreground(prev)
    if not ok:
        return (None, [])
    return dlg.result_payload()


class _QuestionBridge(QObject):
    """Routes one rich question from a worker thread to the GUI thread."""

    _request = pyqtSignal(str)  # JSON-encoded request dict

    def __init__(self):
        super().__init__()
        self._result = None
        self.moveToThread(QApplication.instance().thread())
        self._request.connect(self._run, Qt.BlockingQueuedConnection)

    def _run(self, payload):
        # The result rides on this attribute (see _PromptBridge note).
        try:
            request = json.loads(payload)
        except Exception:
            request = {'question': payload}
        self._result = _prompt_question_on_gui_thread(request)

    def ask(self, request):
        self._result = None
        self._request.emit(json.dumps(request, ensure_ascii=False))
        return self._result


_question_bridge = None
_question_bridge_lock = threading.Lock()


def ask_question(request):
    """Rich modal prompt for the ask-user v2 protocol.

    ``request`` is the v2 dict (question / kind / choices / accepts / default);
    returns ``(value, attachments)``, or ``(None, [])`` when cancelled or when
    no GUI exists.  Thread-safe like :func:`ask_text`.
    """
    global _question_bridge
    request = request if isinstance(request, dict) else {'question': str(request)}
    app = QApplication.instance()
    if app is None or QThread.currentThread() is not app.thread():
        if app is None:
            logger.warning("[INPUT] no GUI thread available for question - refusing")
            return (None, [])
        try:
            with _question_bridge_lock:
                if _question_bridge is None:
                    _question_bridge = _QuestionBridge()
                bridge = _question_bridge
            result = bridge.ask(request)
        except Exception:
            logger.exception("[INPUT] could not route the question to the GUI thread")
            return (None, [])
        return result if result is not None else (None, [])
    return _prompt_question_on_gui_thread(request)
