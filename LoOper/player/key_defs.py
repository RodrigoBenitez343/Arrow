# key_defs.py
"""
Shared canonical keyboard key naming and resolution used by both the
recorder (keyboard_handler) and the player (action_handlers).

Keys are normalized to stable, side-agnostic canonical names so recorded
key events (e.g. Ctrl+Shift+Esc, Win+R, Alt+F4) can be replayed on any
backend without a hard-coded shortcut table:

  - modifiers:        ctrl, shift, alt, cmd, alt_gr
  - special keys:     enter, tab, esc, up, f5, media_play_pause, ...
  - printable chars:  the character itself ('a', '2', 'é')
  - unresolved codes: 'vk_<vk>' (resolved via pynput virtual key code)

The player side resolves canonical names back into pynput keys with
``pynput_key_for()``, so no other module needs pynput-specific knowledge.
"""

from pynput import keyboard

# Canonical modifier names. Side variants (ctrl_l / ctrl_r / ctrl) collapse
# into these so a chord is described once regardless of which side was used.
MODIFIER_NAMES = frozenset(("ctrl", "shift", "alt", "cmd", "alt_gr"))

# pynput Key.name -> canonical name. Identical for most special keys;
# modifiers collapse their side variants here.
SPECIAL_KEY_MAP = {k.name: k.name for k in keyboard.Key}
SPECIAL_KEY_MAP.update({
    "ctrl_l": "ctrl", "ctrl_r": "ctrl",
    "shift_l": "shift", "shift_r": "shift",
    "alt_l": "alt", "alt_r": "alt",
    "cmd_l": "cmd", "cmd_r": "cmd",
})

# Canonical name -> pynput key for playback. Generic modifier variants are
# preferred when sending so the OS applies the modifier regardless of side.
_CANONICAL_TO_KEY = {}
for _enum_key in keyboard.Key:
    _canonical = SPECIAL_KEY_MAP.get(_enum_key.name, _enum_key.name)
    _CANONICAL_TO_KEY.setdefault(_canonical, _enum_key)
_CANONICAL_TO_KEY.update({
    "ctrl": keyboard.Key.ctrl,
    "shift": keyboard.Key.shift,
    "alt": keyboard.Key.alt,
    "cmd": keyboard.Key.cmd,
    "alt_gr": keyboard.Key.alt_gr,
})

# ASCII control characters -> canonical key name. With Ctrl held, pynput
# reports letters as their control codes (Ctrl+C == '\x03'), so this maps
# them back to the letter name (vk codes cover the same ground).
_CONTROL_CHAR_TO_NAME = {
    "\x01": "a", "\x02": "b", "\x03": "c", "\x04": "d", "\x05": "e",
    "\x06": "f", "\x07": "g", "\x08": "backspace", "\x09": "tab",
    "\x0a": "j", "\x0b": "k", "\x0c": "l", "\x0d": "enter", "\x0e": "n",
    "\x0f": "o", "\x10": "p", "\x11": "q", "\x12": "r", "\x13": "s",
    "\x14": "t", "\x15": "u", "\x16": "v", "\x17": "w", "\x18": "x",
    "\x19": "y", "\x1a": "z", "\x7f": "delete",
}


def key_to_name(key):
    """
    Return the canonical name for a pynput key (or duck-typed stand-in).

    Handles ``Key`` enums, ``KeyCode`` objects (char / vk based) and plain
    objects with ``char`` / ``vk`` attributes, plus a string fallback for
    anything else (tests, older callers).
    """
    if isinstance(key, keyboard.Key):
        name = key.name
        return SPECIAL_KEY_MAP.get(name, name)

    char = getattr(key, "char", None)
    vk = getattr(key, "vk", None)
    if isinstance(key, keyboard.KeyCode) or char is not None or vk is not None:
        if char is not None and str(char).isprintable():
            return str(char)
        if char is not None:
            name = _CONTROL_CHAR_TO_NAME.get(str(char))
            if name:
                return name
        if vk is not None:
            try:
                vk = int(vk)
            except (TypeError, ValueError):
                vk = None
            if vk is not None:
                if 65 <= vk <= 90:  # VK_A..VK_Z
                    return chr(vk).lower()
                return "vk_%s" % vk
        if char is not None:
            return "control_%s" % ord(str(char))
        return "unknown"

    # String fallback for non-pynput stand-ins (e.g. test fakes).
    s = str(key)
    if s.startswith("Key."):
        return SPECIAL_KEY_MAP.get(s[4:], s[4:])
    if len(s) >= 2 and s[0] == "'" and s[-1] == "'":
        return s[1:-1]
    return s


def is_modifier_key(key):
    """Return True when *key* is a modifier (ctrl/shift/alt/cmd/alt_gr)."""
    return key_to_name(key) in MODIFIER_NAMES


def is_modifier_name(name):
    """Return True when *name* is a canonical modifier name."""
    return name in MODIFIER_NAMES


def pynput_key_for(name):
    """
    Resolve a canonical key name back to a pynput key for playback.

    Returns None when the name cannot be resolved (callers then skip it).
    """
    if not name:
        return None
    key = _CANONICAL_TO_KEY.get(name)
    if key is not None:
        return key
    if name.startswith("vk_"):
        try:
            return keyboard.KeyCode.from_vk(int(name[3:]))
        except (TypeError, ValueError):
            return None
    if len(name) == 1:
        return keyboard.KeyCode.from_char(name)
    return None
