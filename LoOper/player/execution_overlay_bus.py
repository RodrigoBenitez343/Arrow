"""Thread-safe feed from the desktop replay (worker thread) to the on-screen
execution overlay (GUI thread).

The desktop player moves the PHYSICAL cursor with pyautogui, so the overlay
could rebuild the trail by polling ``pyautogui.position()`` - but a human-like
move is a ~1 ms burst of waypoints, so a 30 ms poll keeps only the endpoints.
Publishing the waypoints here gives the overlay the whole path.

Pure data, no Qt import: ``player`` must stay importable without the GUI.
"""
from __future__ import annotations

import contextlib
import threading
import time
from collections import deque

_TRAIL_MAX = 400    # ~30 human-like moves of cursor path
_CLICK_MAX = 48


class _ExecutionBus:
    """Recent action label + cursor trail + click pulses, last-write-wins."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._action = ""
        self._target = None  # (x, y, t) point the replay is moving to
        self._trail: deque = deque(maxlen=_TRAIL_MAX)
        self._clicks: deque = deque(maxlen=_CLICK_MAX)
        # Set by the overlay: hides it (blocking) so a screen read never sees it.
        # The overlay lives in the GUI layer; the player only calls this slot.
        self.capture_hook = None
        # Extra USER-ONLY windows that must also leave the screen for a capture
        # (e.g. the agent notch, which now stays up while a chain runs).  Kept
        # apart from capture_hook so the single-hook contract is untouched, and
        # they run even when capture_hook is None (a notch-only run creates no
        # execution overlay).
        self.extra_capture_hooks = []
        # The APP's own windows (main window, standalone chat overlay) must leave
        # the screen for a screen ACCESS - not only for a capture: a desktop
        # click or keystroke must not land on our window either, and a template
        # match must not read it.  Refcounted, because nested chains and the
        # per-capture poke share one hide/restore pair; the GUI side applies the
        # linger so back-to-back actions do not flicker.
        self.screen_hide_hook = None
        self.screen_show_hook = None
        self._screen_depth = 0
        self._screen_lock = threading.Lock()

    def set_screen_hooks(self, hide=None, show=None) -> None:
        """Register the app-window hide/restore pair for screen access."""
        self.screen_hide_hook = hide
        self.screen_show_hook = show

    @property
    def screen_busy(self) -> bool:
        """True while the runtime owns the screen (acting, not thinking)."""
        with self._screen_lock:
            return self._screen_depth > 0

    def _begin_screen_access(self) -> None:
        with self._screen_lock:
            self._screen_depth += 1
            first = self._screen_depth == 1
        if first and self.screen_hide_hook is not None:
            try:
                self.screen_hide_hook()
            except Exception:
                pass

    def _end_screen_access(self) -> None:
        with self._screen_lock:
            self._screen_depth = max(0, self._screen_depth - 1)
            last = self._screen_depth == 0
        if last and self.screen_show_hook is not None:
            try:
                self.screen_show_hook()
            except Exception:
                pass

    @contextlib.contextmanager
    def screen_access(self):
        """Keep this app off the screen while the runtime touches the desktop.

        Wrap a desktop/visual node (sequence, handle, form fill, visual
        conditional) in this: the window is gone for the whole node, so long
        actions never click through it, and it comes back - showing progress -
        for every node that only thinks (LLM, orchestrator, code, web, ...).
        Nested use (a chain inside a chain, a capture inside a sequence) shares
        one hide/restore pair.
        """
        self._begin_screen_access()
        try:
            yield
        finally:
            self._end_screen_access()

    def set_action(self, text: str) -> None:
        """Name the action about to run (e.g. ``"3: click"``)."""
        with self._lock:
            self._action = str(text or "")

    def set_target(self, x, y) -> None:
        """Name the point the replay is moving to - the NEXT click target.

        The overlay hit-tests THIS point (once) to box the element about to be
        acted on; it deliberately does not follow the live cursor.
        """
        with self._lock:
            self._target = (int(x), int(y), time.time())

    def add_trail(self, x, y) -> None:
        with self._lock:
            self._trail.append((int(x), int(y), time.time()))

    def add_click(self, x, y) -> None:
        with self._lock:
            now = time.time()
            self._clicks.append((int(x), int(y), now))
            self._trail.append((int(x), int(y), now))

    def snapshot(self) -> dict:
        """Copy of the current state, safe to read from the GUI thread."""
        with self._lock:
            return {
                "action": self._action,
                "target": self._target,
                "trail": list(self._trail),
                "clicks": list(self._clicks),
            }

    def reset(self) -> None:
        with self._lock:
            self._action = ""
            self._target = None
            self._trail.clear()
            self._clicks.clear()

    def hide_overlay_for_capture(self) -> None:
        """Take this app off the screen for a screen read.

        Called from the replay thread right before the screen is captured for
        template matching / OCR.  The hooks marshal to the GUI thread and block
        until the window is hidden, so the capture can never contain it.  The
        app window is restored by the GUI side after a short linger (so a burst
        of reads stays hidden as one block).
        """
        self._begin_screen_access()
        try:
            hook = self.capture_hook
            if hook is not None:
                try:
                    hook()
                except Exception:
                    pass
            for extra in list(self.extra_capture_hooks):
                try:
                    extra()
                except Exception:
                    pass
        finally:
            self._end_screen_access()


bus = _ExecutionBus()
