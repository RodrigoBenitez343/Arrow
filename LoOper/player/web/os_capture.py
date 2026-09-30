from __future__ import annotations

"""OS-level web recording prototype: the desktop recorder's capture, with the
web engine's DOM locators.

The desktop ElementRecorder (recorder/) captures clicks, typing, keys and
scrolls at the OS level (pynput) and locates replay targets with OpenCV
screenshots.  This module is the web twin of that idea: the SAME desktop
handlers (``KeyboardHandler`` / ``MouseHandler`` / ``ScrollManager`` - pure
event logic, no listeners) are fed by pynput listeners scoped to the
recording browser, and the poll loop - the only thread that talks to the
WebDriver - attaches DOM locators to every action by asking Chrome what is
under the cursor / focused at that moment.  The session is saved in the
regular web format (mode=web, schema 1) so the existing replay engine and
web_sequence nodes consume it unchanged.

Replay contract (never auto-open): replay is interaction-driven and runs on
the browser's current page.  Browser-chrome interactions - typing in the
address bar, clicking tabs/bookmarks - are browser SETUP, not automation:
they are excluded from the session (semantic back/forward/reload clicks are
still recorded, exactly like the in-page recorder path).

Known ceilings (prototype): locators come from elementFromPoint, which does
not pierce closed shadow roots (one open-shadow level is drilled manually)
and does not descend into iframes - actions inside embedded frames land on
the frame element.  Hover/focus/select/submit have no OS event and are not
captured here (the in-page recorder still covers them).  Screen-to-page
coordinates assume a 100% zoom page; DPI scaling is corrected from the CDP
window bounds.
"""

import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from .chrome_capture import _foreground_pid, _MARKER_KEY_NAMES
from .events import Event, Locator, save_session
from .recorder import _finalize_actions

logger = logging.getLogger(__name__)

_INJECT_DIR = Path(__file__).parent / "inject"
_POLL_INTERVAL_S = 0.1
_SETTLE_AFTER_STOP_S = 0.4
_IDLE_TYPE_FLUSH_S = 0.6  # flush the typing buffer after this much idle

# Desktop canonical key names (recorder.key_defs) -> browser e.key names.
_KEY_TO_WEB = {
    "enter": "Enter", "tab": "Tab", "esc": "Escape",
    "backspace": "Backspace", "delete": "Delete",
    "up": "ArrowUp", "down": "ArrowDown", "left": "ArrowLeft",
    "right": "ArrowRight", "home": "Home", "end": "End", "insert": "Insert",
    "page_up": "PageUp", "page_down": "PageDown", "space": " ",
    "f1": "F1", "f2": "F2", "f3": "F3", "f4": "F4", "f5": "F5", "f6": "F6",
    "f7": "F7", "f8": "F8", "f9": "F9", "f10": "F10", "f11": "F11",
    "f12": "F12", "caps_lock": "CapsLock", "num_lock": "NumLock",
    "scroll_lock": "ScrollLock", "print_screen": "PrintScreen",
    "pause": "Pause", "menu": "ContextMenu",
}

# Desktop canonical modifiers -> web event modifier names (same as the
# in-page recorder: e.ctrlKey -> "Ctrl", metaKey -> "Meta", ...).
_MOD_TO_WEB = {
    "ctrl": "Ctrl", "shift": "Shift", "alt": "Alt",
    "cmd": "Meta", "alt_gr": "AltGraph",
}


def _web_key(name: str) -> Optional[str]:
    """Desktop canonical key name -> browser e.key value (None = unknown)."""
    if not name:
        return None
    if name in _KEY_TO_WEB:
        return _KEY_TO_WEB[name]
    if len(name) == 1:
        return name
    if name.startswith("vk_"):
        return None  # unresolved virtual key - cannot replay meaningfully
    return None


def _web_mods(names: list[str]) -> list[str]:
    """Desktop canonical modifier list -> web modifier names (order stable)."""
    return [_MOD_TO_WEB[n] for n in names if n in _MOD_TO_WEB]


# Keys used purely as RECORDING MARKERS are shared with chrome_capture (the
# single source of truth, kept in sync with recorder.js ENTITY_HOLD_CODE).


