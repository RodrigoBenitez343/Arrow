"""
ActionExecutor — Low-level desktop action executor with recording.

Executes raw actions (click, type, press key, wait) on the desktop
via pyautogui/pynput, and records each action into a buffer that
can later be used to build a chain JSON file.

Used by VisualAgent when improvising guided actions.
"""

import logging
import time
from typing import Any, Callable, Dict, List, Optional

try:
    import pyautogui
except Exception:
    pyautogui = None

try:
    from pynput import keyboard as pynput_keyboard
except Exception:
    pynput_keyboard = None

logger = logging.getLogger(__name__)

_KEY_MAPPING = {
    "enter": "Key.enter",
    "tab": "Key.tab",
    "backspace": "Key.backspace",
    "escape": "Key.esc",
    "esc": "Key.esc",
    "space": "Key.space",
    "up": "Key.up",
    "down": "Key.down",
    "left": "Key.left",
    "right": "Key.right",
    "delete": "Key.delete",
    "shift": "Key.shift",
    "ctrl": "Key.ctrl",
    "alt": "Key.alt",
}


class ActionExecutor:
    """Executes and records desktop automation actions."""

    def __init__(
        self,
        on_minimize: Optional[Callable] = None,
        on_restore: Optional[Callable] = None,
    ):
        self._on_minimize = on_minimize
        self._on_restore = on_restore
        self._recorded_actions: List[Dict[str, Any]] = []

    # ── public api ───────────────────────────────────────────

    def click(self, x: int, y: int, button: str = "left") -> bool:
        """Move mouse and click at screen coordinates."""
        if pyautogui is None:
            logger.error("pyautogui not available")
            return False
        self._record_action(
            {
                "type": "click",
                "button": button,
                "coordinates": {"x": x, "y": y},
                "timestamp": time.time(),
            }
        )
        try:
            self._minimize()
            pyautogui.moveTo(x, y, duration=0.1)
            pyautogui.click(button=button)
            logger.info("Clicked (%s) at (%d, %d)", button, x, y)
            return True
        except Exception as e:
            logger.error("Click failed: %s", e)
            return False
        finally:
            self._restore()

    def type_text(self, text: str) -> bool:
        """Type text at the current cursor position."""
        if pyautogui is None:
            logger.error("pyautogui not available")
            return False
        self._record_action(
            {
                "type": "type_string",
                "text": text,
                "timestamp": time.time(),
            }
        )
        try:
            self._minimize()
            pyautogui.write(text, interval=0.02)
            logger.info("Typed %d chars", len(text))
            return True
        except Exception as e:
            logger.error("Type text failed: %s", e)
            return False
        finally:
            self._restore()

    def press_key(self, key: str) -> bool:
        """Press a special key (enter, tab, backspace, etc.)."""
        self._record_action(
            {
                "type": "keystroke",
                "key": _KEY_MAPPING.get(key.lower(), key),
                "timestamp": time.time(),
            }
        )
        try:
            self._minimize()
            if pynput_keyboard:
                mapped = self._resolve_key(key)
                kb = pynput_keyboard.Controller()
                kb.press(mapped)
                time.sleep(0.02)
                kb.release(mapped)
            elif pyautogui:
                pyautogui.press(key)
            logger.info("Pressed key: %s", key)
            return True
        except Exception as e:
            logger.error("Key press failed: %s", e)
            return False
        finally:
            self._restore()

    def wait(self, seconds: float) -> bool:
        """Wait for the specified duration."""
        self._record_action(
            {
                "type": "wait",
                "duration": seconds,
                "timestamp": time.time(),
            }
        )
        try:
            time.sleep(seconds)
            return True
        except Exception:
            return False

    # ── recording ───────────────────────────────────────────

    def get_recorded_actions(self) -> List[Dict[str, Any]]:
        """Return the list of recorded actions for chain building."""
        return list(self._recorded_actions)

    def clear_recorded_actions(self) -> None:
        """Clear the recorded action buffer."""
        self._recorded_actions.clear()

    def _record_action(self, action: Dict[str, Any]) -> None:
        """Append an action to the recording buffer."""
        self._recorded_actions.append(action)

    # ── helpers ─────────────────────────────────────────────

    def _minimize(self) -> None:
        if self._on_minimize:
            try:
                self._on_minimize()
            except Exception:
                pass

    def _restore(self) -> None:
        if self._on_restore:
            try:
                self._on_restore()
            except Exception:
                pass

    def _resolve_key(self, key: str) -> Any:
        """Map a key name to a pynput Key object."""
        if pynput_keyboard is None:
            return key
        mapping = {
            "enter": pynput_keyboard.Key.enter,
            "tab": pynput_keyboard.Key.tab,
            "backspace": pynput_keyboard.Key.backspace,
            "space": pynput_keyboard.Key.space,
            "escape": pynput_keyboard.Key.esc,
            "esc": pynput_keyboard.Key.esc,
            "up": pynput_keyboard.Key.up,
            "down": pynput_keyboard.Key.down,
            "left": pynput_keyboard.Key.left,
            "right": pynput_keyboard.Key.right,
            "delete": pynput_keyboard.Key.delete,
            "shift": pynput_keyboard.Key.shift,
            "ctrl": pynput_keyboard.Key.ctrl,
            "alt": pynput_keyboard.Key.alt,
        }
        return mapping.get(key.lower(), key)
