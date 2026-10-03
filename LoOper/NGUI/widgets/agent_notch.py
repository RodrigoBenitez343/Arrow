"""Compact "notch" for agent mode: a small semi-transparent bar pinned to the
top-center of the screen, with a text OR voice input for a quick request.

This is the summonable compact state of agent mode. The agent hotkey raises it
(the builder/graph is hidden while it is up - the graph UI and the agent UI are
never shown at the same time).

Two modes, switched by the mic/keyboard button at the right:
  * text  - type in the field and press Enter (or the send button).
  * voice - hold the mic button to talk; on release the speech is transcribed
            and submitted.
Either way the transcribed / typed query opens the full in-window panel.

The bar carries a soft cyan "fog" (a drop shadow), not a hard border, matching
the float treatment of the graph's floating bars and the dialogs.
"""

import logging
import threading

from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (QApplication, QFrame, QGraphicsDropShadowEffect,
                             QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget)

from ..constants import ACCENT_COLOR, DANGER_COLOR, TEXT_COLOR, TEXT_MUTED
from ..i18n import _
from ..icons import tabler_qicon
from .hover_button import HoverGlowButton

logger = logging.getLogger(__name__)

NOTCH_WIDTH = 480             # wider than a pill: it holds an input + buttons
NOTCH_HEIGHT = 40
NOTCH_GLOW = 6                # transparent room around the bar for the fog
NOTCH_TOP_MARGIN = 6
NOTCH_RADIUS = NOTCH_HEIGHT // 2
NOTCH_BG = "rgba(11, 14, 18, 185)"   # semi-transparent bar fill
FOG_BLUR = 12                 # soft cyan fog around the bar
FOG_ALPHA = 90
BUTTON_PX = 32                # bigger action / mode buttons
ICON_PX = 18
AVATAR_PX = 26                # system-chain avatar in the left slot
_CAPTURE_HIDE_S = 0.3         # stay off-screen for a screen read (like the overlay)

_PLACEHOLDER_TEXT = "Ask the agent…  (Enter to send)"
_PLACEHOLDER_VOICE = "Hold the mic to talk"


