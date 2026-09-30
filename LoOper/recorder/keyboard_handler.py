# keyboard_handler.py
"""
Event-driven keyboard macro recording.

Every key press/release is tracked as a raw ``key_event`` action carrying the
canonical key name, the press/release state, a snapshot of the modifiers held
at that moment, and the inter-event timing. This lets arbitrary shortcuts --
Ctrl+Alt+Shift+<key>, Win+R, Alt+F4, F5, media keys, ... -- be reproduced
faithfully during playback instead of relying on a hard-coded combo table.

Backward compatibility is preserved for the previously hard-coded cases:
  * printable text (no Ctrl/Alt/Cmd held) is buffered into ``type_string``
  * Ctrl+C/V/X/A/Z/Y still emit the semantic ``clipboard`` action
  * plain special keys (Enter, Tab, arrows, F-keys without modifiers) still
    emit ``keystroke`` actions, so legacy recordings stay byte-compatible
"""
import time

from pynput import keyboard

try:
    from ..player.key_defs import (
        MODIFIER_NAMES,
        key_to_name,
    )
except ImportError:
    # Top-level import (standalone scripts / importability tests): the
    # relative import fails, so fall back to the package-qualified path.
    from player.key_defs import (
        MODIFIER_NAMES,
        key_to_name,
    )

# Side-aware modifier keys stored in self.modifiers (mirrors pynput names).
_MODIFIER_SIDE_KEYS = (
    "ctrl_l", "ctrl_r", "ctrl",
    "shift_l", "shift_r", "shift",
    "alt_l", "alt_r", "alt",
    "cmd_l", "cmd_r", "cmd",
    "alt_gr",
)

_LEGACY_CLIPBOARD_OPS = {
    "c": ("📋", "COPY (Ctrl+C)"),
    "v": ("📎", "PASTE (Ctrl+V)"),
    "x": ("✂️", "CUT (Ctrl+X)"),
    "a": ("🅰", "SELECT ALL (Ctrl+A)"),
    "z": ("↩️", "UNDO (Ctrl+Z)"),
    "y": ("↪️", "REDO (Ctrl+Y)"),
}

# Repeating-element marker key: the desktop twin of the web recorder's Insert
# gesture.  HOLDING it while clicking marks the element as a repeating sibling
# row; two marked sibling clicks define the row set (see element_recorder).
# Consumed here so it is never recorded as a keystroke.  pynput normally reports
# Insert as Key.insert ("insert"), but a low-level hook can hand it over as a
# virtual key code ("vk_45") - accept both.
_MARKER_NAMES = frozenset(("insert", "vk_45"))
# A marker tap just before the click still counts - the user may release Insert
# a heartbeat before the click lands (mirrors the web recorder's grace window).
_MARKER_GRACE_S = 1.5

# Visual-match marker: HOLDING right Ctrl while clicking records the click as
# VISUAL - replay then uses the legacy template match instead of re-finding the
# UIA element (an element can resolve to a container region whose subtree
# re-find lands the click in the wrong spot).  Like Insert it is a recording
# GESTURE and is consumed here so no key event (and no live Ctrl at replay)
# surrounds the click.  Unlike Insert there is NO grace window: right Ctrl is
# also the "move (hover)" capture key, and a released key right before a click
# means hover, never a visual-match click.
_VISUAL_MARKER_SIDES = frozenset(("ctrl_r",))


