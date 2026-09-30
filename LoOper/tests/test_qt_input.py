"""Tests for the always-on-top Input-node prompt.

The prompt used to be a bare ``QInputDialog.getText(None, ...)``: no owner, no
top-most flag and the platform's default chrome, so during playback (main
window minimized, a browser window opened by a web sequence on top) it could
open *behind* another window and the user's Enter / OK click never reached it.
Every prompt site now goes through ``ask_text`` (a ``ModernDialog``), which
must keep the dialog on top, accept Return like OK, and hand the foreground
back to the window the chain is driving.
"""
import os
import threading
import time
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt, QTimer  # noqa: E402
from PyQt5.QtTest import QTest  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication, QDialog, QDialogButtonBox, QLineEdit,
)

_app = QApplication.instance() or QApplication([])

import player.qt_input as qt_input  # noqa: E402
from player.qt_input import ask_text  # noqa: E402


def _accept_immediately(monkeypatch, seen):
    def _fake_exec(self):
        seen["dlg"] = self
        return QDialog.Accepted

    monkeypatch.setattr(QDialog, "exec_", _fake_exec)


def test_a_long_choice_list_lists_every_option(monkeypatch):
    """A form's option list can be long (countries, roles).  A fixed-height
    dialog clipped the tail and a dropdown hid the list entirely - every option
    must be LISTED and reachable."""
    from PyQt5.QtWidgets import QPushButton, QScrollArea

    seen = {}
    _accept_immediately(monkeypatch, seen)

    choices = [{"label": "Country %d" % i, "value": "Country %d" % i}
               for i in range(1, 61)]
    qt_input.ask_question(
        {"question": "Pick", "kind": "choice", "choices": choices})

    dlg = seen["dlg"]
    labels = {b.text() for b in dlg.findChildren(QPushButton)}
    assert "Country 1" in labels and "Country 60" in labels   # tail included
    assert dlg.findChild(QScrollArea) is not None             # and reachable


def test_a_choice_list_click_is_the_answer(monkeypatch):
    """Clicking an option IS the answer - no dropdown, no OK needed."""
    from PyQt5.QtWidgets import QPushButton

    seen = {}
    _accept_immediately(monkeypatch, seen)

    choices = [{"label": "Yes", "value": "Yes"},
               {"label": "No", "value": "No"}]
    qt_input.ask_question(
        {"question": "Pick", "kind": "choice", "choices": choices})

    dlg = seen["dlg"]
    yes = next(b for b in dlg.findChildren(QPushButton) if b.text() == "Yes")
    yes.click()
    assert dlg._value == "Yes"


def test_ask_text_prompt_is_always_on_top(monkeypatch):
    seen = {}
    _accept_immediately(monkeypatch, seen)

    text, ok = ask_text("What to search?", default="milo j")

    assert ok is True
    assert text == "milo j"
    assert seen["dlg"].prompt_label.text() == "What to search?"
    assert seen["dlg"].windowFlags() & Qt.WindowStaysOnTopHint


def test_ask_text_restores_the_window_that_had_the_foreground(monkeypatch):
    """The prompt is modal and takes the foreground; the window the Input node
    will type into (the browser a web sequence focused) must get it back, or
    the keystrokes land on whatever app kept the focus instead."""
    seen = {}
    _accept_immediately(monkeypatch, seen)
    restored = []
    monkeypatch.setattr(qt_input, "_foreground_hwnd", lambda: 0xABCD)
    monkeypatch.setattr(qt_input, "_restore_foreground", restored.append)

    ask_text("Question?")

    assert restored == [0xABCD]


def test_ask_text_return_key_accepts_like_ok(monkeypatch):
    """Return must accept the prompt, so a typed answer can never look
    "ignored" just because the OK button was not clicked."""
    seen = {}
    _accept_immediately(monkeypatch, seen)

    ask_text("Question?")

    dlg = seen["dlg"]
    dlg.findChild(QLineEdit).returnPressed.emit()
    assert dlg.result() == QDialog.Accepted


def test_enter_never_belongs_to_cancel():
    """The title-bar ✕ is wired to reject().  A push button inside a dialog is
    auto-default, so a focusable ✕ takes the default-button role and Enter
    activates it - the answer the user had just typed is thrown away.  OK must
    stay the default and the ✕ must never be able to take focus."""
    dlg = qt_input.InputTextDialog("Question?")
    try:
        assert dlg.close_btn.autoDefault() is False
        assert dlg.close_btn.focusPolicy() == Qt.NoFocus
        box = dlg.findChild(QDialogButtonBox)
        assert box.button(QDialogButtonBox.Ok).isDefault() is True
        assert box.button(QDialogButtonBox.Cancel).autoDefault() is False
    finally:
        dlg.close()


def test_return_on_the_close_button_never_rejects_the_prompt():
    """End-to-end for the same bug: even asked to focus the ✕ and press Enter,
    the prompt must not reject and lose what was typed."""
    dlg = qt_input.InputTextDialog("Question?")
    dlg._edit.setText("milo j")
    rejected = []
    dlg.rejected.connect(lambda: rejected.append(True))
    try:
        dlg.close_btn.setFocus()
        QTest.keyClick(dlg.close_btn, Qt.Key_Return)
        assert rejected == []
    finally:
        dlg.close()


