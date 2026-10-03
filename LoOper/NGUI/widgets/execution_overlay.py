"""Desktop EXECUTION overlay: the orange twin of the recording overlay.

While a chain replays (native pyautogui path), a frameless, click-through,
always-on-top window draws:

* an orange box + name over the UI Automation element at the point the replay
  is moving to - the element it is about to act on;
* a fading orange trail of the cursor's recent waypoints plus a ring pulsed at
  every click, so the path and the action points are visible even when the
  physical pointer is not;
* a top-left banner naming the action currently executing.

Unlike ``RecordingOverlay`` this window is kept OUT OF every screen read: the
player calls ``bus.hide_overlay_for_capture()`` before it captures the screen
for template matching / OCR, which marshals to the GUI thread and blocks until
the window is hidden.  Windows' own ``WDA_EXCLUDEFROMCAPTURE`` is deliberately
NOT used - on a translucent (layered) window that affinity conflicts with Qt's
layered rendering and the overlay stops drawing.  The window is also shown only
while the feed has something to draw, so a chain that never drives the desktop
(a web-only chain) puts nothing on screen at all - a full-screen always-on-top
window there made the browser flicker.

The element hit-test reuses the recording overlay's UIA helper; the cursor
trail/action come from ``player.execution_overlay_bus``, which the replay
(worker thread) feeds.  Coordinates: UIA/pyautogui report PHYSICAL pixels, the
app runs high-DPI scaling so widgets are LOGICAL - marks are scaled by 1/dpr.

ponytail: a full-window QPainter repaint at 30 fps; Qt paints a handful of
lines cheaply, so stop repainting (self._busy) once the trail has aged out if
this ever shows as CPU.
"""
from __future__ import annotations

import logging
import collections
import threading
import time

from PyQt5.QtCore import QRect, Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QPainter, QPen
from PyQt5.QtWidgets import QApplication, QLabel, QWidget

try:
    from player.execution_overlay_bus import bus as _bus
except Exception:  # keep the GUI importable if the player package is absent
    _bus = None

from .log_bubbles import LogBubbleList
from .recording_overlay import element_at  # reuse the UIA hit-test

logger = logging.getLogger(__name__)

_MARGIN = 12
_DRAW_MS = 33          # 30 fps is plenty for a fading trail
_HIT_IDLE_S = 0.03
_TRAIL_TTL = 5.0       # seconds a trail segment / box / click stays drawable
_ORANGE = (255, 140, 0)
_HIDE_AFTER_READ_S = 0.3   # stay off-screen this long after a screen read

# Shared "logging window" palette: the action banner uses the SAME colours as
# the log panel so the two read as one surface.
_PANEL_BG = "rgba(8,12,16,0.84)"
_PANEL_TEXT = "#d5d9e2"
_PANEL_BORDER = "rgba(255,255,255,0.10)"

_BANNER_QSS = (
    f"background: {_PANEL_BG}; color: {_PANEL_TEXT};"
    f"border: 1px solid {_PANEL_BORDER}; border-radius: 6px;"
    "padding: 8px 12px; font: 12px monospace;"
)

# Top-left column of per-entry bubbles (the terminal renders every record as
# its own boxed panel; the overlay mirrors that look). Drawn ON this overlay,
# which the player hides before every screen read - so it is visible to the
# USER but never to the agent (the agent only ever sees screen captures).
_LOG_BUBBLES = 8       # how many recent entries the panel shows
_LOG_TTL = 6.0         # seconds an entry keeps the panel on screen
_LOG_WIDTH = 460       # FIXED width: the panel only grows vertically


class _OverlayLogHandler(logging.Handler):
    """Thread-safe ring buffer of recent log records for the overlay panel.

    Purely a display sink: it never feeds the agent's context.
    """

    def __init__(self, capacity=400):
        super().__init__(level=logging.INFO)
        self._buf = collections.deque(maxlen=capacity)
        self._lock = threading.Lock()

    def emit(self, record):
        try:
            self._buf.append((time.time(), record.levelname,
                              record.name.split(".")[-1],
                              record.getMessage()))
        except Exception:
            pass

    def recent(self, n, now=None, ttl=None):
        now = time.time() if now is None else now
        with self._lock:
            items = list(self._buf)
        if ttl is not None:
            items = [it for it in items if now - it[0] <= ttl]
        return items[-n:]


