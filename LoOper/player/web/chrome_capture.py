from __future__ import annotations

"""OS-level browser-chrome capture for the web recorder.

Page JavaScript can never observe or trigger browser-chrome UI (back/forward/
reload buttons, the omnibox, the tab strip) - those events never reach the
document.  To record the full set of user actions the recorder installs two
OS-level hooks during a session:

- ``ChromeKeyHook``: captures page-INVISIBLE keyboard chords (Ctrl+T/W/L/N/H,
  Ctrl+Tab, Ctrl+1-9) via the ``keyboard`` package (the same one already used
  for the global ESC stop hook), plus BARE special keys (Enter, Tab,
  Backspace, arrows, F-keys, ...) typed while the browser chrome has focus
  (the omnibox) - those never reach a page document either.  They become
  ordinary ``key`` events tagged ``context={"chrome": True}`` and replay
  through the trusted native ActionChains path.  Page-visible chords
  (Alt+Left/Right back/forward, F5, Ctrl+R, zoom keys) and bare keys that DO
  reach the page are already recorded by the in-page recorder and are
  deduped against the OS twin at save time (recorder._dedupe_os_chrome_keys),
  so there is no double-recording.

- ``ChromeMouseHook``: captures clicks inside the browser toolbar band via a
  pynput mouse listener and classifies them into semantic actions
  (back/forward/reload) with ``classify_chrome_region`` - conservative
  Chrome-default regions, never raw coordinates, so replay survives DPI and
  window-size drift.  Unclassified toolbar clicks are logged as hints (use the
  keyboard shortcut); page clicks are left to the DOM recorder.

Both hooks degrade gracefully: when the OS packages are unavailable the
recorder continues without chrome capture (try/except at the call site).

Event timestamps use ``time.time() * 1000`` (epoch ms) - the same timebase as
the in-page recorder's ``Date.now()``, so OS events merge safely with page
events in the shared session sort.
"""

import logging
import os
import threading
import time
from typing import Callable, Optional

from .events import Event

logger = logging.getLogger(__name__)

# ``keyboard``-package key names -> browser ``e.key`` names.
KEY_NAME_MAP = {
    "left": "ArrowLeft",
    "right": "ArrowRight",
    "up": "ArrowUp",
    "down": "ArrowDown",
    "tab": "Tab",
    "enter": "Enter",
    "backspace": "Backspace",
    "delete": "Delete",
    "escape": "Escape",
    "space": " ",
    "home": "Home",
    "end": "End",
    "insert": "Insert",
    "page up": "PageUp",
    "page down": "PageDown",
    "f1": "F1",
    "f2": "F2",
    "f3": "F3",
    "f4": "F4",
    "f5": "F5",
    "f6": "F6",
    "f7": "F7",
    "f8": "F8",
    "f9": "F9",
    "f10": "F10",
    "f11": "F11",
    "f12": "F12",
    "+": "+",
    "-": "-",
    "=": "=",
}

# Page-INVISIBLE browser-chrome chords only (the page never receives them, so
# the in-page recorder cannot see them; the OS hook is the only way).  Zoom
# keys (Ctrl+0/+/-, Ctrl+R, F5, Alt+Left/Right) ARE delivered to the page and
# are therefore recorded by the in-page listener - no dedup needed here.
CAPTURE_CHORDS: set[frozenset[str]] = {
    frozenset({"ctrl", "t"}),
    frozenset({"ctrl", "w"}),
    frozenset({"ctrl", "shift", "t"}),
    frozenset({"ctrl", "n"}),
    frozenset({"ctrl", "l"}),
    frozenset({"ctrl", "h"}),
    frozenset({"ctrl", "tab"}),
    frozenset({"ctrl", "shift", "tab"}),
    frozenset({"ctrl", "1"}),
    frozenset({"ctrl", "2"}),
    frozenset({"ctrl", "3"}),
    frozenset({"ctrl", "4"}),
    frozenset({"ctrl", "5"}),
    frozenset({"ctrl", "6"}),
    frozenset({"ctrl", "7"}),
    frozenset({"ctrl", "8"}),
    frozenset({"ctrl", "9"}),
}

