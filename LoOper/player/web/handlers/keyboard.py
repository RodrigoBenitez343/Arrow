from __future__ import annotations

"""Keyboard interaction handlers: key presses and typing.

Keys whose browser DEFAULT action (editing, caret movement, selection,
submission, modifier hold) only fires for trusted input replay natively -
untrusted synthetic events never perform default actions.  Plain single-char
hotkeys stay on the JS path: page handlers respond to those fine.  Typing
prefers the element the user actually focused (deepest active element through
open shadow roots), then the recorded selector.
"""
import time

from .. import actions as A  # module access: tests patch web_actions.* helpers
from ..actions import (
    ElementNotFoundError,
    JS_KEY,
    JS_TYPE,
    MOD_KEYS,
    MODIFIER_DEFAULT_LETTERS,
    NATIVE_KEYS,
    _dispatch_dom_type,
    _is_replay_chrome_chord,
    _semantic_chrome_action,
)
from ..events import Event
from .base import BaseHandler
from .chrome import ChromeHandler

# Shared instance for the semantic chrome routing below (a Ctrl+T chord
# becomes a guarded new_tab action instead of a raw key that may never reach
# browser chrome).
_CHROME = ChromeHandler()


class KeyHandler(BaseHandler):
    """Replay one recorded key press (or macro chord)."""

    type_name = "key"

    def _execute(self, driver, event, config) -> str:
        key = event.key or "Unknown"
        # Release events are recorded for macro completeness; every tap below
        # presses AND releases its chord atomically, so "up" adds nothing.
        if getattr(event, "state", None) == "up":
            return "skip"
        # Chrome-affecting keys (or keys captured by the OS-level chrome hook)
        # must use the trusted native path: synthetic JS events can never
        # trigger browser chrome, so the toggle is bypassed for these.
        # Editing/navigation keys and modifier chords are bypassed too -
        # untrusted key events never perform default actions (deletion, caret
        # movement, selection, submit).
        mods = frozenset(getattr(event, "modifiers", None) or [])
        force_native = (
            bool((getattr(event, "context", None) or {}).get("chrome"))
            or _is_replay_chrome_chord(event)
            or key in NATIVE_KEYS
            or bool(mods and key and key.lower() in MODIFIER_DEFAULT_LETTERS)
        )
        if config.native_actions or force_native:
            if key in MOD_KEYS:
                # A lone modifier key (Shift/Ctrl/Alt/Meta): clean press+release
                # tap so the modifier actually registers.  It has no default of
                # its own; real modifier state lives on the following
                # chord/click, which replays its own modifiers natively.
                chain = A.ActionChains(driver)
                chain = chain.key_down(MOD_KEYS[key]).key_up(MOD_KEYS[key])
                chain.perform()
                return "native"
            semantic = _semantic_chrome_action(event)
            if semantic is not None:
                # Chrome chords replay as semantic actions (driver.back()/
                # refresh()/guarded tab switches) - raw chromedriver keys may
                # never reach browser chrome, and Ctrl+W must never kill the
                # last tab.
                _CHROME._execute(
                    driver,
                    Event(
                        type="chrome",
                        ts=time.time() * 1000.0,
                        action=semantic[0],
                        value=semantic[1],
                        context=getattr(event, "context", None) or {},
                    ),
                    config,
                )
                return "chrome:" + (semantic[0] or "action")
            mapped = {
                "Enter": "enter",
                "Tab": "tab",
                "Backspace": "backspace",
                "Delete": "delete",
                "Escape": "escape",
                "ArrowUp": "up",
                "ArrowDown": "down",
                "ArrowLeft": "left",
                "ArrowRight": "right",
                "Home": "home",
                "End": "end",
                "PageUp": "pageup",
                "PageDown": "pagedown",
                "Insert": "insert",
                " ": "space",
                "Space": "space",
                "F1": "f1",
                "F2": "f2",
                "F3": "f3",
                "F4": "f4",
                "F5": "f5",
                "F6": "f6",
                "F7": "f7",
                "F8": "f8",
                "F9": "f9",
                "F10": "f10",
                "F11": "f11",
                "F12": "f12",
            }.get(key, key)
            # Recorded modifiers replay as REAL chorded keys (CDP input is
            # trusted, so Alt+Left / Alt+Right drive the browser back/forward
            # exactly like the user's own keystroke).
            from selenium.webdriver.common.keys import Keys as _Keys

            keys_map = {
                "enter": _Keys.ENTER, "tab": _Keys.TAB,
                "backspace": _Keys.BACKSPACE, "delete": _Keys.DELETE,
                "escape": _Keys.ESCAPE, "up": _Keys.ARROW_UP,
                "down": _Keys.ARROW_DOWN, "left": _Keys.ARROW_LEFT,
                "right": _Keys.ARROW_RIGHT, "home": _Keys.HOME,
                "end": _Keys.END, "pageup": _Keys.PAGE_UP,
                "pagedown": _Keys.PAGE_DOWN, "insert": _Keys.INSERT,
                "space": _Keys.SPACE, "f1": _Keys.F1, "f2": _Keys.F2,
                "f3": _Keys.F3, "f4": _Keys.F4, "f5": _Keys.F5,
                "f6": _Keys.F6, "f7": _Keys.F7, "f8": _Keys.F8,
                "f9": _Keys.F9, "f10": _Keys.F10, "f11": _Keys.F11,
                "f12": _Keys.F12,
            }
            chain = A.ActionChains(driver)
            # Keys always replay at the element focused at replay time - like a
            # desktop sequence, never moved to a recorded target.  The session's
            # own clicks/types (or the previous web sequence node, since all web
            # sequence nodes share one browser) do the focusing; resolving a
            # stale recorded locator could only move the mouse to the wrong
            # element when sequences compose.
            for mod in event.modifiers or []:
                chain = chain.key_down(MOD_KEYS.get(mod, mod))
            chain.send_keys(keys_map.get(mapped, mapped))
            for mod in reversed(event.modifiers or []):
                chain = chain.key_up(MOD_KEYS.get(mod, mod))
            chain.perform()
            return "native"
        driver.execute_script(JS_KEY, key, event.modifiers)
        return "js"