class ExecutionOverlay(QWidget):
    """Orange element marker + cursor trail + action banner for replay."""

    # Emitted (and blocked on) from the replay thread so the hide lands before
    # the screen is captured - see hide_for_capture / _hide_now.
    _hideRequested = pyqtSignal()

    def __init__(self, stop_event: threading.Event, parent=None):
        super().__init__(parent)
        self._stop = stop_event
        self._kill = threading.Event()
        self._hit = None
        self._hit_lock = threading.Lock()
        self._snap = {"action": "", "target": None, "trail": [], "clicks": []}
        self._banner_text = ""
        self._dpr = 1.0
        self._hidden_until = 0.0  # keep off-screen while the replay reads screens

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowTransparentForInput
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._hideRequested.connect(self._hide_now, Qt.BlockingQueuedConnection)
        if _bus is not None:
            try:
                _bus.capture_hook = self.hide_for_capture
            except Exception:
                pass

        self._banner = QLabel(self)
        self._banner.setStyleSheet(_BANNER_QSS)
        self._banner.setText("EXECUTING")
        self._banner.adjustSize()

        # Top-left bubble column (user-only; see the palette note).
        self._log_handler = _OverlayLogHandler()
        self._log_panel = LogBubbleList(_LOG_WIDTH, _LOG_BUBBLES, self)
        self._log_panel.hide()

        self._timer = QTimer(self)
        self._timer.setInterval(_DRAW_MS)
        self._timer.timeout.connect(self._tick)

        self._worker = threading.Thread(target=self._hit_loop, daemon=True)

    # ------------------------------------------------------------------ API

    def start(self) -> None:
        """Cover the virtual desktop and begin marking the replay."""
        try:
            self._dpr = float(QApplication.primaryScreen().devicePixelRatio()) or 1.0
        except Exception:
            self._dpr = 1.0
        self.setGeometry(self._virtual_geometry())
        self._banner.move(_MARGIN, _MARGIN)
        self._banner.show()
        # Feed the log panel from the app's root logger (INFO and up).
        try:
            logging.getLogger().addHandler(self._log_handler)
        except Exception:
            pass
        self._worker.start()
        self._timer.start()
        # The window is NOT shown here: it appears only while the feed has
        # something to draw (see _tick), so a chain that never drives the
        # desktop puts nothing on screen (that is what made the browser flicker
        # during web replay).

    def stop(self) -> None:
        """Hide and stop both the draw timer and the hit-test thread."""
        if _bus is not None and getattr(_bus, "capture_hook", None) == self.hide_for_capture:
            try:
                _bus.capture_hook = None
            except Exception:
                pass
        self._kill.set()
        try:
            logging.getLogger().removeHandler(self._log_handler)
        except Exception:
            pass
        self._timer.stop()
        self.hide()

    def hide_for_capture(self) -> None:
        """Replay thread: get off the screen for a screen read, then return."""
        try:
            if QThread.currentThread() == self.thread():
                self._hide_now()   # same thread (no event loop): hide directly
            else:
                self._hideRequested.emit()
        except Exception:
            pass

    def _hide_now(self) -> None:
        """GUI thread: hide for a screen read and stay off briefly after it."""
        self._hidden_until = time.time() + _HIDE_AFTER_READ_S
        if not self.isHidden():
            self.hide()

    # --------------------------------------------------------------- internal

    def _virtual_geometry(self) -> QRect:
        """Union of every screen, so marks follow across monitors."""
        geo = QRect()
        for screen in QApplication.screens():
            geo = geo.united(screen.geometry())
        if geo.isNull():
            primary = QApplication.primaryScreen()
            if primary is not None:
                return primary.geometry()
        return geo

    def _local(self, x, y):
        """Physical (UIA/pyautogui) point -> widget-local logical int point."""
        dpr = self._dpr or 1.0
        return (int(round(x / dpr - self.x())), int(round(y / dpr - self.y())))

    def _hit_loop(self) -> None:
        """Worker: UIA hit-test the point the replay is moving to.

        Driven by the feed, NOT by the live cursor.  Sampling the cursor every
        ~30 ms threw thousands of ElementFromPoint calls at the OS
        accessibility layer per run - which made the marks follow the USER's
        own mouse during playback and disturbed the app the replay was driving.
        One hit-test per published target is enough to box the element the
        replay is about to act on.
        """
        last_target_ts = 0.0
        while not (self._stop.is_set() or self._kill.is_set()):
            target = _bus.snapshot().get("target") if _bus is not None else None
            if target is not None and target[2] > last_target_ts:
                last_target_ts = target[2]
                # Compute the hit OUTSIDE the lock.  UIA can block - and over
                # our own full-desktop overlay window it can wait on THIS
                # process's UI thread - so holding _hit_lock across element_at()
                # would deadlock the GUI's paintEvent (which takes the same
                # lock).
                hit = element_at(int(target[0]), int(target[1]))
                if hit is not None:
                    hit["ts"] = target[2]
                with self._hit_lock:
                    self._hit = hit
            time.sleep(_HIT_IDLE_S)

    def _tick(self) -> None:
        """GUI timer: read the feed, refresh the banner, repaint."""
        if self._stop.is_set():
            self.stop()
            return
        self._snap = _bus.snapshot() if _bus is not None else self._snap
        # The action sits BESIDE the EXECUTING tag (one line), not under it.
        text = "EXECUTING  \u00b7  " + (self._snap.get("action") or "...")
        if text != self._banner_text:
            self._banner_text = text
            self._banner.setText(text)
            self._banner.adjustSize()
        now = time.time()
        # --- log bubbles: last few entries, top-left under the banner ---
        entries = self._log_handler.recent(_LOG_BUBBLES, now, ttl=_LOG_TTL)
        if entries:
            self._log_panel.set_entries([
                (time.strftime("%H:%M:%S", time.localtime(t)), lvl, comp, msg)
                for (t, lvl, comp, msg) in entries])
            top = _MARGIN
            if not self._banner.isHidden():
                top = _MARGIN + self._banner.height() + 6
            self._log_panel.move(_MARGIN, top)
            if self._log_panel.isHidden():
                self._log_panel.show()
        elif not self._log_panel.isHidden():
            self._log_panel.hide()
        # Stay off-screen while the replay reads screens, and otherwise occupy
        # the screen ONLY while there is something to draw: a full-screen
        # always-on-top window on a web-only stretch is pure interference (it
        # made the browser flicker).  The content TTL hides it again.
        if now < self._hidden_until:
            if not self.isHidden():
                self.hide()
            return
        if self._has_content(now):
            if self.isHidden():
                self.show()
                self.raise_()
            self.update()
        elif not self.isHidden():
            self.hide()

    def _has_content(self, now) -> bool:
        """True while any trail point, click ring or target box is fresh."""
        if self._log_handler.recent(1, now, ttl=_LOG_TTL):
            return True
        if any(now - t <= _TRAIL_TTL for (_, _, t) in (self._snap.get("trail") or [])):
            return True
        if any(now - t <= _TRAIL_TTL for (_, _, t) in (self._snap.get("clicks") or [])):
            return True
        with self._hit_lock:
            hit = self._hit
        return bool(hit and hit.get("rect")
                    and now - hit.get("ts", 0.0) <= _TRAIL_TTL)

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt name)
        now = time.time()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)

        trail = [(x, y, t) for (x, y, t) in self._snap.get("trail", [])
                 if now - t <= _TRAIL_TTL]
        # --- cursor trail: a fading polyline of recent waypoints ---
        if len(trail) >= 2:
            pen = QPen()
            pen.setWidth(2)
            pen.setCapStyle(Qt.RoundCap)
            for (ax, ay, _), (bx, by, bt) in zip(trail, trail[1:]):
                alpha = int(210 * max(0.0, 1.0 - (now - bt) / _TRAIL_TTL))
                if alpha <= 0:
                    continue
                pen.setColor(QColor(_ORANGE[0], _ORANGE[1], _ORANGE[2], alpha))
                p.setPen(pen)
                p.drawLine(*self._local(ax, ay), *self._local(bx, by))
            # live head
            hx, hy, _ = trail[-1]
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(_ORANGE[0], _ORANGE[1], _ORANGE[2], 235))
            p.drawEllipse(*self._local(hx, hy), 3, 3)

        # --- click rings: expand + fade out from the click instant ---
        for (cx, cy, ct) in self._snap.get("clicks", []):
            age = now - ct
            if age > _TRAIL_TTL:
                continue
            radius = 6 + 12 * min(1.0, age / 0.6)
            alpha = int(220 * max(0.0, 1.0 - age / _TRAIL_TTL))
            p.setPen(QPen(QColor(_ORANGE[0], _ORANGE[1], _ORANGE[2], alpha), 2))
            p.setBrush(Qt.NoBrush)
            px, py = self._local(cx, cy)
            rad = int(radius)
            p.drawEllipse(px - rad, py - rad, rad * 2, rad * 2)

        # --- the element the replay is acting on: orange box + name chip ---
        with self._hit_lock:
            hit = self._hit
        if hit and hit.get("rect") and now - hit.get("ts", 0.0) <= _TRAIL_TTL:
            l, t = self._local(hit["rect"][0], hit["rect"][1])
            r, b = self._local(hit["rect"][2], hit["rect"][3])
            if not (l <= 0 and t <= 0 and r >= self.width() and b >= self.height()):
                p.setPen(QPen(QColor(*_ORANGE), 2))
                p.setBrush(Qt.NoBrush)
                p.drawRect(l, t, r - l, b - t)
                label = hit.get("label") or ""
                if label:
                    p.setFont(QFont("monospace", 8))
                    metrics = p.fontMetrics()
                    w = metrics.horizontalAdvance(label) + 10
                    h = metrics.height() + 2
                    ly = t - h - 2
                    if ly < 0:
                        ly = b + 2
                    p.setPen(Qt.NoPen)
                    p.setBrush(QColor(*_ORANGE))
                    p.drawRect(l, ly, w, h)
                    p.setPen(QColor(0, 32, 26))
                    p.drawText(l + 5, ly + h - 3, label)
        p.end()