# Bare non-printable keys captured chrome-side as well (no held modifiers).
# Pressed while the browser chrome has focus (a fresh new-tab page focuses the
# omnibox by default) they never reach a page document, so the in-page
# recorder cannot see them - the OS hook is the only way.  Keys that DO reach
# the page are recorded by the in-page recorder too; recorder.py drops the OS
# twin at save time (same key+state+modifiers within a short window).  Space
# and Escape are excluded on purpose: space on editable targets lands in the
# typing buffer (no page keydown to dedupe against), and Escape is the global
# stop chord (its keyup is recorder control noise).
BARE_CHROME_KEYS: frozenset[str] = frozenset({
    "enter", "tab", "backspace", "delete",
    "up", "down", "left", "right",
    "home", "end", "insert", "page up", "page down",
    "f1", "f2", "f3", "f4", "f5", "f6",
    "f7", "f8", "f9", "f10", "f11", "f12",
})

# Keys used purely as RECORDING MARKERS (held as part of a gesture, never
# replayed) and therefore never recorded as actions.  'insert' is the
# repeating-element marker in recorder.js - keep this in sync with its
# ENTITY_HOLD_CODE.  Shared with os_capture via import.
_MARKER_KEY_NAMES: frozenset[str] = frozenset({"insert"})


# Upper bound (device px, window-relative) of the browser-chrome band where
# a click is captured as a coordinate action: everything below the toolbar
# cluster (back/forward/reload) and above the page content start - the
# bookmark bar, extensions, or the top strip that belongs to the window
# frame.  Clicks in this band that ALSO reached the page are deduped against
# the page's own click at save time (recorder._dedupe_os_chrome_clicks), so
# a generous band is safe - the page recorder wins when it saw the click.
CHROME_BAND_MAX_Y = 170


def classify_chrome_region(x: float, y: float, scale: float = 1.0) -> Optional[str]:
    """Classify a browser-chrome click into a semantic chrome action.

    Conservative Chrome-default zones in device pixels (theme/DPI drift is
    mitigated by the *scale* factor): back/forward/reload sit in the left
    cluster of the toolbar band.  Clicks in the band below the toolbar (the
    bookmark bar / window frame top strip) classify as ``chrome_click`` - a
    coordinate action the replay engine clicks natively at the recorded
    position.  Returns None only for clicks that are clearly inside the page
    (beyond the chrome band) - the DOM recorder owns those.
    """
    if y < 80 * scale:
        if x < 55 * scale:
            return "back"
        if x < 105 * scale:
            return "forward"
        if x < 155 * scale:
            return "reload"
        return "chrome_click"
    if y < CHROME_BAND_MAX_Y * scale:
        return "chrome_click"
    return None