class TypeHandler(BaseHandler):
    """Replay one recorded typed string into the element that received it.

    Focus-driven like a desktop sequence: when the recorded element cannot be
    resolved (rotated ids, hydration, shadow DOM), the text still writes into
    whichever editable is focused at replay time - the typing never depends on
    the preceding hover/click actions resolving their own elements.
    """

    type_name = "type"

    def _execute(self, driver, event, config) -> str:
        if not event.value:
            return "skip"
        # Native resolve first (full timeout); when the locator cannot be
        # resolved by WebDriver (elements inside shadow roots are invisible to
        # it) fall back to resolving + inserting entirely inside the browser.
        try:
            element = A.resolve_element(driver, event, config.element_timeout)
        except ElementNotFoundError:
            element = None
        if element is not None:
            A._scroll_into_view(driver, element)
            if config.native_actions:
                try:
                    element.send_keys(event.value)
                    return "native"
                except Exception:
                    # send_keys cannot reach the field: it is hidden/covered
                    # (Google keeps the password form in the DOM until the
                    # flow reveals it) or the page re-rendered mid-action
                    # (stale).  The JS value setter works on hidden editables
                    # - re-resolve once for the stale case, then insert
                    # in-browser so the typing is never lost.
                    try:
                        fresh = A.resolve_element(
                            driver, event, min(config.element_timeout, 5.0)
                        )
                    except ElementNotFoundError:
                        fresh = None
                    if fresh is None:
                        fresh = element
                    try:
                        if driver.execute_script(JS_TYPE, fresh, event.value):
                            return "js-hidden"
                    except Exception:
                        pass
            else:
                driver.execute_script(JS_TYPE, element, event.value)
                return "js"
        # The recorded element is gone (rotated id/class between sessions, or
        # the flow only ever relied on focus).  Typing is focus-driven like a
        # desktop sequence: when an editable is focused at replay time, write
        # there - the hover/click actions before this one did the focusing, so
        # THEIR failure must not cancel the writing (fill forms whose recorded
        # fields drifted without having to re-hover them).
        if config.native_actions and A._focused_editable(driver):
            # Bare trusted keys land in whatever the browser has focused -
            # shadow-root fields included, no element round-trip needed.
            A.ActionChains(driver).send_keys(event.value).perform()
            return "native-focus"
        if not _dispatch_dom_type(driver, event, config):
            chain = event.locator.chain() if event.locator else "no locator"
            raise ElementNotFoundError(f"cannot locate type target: {chain}")
        return "js-shadow"