class KeyboardHandler:
    """Handles keyboard events and modifier key tracking."""

    def __init__(self):
        self.modifiers = {side: False for side in _MODIFIER_SIDE_KEYS}
        self.current_string = ""
        # Keys currently held down (repeat dedupe + stuck-key flush).
        self._down_keys = set()
        # Held PLAIN special keys (Enter/Tab/arrows/Insert/...) recorded as the
        # legacy 'keystroke' action: tracked separately so their auto-repeat is
        # suppressed WITHOUT emitting a synthetic key-up (that legacy action has
        # no release counterpart).  Without this, holding such a key floods the
        # session with one action per OS auto-repeat.
        self._special_down = set()
        # Timestamp of the previous key event, for inter-event delta pacing.
        self._last_event_time = None
        # Repeating-element marker (Insert) held state + release timestamp.
        self._marker_down = False
        self._marker_up_ts = None
        # Visual-match marker (right Ctrl) held state.
        self._visual_down = False

    # ------------------------------------------------------------------
    # Modifier state
    # ------------------------------------------------------------------

    def is_ctrl_down(self):
        return bool(self.modifiers.get("ctrl_l") or self.modifiers.get("ctrl_r")
                    or self.modifiers.get("ctrl"))

    def is_shift_down(self):
        return bool(self.modifiers.get("shift_l") or self.modifiers.get("shift_r")
                    or self.modifiers.get("shift"))

    def is_alt_down(self):
        return bool(self.modifiers.get("alt_l") or self.modifiers.get("alt_r")
                    or self.modifiers.get("alt"))

    def is_cmd_down(self):
        return bool(self.modifiers.get("cmd_l") or self.modifiers.get("cmd_r")
                    or self.modifiers.get("cmd"))

    def active_modifiers(self):
        """
        Canonical list of currently held modifiers, e.g. ['ctrl', 'shift'].

        Side variants collapse into their canonical group so a chord is
        described once regardless of which Ctrl/Shift/Alt was used.
        """
        m = self.modifiers
        out = []
        for group, sides in (
            ("ctrl", ("ctrl_l", "ctrl_r", "ctrl")),
            ("shift", ("shift_l", "shift_r", "shift")),
            ("alt", ("alt_l", "alt_r", "alt")),
            ("cmd", ("cmd_l", "cmd_r", "cmd")),
        ):
            if any(m.get(s) for s in sides):
                out.append(group)
        if m.get("alt_gr"):
            out.append("alt_gr")
        return out

    def marker_active(self):
        """True while Insert is held, or just after release (grace window).

        The click/drag that lands a heartbeat after the marker key is released
        must still count as marked - hence the grace window.
        """
        if self._marker_down:
            return True
        if (self._marker_up_ts is not None
                and (time.time() - self._marker_up_ts) <= _MARKER_GRACE_S):
            return True
        return False

    def visual_active(self):
        """True while right Ctrl is held: the click records as VISUAL.

        No grace window (see ``_VISUAL_MARKER_SIDES``): the marker must be
        held WHEN the click lands, so releasing it restores the plain click -
        and the hover gesture keeps working.
        """
        return bool(self._visual_down)

    # ------------------------------------------------------------------
    # Event recording
    # ------------------------------------------------------------------

    def handle_keypress(self, key):
        """
        Handle keyboard press events.

        Args:
            key: The pressed key from pynput

        Returns:
            dict, list or None: Action(s) to record, or None when the press
            only mutated internal state (repeat presses) or was buffered as
            part of a typed string.
        """
        try:
            now = time.time()
            name = key_to_name(key)

            # --- Marker key (Insert): a recording GESTURE, never an action ---
            # Held Insert marks a click/drag as a repeating sibling element.
            # Consumed here (like the web recorder's marker key) so it never
            # lands in the sequence as a keystroke.  Mirror the modifier path:
            # flush any pending typed string first so it is not merged oddly.
            if name in _MARKER_NAMES:
                if self._marker_down:
                    return None  # auto-repeat while held
                self._marker_down = True
                self._marker_up_ts = None
                string_action = self.flush_current_string()
                print("MARKER (Insert) down")
                return string_action

            # --- Modifier keys: update state and emit a raw down event ---
            if name in MODIFIER_NAMES:
                side = key.name if isinstance(key, keyboard.Key) else name
                if self.modifiers.get(side):
                    return None  # auto-repeat of a held modifier
                string_action = self.flush_current_string()
                if side in _VISUAL_MARKER_SIDES:
                    # Right Ctrl is a recording GESTURE (visual-match marker):
                    # the hold is tracked (the "move (hover)" capture reads it)
                    # but no key event is recorded, so replay never holds Ctrl
                    # during the visual click.
                    self.modifiers[side] = True
                    self._visual_down = True
                    print("MARKER (Right Ctrl) down - click records as VISUAL match")
                    return string_action
                mods_before = self.active_modifiers()
                self.modifiers[side] = True
                self._down_keys.add(name)
                event = self._key_event(name, "down", now, mods_before)
                return [string_action, event] if string_action else event

            # --- Repeat guard: a key already held only fires once ---
            if name in self._down_keys or name in self._special_down:
                return None

            mods = self.active_modifiers()
            char = getattr(key, "char", None)
            has_printable = char is not None and str(char).isprintable()
            combo_mods = set(mods) & {"ctrl", "alt", "cmd"}

            # --- Legacy clipboard combos (Ctrl+C/V/X/A/Z/Y only) ---
            if set(mods) == {"ctrl"} and name.lower() in _LEGACY_CLIPBOARD_OPS:
                return self._record_clipboard_op(name.lower(), now)

            # --- Typing: printable chars without Ctrl/Alt/Cmd modifiers ---
            if has_printable and not combo_mods:
                self.current_string += str(char)
                return None

            # --- Printable char with modifiers: text vs. shortcut ---
            if has_printable and combo_mods:
                if not str(char).isascii():
                    # AltGr-style text input (e.g. 'ñ') -- treat as typing
                    self.current_string += str(char)
                    return None
                string_action = self.flush_current_string()
                event = self._record_key_event(name, "down", now, mods)
                return [string_action, event] if string_action else event

            # --- Non-printable keys (Enter, F-keys, arrows, control chars) ---
            if mods:
                # Shift+Enter, Ctrl+Tab, Alt+F4, ... -> raw event keeps the
                # modifier context the old keystroke action would lose
                string_action = self.flush_current_string()
                event = self._record_key_event(name, "down", now, mods)
                return [string_action, event] if string_action else event

            # --- Plain special key without modifiers -> legacy keystroke ---
            if name in self._special_down:
                return None  # auto-repeat of a held special key
            self._special_down.add(name)
            string_action = self.flush_current_string()
            action = {
                "type": "keystroke",
                "key": name,
                "timestamp": now,
            }
            print(f"⌨ Key: {name}")
            return [string_action, action] if string_action else action

        except Exception as e:
            print(f"Error in handle_keypress: {e}")
            return None

    def handle_keyrelease(self, key):
        """
        Handle keyboard release events.

        Emits a raw ``key_event`` 'up' for every key whose press was recorded
        as an event, so playback can reproduce press/release order faithfully.
        """
        try:
            now = time.time()
            name = key_to_name(key)

            if name in _MARKER_NAMES:
                if self._marker_down:
                    self._marker_down = False
                    self._marker_up_ts = now
                    print("MARKER (Insert) up")
                return None

            if name in MODIFIER_NAMES:
                side = key.name if isinstance(key, keyboard.Key) else name
                if not self.modifiers.get(side):
                    return None  # release without a tracked press
                if side in _VISUAL_MARKER_SIDES:
                    self.modifiers[side] = False
                    self._visual_down = False
                    print("MARKER (Right Ctrl) up")
                    return None
                mods = [m for m in self.active_modifiers() if m != name]
                self.modifiers[side] = False
                self._down_keys.discard(name)
                return self._key_event(name, "up", now, mods)

            if name in self._down_keys:
                self._down_keys.discard(name)
                mods = self.active_modifiers()
                return self._key_event(name, "up", now, mods)

            if name in self._special_down:
                # Legacy keystroke has no release counterpart - just clear it.
                self._special_down.discard(name)
                return None

            return None
        except Exception as e:
            print(f"Error in handle_keyrelease: {e}")
            return None

    def flush_pending_key_events(self):
        """
        Emit synthetic 'up' events for every key still held (stuck-key safety).

        Called when recording stops so playback never starts with modifiers
        or main keys stuck down.
        """
        events = []
        now = time.time()
        for name in sorted(self._down_keys):
            mods = [m for m in self.active_modifiers() if m != name]
            if name in MODIFIER_NAMES:
                prefix = name + "_"
                for side in [k for k in self.modifiers
                             if k == name or k.startswith(prefix)]:
                    self.modifiers[side] = False
            events.append(self._key_event(name, "up", now, mods))
        self._down_keys.clear()
        self._special_down.clear()
        self._marker_down = False
        self._visual_down = False
        for side in _VISUAL_MARKER_SIDES:
            self.modifiers[side] = False
        return events

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _record_key_event(self, name, state, now, mods):
        """Record a raw key event action and track held state."""
        if state == "down":
            self._down_keys.add(name)
        else:
            self._down_keys.discard(name)
        return self._key_event(name, state, now, mods)

    def _key_event(self, name, state, now, mods):
        """Build the raw key_event action with inter-event timing delta."""
        delta = 0.0
        if self._last_event_time is not None:
            delta = round(max(0.0, now - self._last_event_time), 4)
        self._last_event_time = now
        # ASCII-only print: emojis crash cp1252 Windows consoles and would be
        # swallowed by the handler's except clause, losing key events.
        print(f"KEY {state.upper()}: {name} mods={list(mods)}")
        return {
            "type": "key_event",
            "key": name,
            "state": state,
            "modifiers": list(mods),
            "timestamp": now,
            "delta": delta,
        }

    def _record_clipboard_op(self, operation, now):
        """Legacy semantic clipboard action (backward compatibility)."""
        string_action = self.flush_current_string()
        icon, label = _LEGACY_CLIPBOARD_OPS[operation]
        action = {
            "type": "clipboard",
            "operation": operation,
            "timestamp": now,
        }
        print(f"\n{icon} {label}")
        return [string_action, action] if string_action else action

    def flush_current_string(self):
        """
        Flush the current string buffer and create a type action

        Returns:
            dict or None: Type action if there was text to flush, None otherwise
        """
        if self.current_string:
            action = {
                "type": "type_string",
                "text": self.current_string,
                "timestamp": time.time()
            }
            print(f"🔤 Typed: '{self.current_string}'")
            self.current_string = ""
            return action
        return None