def _is_marker_key(key) -> bool:
    """True for a pure recording-marker key (held, never recorded/replayed)."""
    return getattr(key, "name", None) in _MARKER_KEY_NAMES


def _now_ms() -> float:
    return time.time() * 1000.0


def _payload_ts(action: dict) -> float:
    """Epoch-ms timestamp of a desktop action payload.

    Desktop handlers stamp real capture times in seconds; fall back to now
    when a payload (e.g. an idle flush) carries none.
    """
    raw = action.get("timestamp")
    try:
        return float(raw) * 1000.0 if raw else _now_ms()
    except (TypeError, ValueError):
        return _now_ms()


# Active-element snapshot: deepest editable through open shadow roots.
# Returns {"editable": bool, "locator": {...}} or None when nothing focused.
_FOCUSED_JS = """
(function () {
  var L = window.__wvpLoc;
  if (!L) return null;
  var el = document.activeElement;
  var depth = 0;
  while (el && el.shadowRoot && el.shadowRoot.activeElement && depth < 10) {
    el = el.shadowRoot.activeElement;
    depth += 1;
  }
  if (!el || el.nodeType !== 1) return null;
  var tag = (el.tagName || '').toLowerCase();
  var editable = tag === 'input' || tag === 'textarea' || el.isContentEditable;
  try {
    return {editable: editable, locator: L.locatorFor(el)};
  } catch (e) {
    return {editable: editable, locator: {tag: tag}};
  }
})()
"""

# Hit test at viewport (client) coordinates, drilling into open shadow roots
# (elementFromPoint returns the shadow HOST otherwise).  ShadowRoot.
# elementFromPoint takes the SAME viewport x/y as Document.elementFromPoint -
# it is NOT host-relative, so translating by the host's rect made every
# off-origin shadow root miss and the HOST (the outer container / the page
# underneath an open modal) was recorded in place of the innermost element.
_HIT_TEST_JS = """
(function (cx, cy) {
  var L = window.__wvpLoc;
  if (!L) return null;
  function at(doc, x, y) {
    var el = doc.elementFromPoint(x, y);
    if (!el) return null;
    if (el.shadowRoot) {
      try {
        var inner = at(el.shadowRoot, x, y);
        if (inner && inner !== el) return inner;
      } catch (e) {}
    }
    return el;
  }
  var el = at(document, cx, cy);
  if (!el || el === document.documentElement || el === document.body) {
    return null;
  }
  try {
    return L.locatorFor(el);
  } catch (e) {
    return {tag: (el.tagName || '').toLowerCase()};
  }
})(arguments[0], arguments[1])
"""


def _locators_payload() -> str:
    return (_INJECT_DIR / "locators.js").read_text(encoding="utf-8")


def _ensure_locators(driver, payload: str) -> None:
    """Inject locators.js into the current top document (CDP covers new docs)."""
    try:
        ready = driver.execute_script("return !!window.__wvpLoc")
        if not ready:
            driver.execute_script(payload + "; true")
    except Exception:
        pass