def test_ask_text_from_a_worker_thread_is_marshaled(monkeypatch):
    """A chain runs on a plain Python thread (scheduler / web playback) and its
    Input node may still need to prompt.  Building the dialog on that thread is
    undefined behavior: Qt warns "QObject::startTimer: Timers can only be used
    with threads started with QThread" and the whole app dies silently, with no
    Python traceback.  The prompt must be handed to the GUI thread instead.
    """
    monkeypatch.setattr(
        qt_input, "_prompt_on_gui_thread",
        lambda prompt, title, default: ("milo j", True),
    )
    box = {}

    worker = threading.Thread(
        target=lambda: box.setdefault("result", ask_text("What to search?")),
        # daemon: a regressed bridge deadlocks in emit, and a non-daemon thread
        # would then keep the whole pytest process alive at exit.
        daemon=True,
    )
    worker.start()
    # Play the GUI thread's part: a BlockingQueuedConnection only reaches its
    # slot while that thread pumps events.
    deadline = time.time() + 5
    while worker.is_alive() and time.time() < deadline:
        _app.processEvents()
        time.sleep(0.01)
    worker.join(timeout=1)

    assert box.get("result") == ("milo j", True)


def test_enter_in_the_live_prompt_accepts_and_returns_the_text(monkeypatch):
    """End-to-end: a real Return keypress in the SHOWN dialog accepts it, so a
    typed answer is never lost just because OK was not clicked."""
    seen = {}
    real_init = qt_input.InputTextDialog.__init__

    def _init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        seen["dlg"] = self

    monkeypatch.setattr(qt_input.InputTextDialog, "__init__", _init)

    def _type_and_press_enter():
        edit = seen["dlg"].findChild(QLineEdit)
        QTest.keyClicks(edit, "milo j")
        QTest.keyClick(edit, Qt.Key_Return)

    # Fail fast instead of hanging if Return never accepts the dialog.
    def _give_up():
        if "dlg" in seen:
            seen["dlg"].reject()

    QTimer.singleShot(50, _type_and_press_enter)
    QTimer.singleShot(3000, _give_up)

    text, ok = ask_text("What to search?")

    assert ok is True
    assert text == "milo j"


def test_return_accepts_even_when_the_field_holds_no_focus(monkeypatch):
    """The answer must not be lost because focus sits on a button or the
    scroll area when Return is pressed."""
    seen = {}
    real_init = qt_input.InputTextDialog.__init__

    def _init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        seen["dlg"] = self

    monkeypatch.setattr(qt_input.InputTextDialog, "__init__", _init)

    def _type_then_enter_outside_the_field():
        dlg = seen["dlg"]
        dlg._edit.setText("milo j")
        dlg._edit.clearFocus()
        QTest.keyClick(dlg, Qt.Key_Return)

    def _give_up():
        if "dlg" in seen:
            seen["dlg"].reject()

    QTimer.singleShot(50, _type_then_enter_outside_the_field)
    QTimer.singleShot(3000, _give_up)

    text, ok = ask_text("What to search?")

    assert ok is True
    assert text == "milo j"


def test_prompt_gives_the_field_focus_when_shown():
    dlg = qt_input.InputTextDialog("Question?")
    dlg.show()
    _app.processEvents()
    try:
        assert dlg._edit.hasFocus()
    finally:
        dlg.close()


class _FakeUser32:
    """Minimal user32 double recording what _restore_foreground does."""

    def __init__(self, iconic):
        self._iconic = iconic
        self._foreground = None
        self.calls = []

    def IsWindow(self, hwnd):
        return True

    def IsIconic(self, hwnd):
        return self._iconic

    def ShowWindow(self, hwnd, command):
        self.calls.append(("ShowWindow", command))
        return True

    def SetForegroundWindow(self, hwnd):
        self.calls.append(("SetForegroundWindow", hwnd))
        self._foreground = hwnd
        return True

    def GetForegroundWindow(self):
        return self._foreground


def _install_fake_user32(monkeypatch, iconic):
    user32 = _FakeUser32(iconic)
    kernel32 = types.SimpleNamespace(GetCurrentThreadId=lambda: 1)
    monkeypatch.setattr(
        qt_input.ctypes, "windll",
        types.SimpleNamespace(user32=user32, kernel32=kernel32),
        raising=False,
    )
    return user32


def test_restore_foreground_never_unmaximises_the_target(monkeypatch):
    """SW_RESTORE also drops a MAXIMISED window back to its restored size, so
    it must be used on a minimised window only - otherwise the Input node
    silently resizes the browser it is driving."""
    user32 = _install_fake_user32(monkeypatch, iconic=False)

    qt_input._restore_foreground(0x1234)

    assert ("SetForegroundWindow", 0x1234) in user32.calls
    assert [c for c in user32.calls if c[0] == "ShowWindow"] == []


def test_restore_foreground_unminimises_an_iconic_window(monkeypatch):
    user32 = _install_fake_user32(monkeypatch, iconic=True)

    qt_input._restore_foreground(0x1234)

    assert ("ShowWindow", 9) in user32.calls
    assert ("SetForegroundWindow", 0x1234) in user32.calls