class AgentNotch(QWidget):
    """Semi-transparent top-center agent bar: quick text/voice input + launcher."""

    clicked = pyqtSignal()          # bar (not the input) clicked
    submitted = pyqtSignal(str)     # text query ready to send
    voice_submitted = pyqtSignal(str)  # transcribed speech ready to send
    _hideRequested = pyqtSignal()   # capture-hide request from the replay thread

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("agentNotchRoot")

        # Frameless, always-on-top, no taskbar entry, no focus stealing.
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)

        self.setFixedSize(NOTCH_WIDTH + 2 * NOTCH_GLOW,
                          NOTCH_HEIGHT + 2 * NOTCH_GLOW)
        self.setToolTip(_("Agent — type a request, or click to open the panel"))

        self._execute_mode = False
        self._voice_mode = False
        self._recording = False
        self._thread = None
        self._chunks = []
        self._capture_hidden = False
        # The player calls hide_for_capture() from the replay thread; route it to
        # the GUI thread and block until the window is really hidden, so a screen
        # read can never contain the notch (same contract as the execution
        # overlay).
        self._hideRequested.connect(self._hide_now, Qt.BlockingQueuedConnection)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(NOTCH_GLOW, NOTCH_GLOW, NOTCH_GLOW, NOTCH_GLOW)

        self._bar = QFrame(self)
        self._bar.setObjectName("agentNotchBar")
        # No border: the bar reads as floating thanks to the cyan fog below.
        self._bar.setStyleSheet(
            f"QFrame#agentNotchBar {{"
            f"  background-color: {NOTCH_BG};"
            f"  border: none;"
            f"  border-radius: {NOTCH_RADIUS}px;"
            f"}}"
        )
        outer.addWidget(self._bar)

        # Soft cyan fog (the same treatment as the floating bars / dialogs).
        self._fog = QGraphicsDropShadowEffect(self._bar)
        self._fog.setBlurRadius(FOG_BLUR)
        self._fog.setOffset(0, 0)
        self._bar.setGraphicsEffect(self._fog)

        row = QHBoxLayout(self._bar)
        row.setContentsMargins(14, 0, 8, 0)
        row.setSpacing(6)

        self._avatar = QLabel(self._bar)
        self._avatar.setFixedSize(AVATAR_PX, AVATAR_PX)
        self._avatar.setAlignment(Qt.AlignCenter)
        # The app-wide stylesheet paints QWidget with DARK_GREY, which showed as
        # a dark mat behind the avatar; keep the label transparent so only the
        # glyph is drawn.
        self._avatar.setStyleSheet("background: transparent;")
        row.addWidget(self._avatar)
        # Left slot: the SELECTED system chain's vector avatar, so it is clear
        # who the user is talking to (it replaced the plain status dot).
        self._chain_icon = "ROBOT"     # until a system chain is selected

        self._input = QLineEdit(self._bar)
        self._input.setPlaceholderText(_(_PLACEHOLDER_TEXT))
        self._input.setStyleSheet(
            f"QLineEdit {{"
            f"  background: transparent; border: none;"
            f"  color: {TEXT_COLOR}; font-size: 12px;"
            f"  selection-background-color: {ACCENT_COLOR};"
            f"}}"
            f"QLineEdit::placeholder {{ color: {TEXT_MUTED}; }}"
        )
        self._input.returnPressed.connect(self._on_return)
        row.addWidget(self._input, 1)

        # Primary action: send the typed query, or (voice mode) hold to talk.
        self._action_btn = HoverGlowButton(
            "SEND", size=BUTTON_PX, icon=ICON_PX, parent=self._bar)
        self._action_btn.setToolTip(_("Send"))
        self._action_btn.clicked.connect(self._on_action_clicked)
        self._action_btn.pressed.connect(self._on_action_pressed)
        self._action_btn.released.connect(self._on_action_released)
        row.addWidget(self._action_btn)

        # Voice / text mode switch.
        self._mode_btn = HoverGlowButton(
            "MICROPHONE", size=BUTTON_PX, icon=ICON_PX, parent=self._bar)
        self._mode_btn.setCheckable(True)
        self._mode_btn.setToolTip(_("Switch to voice mode"))
        self._mode_btn.toggled.connect(self._on_mode_toggled)
        row.addWidget(self._mode_btn)

        self._apply_accent()
        self.reposition()

    # ── Appearance ────────────────────────────────────────────────────

    def _accent(self):
        return QColor(DANGER_COLOR if self._execute_mode else ACCENT_COLOR)

    def _apply_accent(self):
        accent = self._accent()
        self._render_avatar(accent.name())
        fog = QColor(accent)
        fog.setAlpha(FOG_ALPHA)
        self._fog.setColor(fog)

    def _render_avatar(self, color):
        """Paint the chain avatar in *color*, so the run state stays readable."""
        icon = tabler_qicon(self._chain_icon, AVATAR_PX, color)
        if icon.isNull():
            self._avatar.clear()
        else:
            self._avatar.setPixmap(icon.pixmap(AVATAR_PX, AVATAR_PX))

    def set_chain_avatar(self, icon_name):
        """Show the selected system chain's vector avatar in the left slot.

        Takes a Tabler icon name; an empty/unknown name falls back to the
        generic agent glyph.  The icon is tinted by the current state colour,
        so the avatar also carries the idle/executing signal.
        """
        self._chain_icon = icon_name or "ROBOT"
        self._render_avatar(self._accent().name())

    def set_execute(self, executing: bool):
        """Cyan fog/avatar while idle, red while executing (or recording) voice."""
        if self._execute_mode != bool(executing):
            self._execute_mode = bool(executing)
            self._apply_accent()

    # ── Positioning ───────────────────────────────────────────────────

    def reposition(self):
        """Pin to the top-center of the primary screen."""
        try:
            screen = QApplication.primaryScreen()
            if screen is None:
                return
            geo = screen.availableGeometry()
            self.move(geo.center().x() - self.width() // 2,
                      geo.top() + NOTCH_TOP_MARGIN)
        except Exception:
            logger.exception("AgentNotch.reposition failed")

    # ── Capture hiding (the agent must never see the notch) ────────────

    def hide_for_capture(self):
        """Leave the screen for a screen read, then come straight back.

        The notch stays up while a chain runs, so it has to be taken out of
        every capture the agent takes (template matching / OCR would otherwise
        see the bar).  Called by the player from the replay thread; marshals to
        the GUI thread and blocks until hidden, like the execution overlay.
        """
        try:
            if QThread.currentThread() == self.thread():
                self._hide_now()
            else:
                self._hideRequested.emit()
        except Exception:
            logger.exception("AgentNotch.hide_for_capture failed")

    def _hide_now(self):
        """GUI thread: hide for the read, restore a beat later."""
        if not self.isVisible():
            return
        self._capture_hidden = True
        self.hide()
        QTimer.singleShot(int(_CAPTURE_HIDE_S * 1000),
                          self._restore_after_capture)

    def _restore_after_capture(self):
        """GUI thread: put the notch back once the read is done.

        ponytail: a plain show()/raise_(); if the user clicks the notch inside
        the 300 ms window the panel wins anyway (it hides the notch again).
        """
        if not self._capture_hidden:
            return
        self._capture_hidden = False
        self.reposition()
        self.show()
        self.raise_()

    # ── Interaction ───────────────────────────────────────────────────

    def _on_return(self):
        text = self._input.text().strip()
        if text:
            self._input.clear()
            self.submitted.emit(text)

    def _on_action_clicked(self):
        # In voice mode the press/release handlers do the work.
        if self._voice_mode:
            return
        self._on_return()

    def _on_action_pressed(self):
        if self._voice_mode:
            self._start_recording()

    def _on_action_released(self):
        if self._voice_mode:
            self._stop_recording()

    def _on_mode_toggled(self, checked):
        """Switch the bar between text and voice input."""
        self._voice_mode = bool(checked)
        if self._voice_mode:
            self._input.setEnabled(False)
            self._input.setPlaceholderText(_(_PLACEHOLDER_VOICE))
            self._action_btn.set_icon_name("MICROPHONE")
            self._action_btn.setToolTip(_("Hold to talk"))
            self._mode_btn.set_icon_name("KEYBOARD")
            self._mode_btn.setToolTip(_("Switch to text mode"))
        else:
            self._stop_recording()
            self._input.setEnabled(True)
            self._input.setPlaceholderText(_(_PLACEHOLDER_TEXT))
            self._action_btn.set_icon_name("SEND")
            self._action_btn.setToolTip(_("Send"))
            self._mode_btn.set_icon_name("MICROPHONE")
            self._mode_btn.setToolTip(_("Switch to voice mode"))

    def mouseReleaseEvent(self, event):
        # Plain clicks on the bar (the input/buttons swallow their own) open the panel.
        if event.button() == Qt.LeftButton and self.rect().contains(event.pos()):
            self.clicked.emit()

    # ── Voice capture (push-to-talk) ──────────────────────────────────

    def _start_recording(self):
        if self._recording:
            return
        try:
            import pyaudio  # noqa: F401
        except ImportError:
            logger.warning("AgentNotch: voice unavailable — pyaudio not installed")
            return
        self._recording = True
        self._chunks = []
        self.set_execute(True)   # red dot/fog = recording
        self._thread = threading.Thread(target=self._record_worker, daemon=True)
        self._thread.start()

    def _record_worker(self):
        """Capture 16 kHz mono PCM16 chunks while recording is active."""
        stream = None
        pya = None
        try:
            import pyaudio
            pya = pyaudio.PyAudio()
            stream = pya.open(format=pyaudio.paInt16, channels=1, rate=16000,
                              input=True, frames_per_buffer=4096)
            while self._recording:
                try:
                    data = stream.read(4096, exception_on_overflow=False)
                except Exception:
                    break
                if data:
                    self._chunks.append(data)
        except Exception:
            logger.exception("AgentNotch: mic recording failed")
        finally:
            if stream is not None:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
            if pya is not None:
                try:
                    pya.terminate()
                except Exception:
                    pass

    def _stop_recording(self):
        if not self._recording:
            return
        self._recording = False
        self.set_execute(False)
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None
        chunks = self._chunks
        self._chunks = []
        # ~100 ms of audio = an accidental tap; ignore.
        if not chunks or sum(len(c) for c in chunks) < 3200:
            return
        pcm = b"".join(chunks)
        threading.Thread(target=self._transcribe, args=(pcm,), daemon=True).start()

    def _transcribe(self, pcm):
        """Transcribe off the UI thread, then emit the text (queued to the UI)."""
        text = ""
        try:
            from player.stt_engine import get_stt_engine
            result = get_stt_engine().transcribe(pcm, 16000)
            if not result.get("error"):
                text = (result.get("text") or "").strip()
        except Exception:
            logger.exception("AgentNotch: transcription failed")
        if text:
            self.voice_submitted.emit(text)