class OsWebCapture:
    """Desktop-recorder capture scoped to one browser, translated to web Events.

    pynput listeners run on their own threads and only mutate the desktop
    handlers + a small queue; the recorder's poll loop (the ONLY caller of
    ``pump``) owns the WebDriver and translates queued actions into web
    Events with locators snapshotted from the browser at the action's
    position.  Never call the driver from the listener threads.
    """

    def __init__(
        self,
        driver,
        *,
        foreground_pid: Optional[int] = None,
        window_rect: Optional[Callable[[], Optional[tuple[int, int, int, int]]]] = None,
    ) -> None:
        self.driver = driver
        self._foreground_pid = foreground_pid
        self._window_rect_cb = window_rect
        # Screen-physical -> page-client geometry, resolved on the first pump
        # (DPI scale from CDP bounds vs OS rect; toolbar offset from JS).
        self.scale: float = 1.0
        self.content_offset_px: float = 0.0
        self.events: list[Event] = []
        self._queue: list[tuple[str, Any]] = []
        # One lock guards BOTH the queue and the desktop handler state: the
        # listeners mutate KeyboardHandler/MouseHandler/ScrollManager on their
        # own threads while the poll loop reads and resets them.
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._geom_done = False
        self._recorded_count = 0
        # Last time a keystroke was buffered (KeyboardHandler only timestamps
        # key BOUNDARIES - pure typing never touches _last_event_time).
        self._last_input_time: float = 0.0
        # Focused editable element snapshotted WHILE the user is typing, so a
        # type->click flush binds to the field, not the click target (browsers
        # move focus on mousedown, before the release that queues the click).
        self._editable_snapshot: Optional[dict] = None
        # Lazy: the desktop recorder package needs pynput importable; the web
        # package imports fine without it.
        from recorder.keyboard_handler import KeyboardHandler
        from recorder.mouse_handler import MouseHandler
        from recorder.scroll_manager import ScrollManager

        self._kh = KeyboardHandler()
        self._mh = MouseHandler()
        self._sm = ScrollManager()
        self._mouse_listener = None
        self._keyboard_listener = None

    # ------------------------------------------------------------------ api

    @property
    def stop_event(self) -> threading.Event:
        return self._stop

    def start(self) -> None:
        """Start the scoped pynput listeners (ESC anywhere stops)."""
        from pynput import keyboard, mouse

        def _inside_window(x: int, y: int) -> bool:
            rect = self._window_rect_cb() if self._window_rect_cb else None
            if not rect:
                return False
            return rect[0] <= x < rect[2] and rect[1] <= y < rect[3]

        def _browser_foreground() -> bool:
            if self._foreground_pid is None:
                return False
            return _foreground_pid() == self._foreground_pid

        def on_click(x, y, button, pressed):
            if not _inside_window(x, y):
                return
            try:
                with self._lock:
                    if pressed:
                        self._mh.handle_mouse_press(x, y, button, pressed)
                        return
                    action = self._mh.handle_mouse_release(x, y, button)
                    if not action:
                        return
                    action = self._retype_with_modifiers(action)
                if action is not None:
                    self._enqueue(("mouse", action))
            except Exception as exc:
                logger.warning("OS capture mouse error: %s", exc)

        def on_scroll(x, y, dx, dy):
            if not _inside_window(x, y):
                return
            try:
                with self._lock:
                    action = self._sm.process_scroll(x, y, dx, dy)
                if action:
                    self._enqueue(("scroll", action))
            except Exception as exc:
                logger.warning("OS capture scroll error: %s", exc)

        def on_press(key):
            try:
                if getattr(key, "name", None) == "esc" or key == keyboard.Key.esc:
                    self._stop.set()
                    return False
                if _is_marker_key(key):
                    return  # a marker key (e.g. Insert) is not a recorded action
                if not _browser_foreground():
                    return
                with self._lock:
                    result = self._kh.handle_keypress(key)
                    if result is None:
                        # A printable character was buffered (handle_keypress
                        # returns None for typing) - remember when, so the idle
                        # flush can fire even though no key boundary timestamped.
                        self._last_input_time = time.time()
                if result is not None:
                    self._enqueue(("key", result))
            except Exception as exc:
                logger.warning("OS capture key error: %s", exc)

        def on_release(key):
            try:
                if _is_marker_key(key):
                    return
                if not _browser_foreground():
                    return
                with self._lock:
                    result = self._kh.handle_keyrelease(key)
                if result is not None:
                    self._enqueue(("key", result))
            except Exception as exc:
                logger.warning("OS capture key release error: %s", exc)

        self._mouse_listener = mouse.Listener(
            on_click=on_click, on_scroll=on_scroll
        )
        self._keyboard_listener = keyboard.Listener(
            on_press=on_press, on_release=on_release
        )
        self._mouse_listener.start()
        self._keyboard_listener.start()

    def stop(self) -> None:
        for listener in (self._keyboard_listener, self._mouse_listener):
            if listener is not None:
                try:
                    listener.stop()
                except Exception:
                    pass

    def idle_typing(self) -> bool:
        """True when the user paused mid-typing and the buffer should flush.

        ``_last_event_time`` only moves at key boundaries (modifiers, special
        keys) - pure printable typing never sets it - so the fallback is the
        last buffered-character time captured on the listener thread.
        """
        with self._lock:
            if not self._kh.current_string:
                return False
            last = self._kh._last_event_time or self._last_input_time
        return bool(last) and time.time() - last > _IDLE_TYPE_FLUSH_S

    def pump(self) -> None:
        """Translate every queued OS action into a web Event (loop thread)."""
        if not self._geom_done:
            self._resolve_geometry()
        # While the user is typing (no pointer action pending), keep a fresh
        # snapshot of the focused editable - it is what the pending text
        # belongs to, and a later click must not rebind it.
        with self._lock:
            typing = bool(self._kh.current_string)
        if typing:
            if not self._queue or self._queue[0][0] == "key":
                snap = self._focused_editable()
                if snap and snap.get("editable"):
                    self._editable_snapshot = snap
        # Typing followed by a click/scroll must flush BEFORE the queued
        # pointer action, so the type event lands first in the session.
        with self._lock:
            flush_pending = bool(self._kh.current_string) and (
                bool(self._queue) and self._queue[0][0] != "key"
            )
        if flush_pending:
            self._flush_typing()
        items = self._drain()
        for kind, payload in items:
            try:
                if kind == "mouse":
                    self._translate_mouse(payload)
                elif kind == "scroll":
                    self._translate_scroll(payload)
                elif kind == "key":
                    self._translate_key(payload)
            except Exception as exc:
                logger.warning("OS capture translate error (%s): %s", kind, exc)

    # ------------------------------------------------------------- plumbing

    def _enqueue(self, item: tuple[str, Any]) -> None:
        with self._lock:
            self._queue.append(item)

    def _drain(self) -> list[tuple[str, Any]]:
        with self._lock:
            out, self._queue = self._queue, []
        return out

    def _resolve_geometry(self) -> None:
        self._geom_done = True
        rect = self._window_rect_cb() if self._window_rect_cb else None
        if rect:
            try:
                bounds = self.driver.execute_cdp_cmd("Browser.getWindowForTarget", {})
                b = bounds.get("bounds") or {}
                if b.get("width"):
                    self.scale = (rect[2] - rect[0]) / float(b["width"])
                    self.scale = max(0.5, min(self.scale, 3.0))
            except Exception:
                self.scale = 1.0
        try:
            dip = float(
                self.driver.execute_script(
                    "return window.outerHeight - window.innerHeight"
                ) or 0.0
            )
        except Exception:
            dip = 0.0
        self.content_offset_px = dip * self.scale

    def _client_coords(self, screen_x: int, screen_y: int):
        rect = self._window_rect_cb() if self._window_rect_cb else None
        if not rect:
            return None
        cx = (screen_x - rect[0]) / self.scale
        cy = (screen_y - rect[1]) / self.scale - self.content_offset_px / self.scale
        return cx, cy

    def _hit_test_locator(self, cx: float, cy: float) -> Optional[dict]:
        try:
            raw = self.driver.execute_script(_HIT_TEST_JS, cx, cy)
        except Exception:
            return None
        return raw if isinstance(raw, dict) and raw.get("tag") else None

    def _focused_editable(self) -> Optional[dict]:
        """{"editable": bool, "locator": {...}} of the focused element."""
        try:
            raw = self.driver.execute_script(_FOCUSED_JS)
        except Exception:
            return None
        return raw if isinstance(raw, dict) and raw.get("locator") else None

    def _current_url(self) -> Optional[str]:
        try:
            return self.driver.current_url or None
        except Exception:
            return None

    def _record(self, evt: Event) -> None:
        # Page actions carry the page they happened on (needed for the
        # Chrome-internal drop and the cold-start collapse).  OS-captured key
        # and chrome events are locator-less browser actions: leaving their
        # url unset keeps them classified as chrome noise by the shared
        # finalize pipeline instead of masquerading as real page actions.
        if evt.url is None and evt.locator is not None:
            evt.url = self._current_url()
        self._recorded_count += 1
        logger.info("WEB ACTION #%03d: %s", self._recorded_count, evt.describe())
        self.events.append(evt)

    def _typing_target(self) -> Optional[Locator]:
        """The editable the pending text belongs to.

        Prefers the snapshot taken while the user was still typing (a click
        after the text moves focus on mousedown, so a live query at flush time
        would bind - or drop - the text against the click target).
        """
        info = self._editable_snapshot
        if not info or not info.get("editable"):
            live = self._focused_editable()
            info = live if live and live.get("editable") else None
        if not info:
            return None
        return Locator.from_dict(info.get("locator"))

    def _flush_typing(self) -> None:
        """Flush the desktop typing buffer as a web ``type`` on the focused
        element - only when the focus is an editable page element.  Text typed
        while the browser chrome has focus (the address bar) is browser setup
        and is never recorded."""
        with self._lock:
            text = self._kh.flush_current_string()
        if not text:
            return
        locator = self._typing_target()
        if locator is None:
            logger.info(
                "Typing outside an editable page element is not recorded "
                "(address bar / page hotkeys are browser setup)"
            )
            return
        evt = Event(type="type", ts=_now_ms(), value=text, locator=locator)
        evt.url = self._current_url()
        self._record(evt)

    def _type_text(self, text: str, ts: Optional[float] = None) -> None:
        """A type_string already flushed by KeyboardHandler at a key boundary."""
        locator = self._typing_target()
        if locator is None:
            logger.info(
                "Typing outside an editable page element is not recorded "
                "(address bar / page hotkeys are browser setup)"
            )
            return
        evt = Event(type="type", ts=ts or _now_ms(), value=text, locator=locator)
        evt.url = self._current_url()
        self._record(evt)

    # ---------------------------------------------------------- translation

    def _retype_with_modifiers(self, action: dict) -> Optional[dict]:
        """Apply the desktop recorder's modifier click semantics (ElementRecorder
        parity): Ctrl+click / Shift+click become plain clicks with modifiers;
        Alt+click (relative-reference clicks) is unsupported for web replay."""
        mods = self._kh.modifiers or {}
        kind = action.get("type")
        if kind == "drag_drop":
            return action  # OS-level drags are absolute already
        if kind != "click":
            return action
        any_mod = lambda *names: any(mods.get(n) for n in names)
        if any_mod("alt_l", "alt_r", "alt"):
            return None  # Alt+click relative reference has no web analogue
        if any_mod("ctrl_l", "ctrl_r", "ctrl"):
            return dict(action, type="ctrl_click")
        if any_mod("shift_l", "shift_r", "shift"):
            return dict(action, type="shift_click")
        return action

    def _translate_mouse(self, action: dict) -> None:
        typ = action.get("type") or "click"
        button = (action.get("button") or "left").lower()
        # Desktop actions carry their real capture timestamp (seconds); use it
        # so the session order and gap_ms reflect when the user acted, not
        # when the poll loop happened to translate.
        ts = _payload_ts(action)
        if typ == "ctrl_click":
            typ, mods = "click", ["Ctrl"]
        elif typ == "shift_click":
            typ, mods = "click", ["Shift"]
        else:
            mods = []
        coords = action.get("coordinates") or action.get("from")
        if not coords:
            return

        # Browser-chrome band (above the page content): only semantic
        # back/forward/reload clicks belong in a session; tab-strip /
        # bookmark-bar clicks are browser setup.
        rect = self._window_rect_cb() if self._window_rect_cb else None
        rel_y = (coords["y"] - rect[1]) if rect else 0
        if rect and rel_y < self.content_offset_px - 1:
            from .chrome_capture import classify_chrome_region

            zone = classify_chrome_region(
                coords["x"] - rect[0], rel_y, self.scale
            )
            if zone in ("back", "forward", "reload"):
                evt = Event(type="chrome", ts=ts, action=zone)
                self._record(evt)
            else:
                logger.debug(
                    "Toolbar click at (%d, %d) is browser setup - not recorded",
                    coords["x"], coords["y"],
                )
            return

        client = self._client_coords(coords["x"], coords["y"])
        if client is None:
            return
        locator_dict = self._hit_test_locator(*client)
        if locator_dict is None:
            logger.debug(
                "Click at (%d, %d) hit no page element - not recorded",
                coords["x"], coords["y"],
            )
            return

        if typ == "drag_drop":
            to = action.get("to") or coords
            drop_locator = None
            drop_client = self._client_coords(to["x"], to["y"])
            if drop_client is not None:
                drop_locator = self._hit_test_locator(*drop_client)
            evt = Event(
                type="drag", ts=ts,
                locator=Locator.from_dict(locator_dict),
                coordinates={"x": int(coords["x"]), "y": int(coords["y"])},
                context={
                    "drop_x": int(to["x"]),
                    "drop_y": int(to["y"]),
                    "drop_locator": drop_locator,
                },
            )
            self._record(evt)
            return

        if button == "right":
            evt_type = "contextmenu"
        elif typ == "double_click":
            evt_type = "dblclick"
        else:
            evt_type = "click"
        evt = Event(
            type=evt_type, ts=ts,
            locator=Locator.from_dict(locator_dict),
            coordinates={"x": int(coords["x"]), "y": int(coords["y"])},
            button="2" if button == "right" else "0",
            modifiers=mods,
        )
        self._record(evt)

    def _translate_scroll(self, action: dict) -> None:
        client = self._client_coords(
            action["start"]["x"], action["start"]["y"]
        )
        locator_dict = self._hit_test_locator(*client) if client else None
        # ScrollManager keeps the OS sign (pynput dy > 0 = wheel away = up on
        # Windows) but the web contract (recorder.js deltaY / JS_SCROLL)
        # treats a positive delta as scrolling DOWN - negate at the boundary.
        evt = Event(
            type="scroll", ts=_payload_ts(action),
            locator=Locator.from_dict(locator_dict) if locator_dict else None,
            scroll={
                "total_delta": -int(action.get("total_delta") or 0),
                "steps": int(action.get("steps") or 1),
                "start": action["start"],
                "end": action.get("end") or action["start"],
            },
        )
        self._record(evt)

    def _translate_key(self, payload: Any) -> None:
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            kind = item.get("type")
            ts = _payload_ts(item)
            if kind == "type_string":
                self._type_text(item.get("text") or "", ts=ts)
            elif kind == "clipboard":
                letter = (item.get("operation") or "")[:1].lower()
                if letter:
                    evt = Event(
                        type="key", ts=ts, key=letter,
                        modifiers=["Ctrl"], state="down",
                    )
                    self._record(evt)
            elif kind in ("keystroke", "key_event"):
                web = _web_key(item.get("key") or "")
                if web is None:
                    logger.debug(
                        "Key %r has no browser e.key equivalent - not recorded",
                        item.get("key"),
                    )
                    continue
                evt = Event(
                    type="key", ts=ts, key=web,
                    modifiers=_web_mods(item.get("modifiers") or []),
                    state=item.get("state") or "down",
                )
                self._record(evt)