def _foreground_pid() -> Optional[int]:
    """PID of the OS foreground window (Win32 only; None when unavailable)."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value
    except Exception:
        return None


class ChromeKeyHook:
    """OS-level capture of page-invisible browser-chrome keyboard chords.

    Registers a global callback via the ``keyboard`` package (cleaned up by
    the recorder's existing ``unhook_all()`` - no separate handle).  Emits
    ``key`` events tagged ``context={"chrome": True}`` that replay through the
    trusted native path, since untrusted JS events can never drive chrome.
    """

    MOD_WEB = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "windows": "Meta"}

    def __init__(
        self,
        *,
        foreground_pid: Optional[int] = None,
    ) -> None:
        try:
            import keyboard
        except Exception as exc:
            raise RuntimeError(f"keyboard hook unavailable: {exc}") from exc
        self._keyboard = keyboard
        self._foreground_pid = foreground_pid
        self._held: set[str] = set()
        # Bare special keys currently HELD: the `keyboard` hook fires a 'down'
        # for every auto-repeat, so this suppresses the flood of repeated
        # actions a held key (Enter/arrows/...) would otherwise produce.
        self._bare_held: set[str] = set()
        self._events: list[Event] = []
        self._lock = threading.Lock()
        # Live state of the marker key (Insert): the low-level hook sees it
        # system-wide even when the browser page does not receive it, so the
        # recording loop bridges this into the page (recorder.record_web_session).
        self.marker_held = False
        keyboard.hook(self._on_event, suppress=False)

    def drain(self) -> list[Event]:
        """Return and clear the captured chrome-key events."""
        with self._lock:
            out, self._events = self._events, []
        return out

    def _emit(self, key: str, modifiers: list[str]) -> None:
        # No url/title: stamping them here would call the WebDriver from the
        # keyboard hook thread, racing the recording poll loop on the same
        # session and killing the connection (crash reports).
        evt = Event(
            type="key",
            ts=time.time() * 1000.0,
            key=key,
            # The hook only ever emits presses (releases are ignored), so the
            # state is stamped "down" - page key events carry the same state,
            # which is what lets recorder._dedupe_os_chrome_keys match an OS
            # twin against its in-page copy and drop it.  A missing state used
            # to make every OS key look different from its page twin, so bare
            # keys (Enter/Tab/Backspace) recorded TWICE and replayed twice.
            state="down",
            modifiers=modifiers,
            context={"chrome": True},
        )
        with self._lock:
            self._events.append(evt)

    def _on_event(self, event) -> None:
        name = getattr(event, "name", None)
        if not name:
            return
        is_down = getattr(event, "event_type", "down") == "down"
        if name in self.MOD_WEB:
            if is_down:
                self._held.add(name)
            else:
                self._held.discard(name)
            return
        # A pure recording-marker key (e.g. Insert, held as part of a gesture)
        # is never an action - never record it, but DO track its hold so the
        # page can be told (the page may never see the key itself).
        if name in _MARKER_KEY_NAMES:
            self.marker_held = is_down
            return
        if not is_down:
            # Clear the bare-key hold so a later press records again (the
            # `keyboard` hook fires a 'down' for EVERY auto-repeat).
            self._bare_held.discard(name)
            return
        bare_capture = not self._held and name in BARE_CHROME_KEYS
        if bare_capture:
            if name in self._bare_held:
                return  # auto-repeat of a held bare key - not a new action
            self._bare_held.add(name)
        chord = frozenset(self._held | {name})
        # Page-invisible chords AND bare special keys (Enter/Tab/...) typed
        # while the browser chrome has focus - both invisible to the in-page
        # recorder.  Bare keys that reach the page are deduped against the
        # page's own event at save time (recorder._dedupe_os_chrome_keys).
        is_capture = chord in CAPTURE_CHORDS or bare_capture
        if not is_capture:
            return
        # Fail closed: without a matched session pid we cannot tell whether the
        # recording browser is foreground - capture nothing rather than record
        # chords typed in other applications.
        if self._foreground_pid is None or _foreground_pid() != self._foreground_pid:
            return
        key = KEY_NAME_MAP.get(name, name)
        if len(name) == 1 and name.isalpha() and "shift" in self._held:
            key = key.upper()  # browser e.key uppercases letters under Shift
        modifiers = [self.MOD_WEB[m] for m in self._held if m in self.MOD_WEB]
        self._emit(key, modifiers)


class ChromeMouseHook:
    """OS-level capture of browser-chrome toolbar clicks (Win32 only).

    A pynput mouse listener classifies clicks that land in the browser's
    toolbar band into semantic actions (back/forward/reload).  Page clicks are
    left to the DOM recorder; unclassified toolbar clicks get a hint.
    """

    def __init__(self, *, window_rect_cb: Optional[Callable[[], Optional[tuple[int, int, int, int]]]] = None) -> None:
        try:
            from pynput import mouse
        except Exception as exc:
            raise RuntimeError(f"pynput mouse hook unavailable: {exc}") from exc
        self._window_rect_cb = window_rect_cb
        self._events: list[Event] = []
        self._lock = threading.Lock()
        self._listener = mouse.Listener(on_click=self._on_click)

    def start(self) -> None:
        self._listener.start()

    def stop(self) -> None:
        try:
            self._listener.stop()
        except Exception:
            pass

    def drain(self) -> list[Event]:
        """Return and clear the captured chrome-click events."""
        with self._lock:
            out, self._events = self._events, []
        return out

    def _on_click(self, x: int, y: int, button, pressed: bool) -> None:
        if not pressed:
            return
        rect = self._window_rect_cb() if self._window_rect_cb else None
        if not rect:
            return
        left, top, right, bottom = rect
        if not (left <= x < right and top <= y < bottom):
            return  # outside the recording browser window
        rel_x, rel_y = x - left, y - top
        action = classify_chrome_region(rel_x, rel_y, 1.0)
        if action is None:
            logger.debug(
                "Chrome-band click at (%d, %d) inside the page - DOM recorder owns it",
                rel_x, rel_y,
            )
            return
        # chrome_click carries BOTH window-relative (rel) and absolute screen
        # coordinates: replay prefers the absolute position (the desktop
        # recorder's coordinate click), with the relative one as a fallback
        # when the window moved/resized between sessions.  Semantic actions
        # (back/forward/reload) need no coordinates - they replay by name.
        context: dict = {"chrome": True}
        coords: Optional[dict] = None
        if action == "chrome_click":
            context = {"chrome": True, "screen_x": x, "screen_y": y}
            coords = {"x": rel_x, "y": rel_y}
        evt = Event(
            type="chrome",
            ts=time.time() * 1000.0,
            action=action,
            coordinates=coords,
            context=context,
        )
        with self._lock:
            self._events.append(evt)