def record_web_session_os(
    output: Optional[str] = None,
    chain_key: Optional[str] = None,
    name: Optional[str] = None,
) -> Path:
    """Record a web session through the desktop recorder's OS capture.

    The workbench browser opens (or is reused) and the user drives it with a
    real mouse/keyboard; every in-page click/typing/key/scroll is captured at
    the OS level and re-located in the DOM by the recorder's poll loop.  ESC
    or closing the browser window stops and saves.  Browser-chrome actions
    (address bar, tabs, bookmarks) are browser setup and stay out of the
    session - replay is interaction-driven on the browser's current page.

    ``chain_key`` is normalized to the app-wide shared scope (kept for API
    compatibility): every recording lands in the SAME durable profile, so
    cookies/history recorded once survive app restarts everywhere.
    """
    from .session import (
        SHARED_WEB_SCOPE,
        _enumerate_top_windows,
        _match_session_window,
        ensure_workbench_session,
        release_workbench,
    )

    payload = _locators_payload()
    scope = chain_key or SHARED_WEB_SCOPE
    driver = ensure_workbench_session(chain_key=scope)
    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument", {"source": payload}
        )
    except Exception as exc:
        logger.warning("CDP locator pre-injection unavailable (%s)", exc)
    _ensure_locators(driver, payload)

    # Resolve the browser window rect ONCE, on this (loop) thread - the
    # pynput listener threads must never call the WebDriver, and every
    # click/scroll inside the window is gated on this rect.
    rect_box = [None]
    try:
        windows = _enumerate_top_windows()
        browser_pid = _match_session_window(driver, windows)
        for win in windows:
            if win.get("pid") == browser_pid:
                rect_box[0] = win.get("rect")
                break
    except Exception:
        browser_pid = None

    def _window_rect():
        return rect_box[0]  # cached - never resolves from a listener thread

    started_at = time.time()
    capture = OsWebCapture(
        driver,
        foreground_pid=browser_pid,
        window_rect=_window_rect,
    )
    logger.info(
        "Starting OS-level web session recording (scope=%s output=%s)",
        scope, output,
    )
    logger.info("\n=== LoOper web recorder (desktop capture) ===")
    logger.info(
        "Drive the browser with the real mouse/keyboard. ESC stops and saves.\n"
        "Only in-page actions are recorded - address bar / tabs are setup,\n"
        "and replay runs on the browser's current page (never URL navigation)."
    )
    try:
        initial_url = driver.current_url or None
    except Exception:
        initial_url = None
    last_url = initial_url
    capture.start()

    window_gone = False
    try:
        while not capture.stop_event.is_set():
            time.sleep(_POLL_INTERVAL_S)
            if capture.idle_typing():
                capture._flush_typing()
            try:
                handles = driver.window_handles
            except Exception:
                handles = []
            if not handles:
                window_gone = True
                break
            try:
                last_url = driver.current_url or None
            except Exception:
                pass
            capture.pump()
        if not window_gone:
            time.sleep(_SETTLE_AFTER_STOP_S)
            if capture.idle_typing():
                capture._flush_typing()
        # Flush the pending scroll burst (ScrollManager only releases it on
        # the NEXT scroll or an explicit finalize) and drain the queue once
        # more, in both the ESC and the window-closed stop paths.
        with capture._lock:
            burst = capture._sm.finalize_scroll_burst()
        if burst:
            capture._enqueue(("scroll", burst))
        capture.pump()
    finally:
        capture.stop()
        # Leave the browser open for inspection / the next recording.
        release_workbench(driver)

    # Same cleanup pipeline as the in-page recorder: control-signal clean,
    # OS-twin dedupe, cold-start collapse, Chrome-internal drop.
    try:
        final_url = driver.current_url or None
    except Exception:
        final_url = None
    if final_url is None:
        final_url = last_url  # window closed - last page it was verifiably on
    events = _finalize_actions(
        capture.events,
        final_url=final_url,
        initial_url=initial_url,
    )
    prev_ts: Optional[float] = None
    for evt in events:
        if prev_ts is not None:
            evt.gap_ms = max(0, int(evt.ts - prev_ts))
        prev_ts = evt.ts

    if not output:
        output = str(Path("recordings") / f"session-{time.strftime('%Y%m%d-%H%M%S')}.json")
    start_url = next((e.url for e in events if e.url), None)
    save_session(
        events,
        output,
        start_url=start_url,
        started_at=started_at,
        duration_sec=time.time() - started_at,
        name=name,
    )
    logger.info("Web session saved: %s (%d actions)", output, len(events))
    return Path(output)


if __name__ == "__main__":  # pragma: no cover - CLI entry
    import argparse
    import logging as _logging

    _logging.basicConfig(level=_logging.INFO)
    parser = argparse.ArgumentParser(
        description="Record a web session with the desktop recorder's OS capture"
    )
    parser.add_argument("--output", help="session JSON path")
    parser.add_argument("--chain-key", help="chain scope for the workbench browser")
    args = parser.parse_args()
    saved = record_web_session_os(output=args.output, chain_key=args.chain_key)
    print(f"Saved {saved}")
