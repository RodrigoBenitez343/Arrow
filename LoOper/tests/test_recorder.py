"""Unit tests for the recorder subtree:
- keyboard_handler.py (KeyboardHandler)
- mouse_handler.py (MouseHandler)
- scroll_manager.py (ScrollManager)
- sequence_manager.py (SequenceManager)

NOTE: recorder modules are imported as ``LoOper.recorder.*`` because their
internal relative imports (``from ..player.image_utils import ...``) fail
when imported as a top-level package — see the importability test in
test_data_tree.py.
"""

import io
import json
import time

import pytest
from PIL import Image
from pynput import keyboard as pynput_keyboard
from pynput import mouse as pynput_mouse

from LoOper.recorder.keyboard_handler import KeyboardHandler
from LoOper.recorder.mouse_handler import MouseHandler
from LoOper.recorder.scroll_manager import ScrollManager
from LoOper.recorder.sequence_manager import SequenceManager


class FakeKey:
    """Minimal pynput key stand-in."""

    def __init__(self, char=None, vk=None):
        self.char = char
        self.vk = vk

    def __str__(self):
        return "Key.enter" if self.char is None and self.vk is None else f"'{self.char}'"


# ---------------------------------------------------------------------------
# KeyboardHandler
# ---------------------------------------------------------------------------


def test_modifier_tracking():
    kh = KeyboardHandler()
    assert not kh.is_ctrl_down()
    kh.handle_keypress(pynput_keyboard.Key.ctrl_l)
    assert kh.is_ctrl_down()
    kh.handle_keyrelease(pynput_keyboard.Key.ctrl_l)
    assert not kh.is_ctrl_down()


def test_all_modifier_keys():
    """Modifier presses emit raw key_event actions (side-agnostic).

    Right Ctrl is the exception - it is the visual-match recording GESTURE and
    is consumed (see test_right_ctrl_is_a_visual_match_marker).
    """
    kh = KeyboardHandler()
    for key in (pynput_keyboard.Key.ctrl_l,
                pynput_keyboard.Key.shift_l, pynput_keyboard.Key.shift_r,
                pynput_keyboard.Key.alt_l, pynput_keyboard.Key.alt_r):
        action = kh.handle_keypress(key)
        assert action is not None
        assert action["type"] == "key_event"
        assert action["state"] == "down"
    assert kh.is_ctrl_down() and kh.is_shift_down() and kh.is_alt_down()
    for key in (pynput_keyboard.Key.ctrl_l,
                pynput_keyboard.Key.shift_l, pynput_keyboard.Key.shift_r,
                pynput_keyboard.Key.alt_l, pynput_keyboard.Key.alt_r):
        action = kh.handle_keyrelease(key)
        assert action is not None
        assert action["type"] == "key_event"
        assert action["state"] == "up"
    assert not (kh.is_ctrl_down() or kh.is_shift_down() or kh.is_alt_down())


def test_right_ctrl_is_a_visual_match_marker_not_a_key_event():
    """Holding right Ctrl records the click as VISUAL: the gesture is consumed
    (no key_event, so replay never holds Ctrl during the click) but its hold IS
    tracked in the modifiers - the 'right Ctrl + move' hover capture reads it.
    """
    kh = KeyboardHandler()
    assert kh.handle_keypress(pynput_keyboard.Key.ctrl_r) is None
    assert kh.handle_keypress(pynput_keyboard.Key.ctrl_r) is None  # auto-repeat
    assert kh.visual_active() is True
    assert kh.modifiers.get("ctrl_r") is True
    assert kh.handle_keyrelease(pynput_keyboard.Key.ctrl_r) is None
    # No grace window: a released marker means hover/plain click, not visual.
    assert kh.visual_active() is False
    assert kh.handle_keyrelease(pynput_keyboard.Key.ctrl_r) is None  # stray release


def test_flush_pending_key_events_clears_the_visual_marker():
    kh = KeyboardHandler()
    kh.handle_keypress(pynput_keyboard.Key.ctrl_r)
    kh.flush_pending_key_events()
    assert kh.visual_active() is False
    assert kh.modifiers.get("ctrl_r") is False


def test_modifier_repeat_press_ignored():
    kh = KeyboardHandler()
    assert kh.handle_keypress(pynput_keyboard.Key.ctrl_l) is not None
    assert kh.handle_keypress(pynput_keyboard.Key.ctrl_l) is None  # auto-repeat


def test_plain_special_key_repeat_press_ignored():
    """Holding a plain special key (Home) must not flood the session: OS
    auto-repeat yields ONE action, and the release emits nothing.

    (Insert is no longer a plain key - it is the repeating-element recording
    marker, pinned by test_uia_locator.test_marker_active_holds_and_has_a_grace_window.)
    """
    kh = KeyboardHandler()
    first = kh.handle_keypress(pynput_keyboard.Key.home)
    assert first is not None and first["type"] == "keystroke"
    assert kh.handle_keypress(pynput_keyboard.Key.home) is None
    assert kh.handle_keypress(pynput_keyboard.Key.home) is None
    assert kh.handle_keyrelease(pynput_keyboard.Key.home) is None
    # a fresh press after release is recorded again
    assert kh.handle_keypress(pynput_keyboard.Key.home) is not None
    assert kh.handle_keyrelease(pynput_keyboard.Key.home) is None


def test_arbitrary_combo_emits_key_event_with_modifiers():
    """Ctrl+Shift+Esc is captured generically with the full modifier snapshot."""
    kh = KeyboardHandler()
    kh.handle_keypress(pynput_keyboard.Key.ctrl_l)
    kh.handle_keypress(pynput_keyboard.Key.shift_l)
    action = kh.handle_keypress(pynput_keyboard.Key.esc)
    assert action["type"] == "key_event"
    assert action["key"] == "esc"
    assert action["state"] == "down"
    assert set(action["modifiers"]) == {"ctrl", "shift"}
    # Release order is preserved with correct modifier snapshots.
    up_esc = kh.handle_keyrelease(pynput_keyboard.Key.esc)
    assert set(up_esc["modifiers"]) == {"ctrl", "shift"}
    up_shift = kh.handle_keyrelease(pynput_keyboard.Key.shift_l)
    assert set(up_shift["modifiers"]) == {"ctrl"}
    up_ctrl = kh.handle_keyrelease(pynput_keyboard.Key.ctrl_l)
    assert up_ctrl["modifiers"] == []


def test_win_key_combo():
    """Windows key is tracked and Win+R is recorded as a raw chord."""
    kh = KeyboardHandler()
    kh.handle_keypress(pynput_keyboard.Key.cmd_l)
    assert kh.is_cmd_down()
    action = kh.handle_keypress(FakeKey(char="r"))
    assert action["type"] == "key_event"
    assert action["key"] == "r"
    assert action["modifiers"] == ["cmd"]
    kh.handle_keyrelease(pynput_keyboard.Key.cmd_l)
    assert not kh.is_cmd_down()


def test_ctrl_letter_not_in_legacy_set_is_key_event():
    """Ctrl+T (no legacy clipboard mapping) is recorded as a raw chord."""
    kh = KeyboardHandler()
    kh.handle_keypress(pynput_keyboard.Key.ctrl_l)
    action = kh.handle_keypress(FakeKey(vk=84))  # VK_T
    assert action["type"] == "key_event"
    assert action["key"] == "t"
    assert action["modifiers"] == ["ctrl"]


def test_shift_enter_combo_is_key_event():
    """Shift+Enter keeps its modifier context (legacy keystroke would lose it)."""
    kh = KeyboardHandler()
    kh.handle_keypress(pynput_keyboard.Key.shift_l)
    action = kh.handle_keypress(pynput_keyboard.Key.enter)
    assert action["type"] == "key_event"
    assert action["key"] == "enter"
    assert action["modifiers"] == ["shift"]


def test_shift_typing_still_buffers():
    """Shift + letter is typing, not a shortcut -- stays buffered as text."""
    kh = KeyboardHandler()
    kh.handle_keypress(pynput_keyboard.Key.shift_l)
    kh.handle_keypress(FakeKey(char="H"))
    kh.handle_keyrelease(pynput_keyboard.Key.shift_l)
    action = kh.flush_current_string()
    assert action["type"] == "type_string"
    assert action["text"] == "H"


def test_key_event_has_delta():
    """Inter-event timing is recorded for faithful replay pacing."""
    kh = KeyboardHandler()
    first = kh.handle_keypress(pynput_keyboard.Key.ctrl_l)
    assert first["delta"] == 0.0
    time.sleep(0.02)
    second = kh.handle_keypress(pynput_keyboard.Key.shift_l)
    assert second["delta"] >= 0.015


def test_stuck_key_flush_emits_up_events():
    """Keys still held when recording stops are flushed as synthetic ups."""
    kh = KeyboardHandler()
    kh.handle_keypress(pynput_keyboard.Key.ctrl_l)
    kh.handle_keypress(pynput_keyboard.Key.shift_l)
    kh.handle_keypress(pynput_keyboard.Key.f5)
    events = kh.flush_pending_key_events()
    assert len(events) == 3
    assert all(e["state"] == "up" for e in events)
    assert kh.active_modifiers() == []
    assert not kh._down_keys


def test_typing_buffers_string():
    kh = KeyboardHandler()
    kh.handle_keypress(FakeKey(char="h"))
    kh.handle_keypress(FakeKey(char="i"))
    action = kh.flush_current_string()
    assert action == {"type": "type_string", "text": "hi", "timestamp": action["timestamp"]}
    assert kh.flush_current_string() is None  # empty buffer


def test_ctrl_c_detected_via_char_code():
    kh = KeyboardHandler()
    kh.handle_keypress(pynput_keyboard.Key.ctrl_l)
    action = kh.handle_keypress(FakeKey(char="\x03"))  # ETX
    if isinstance(action, list):
        action = action[-1]
    assert action["type"] == "clipboard"
    assert action["operation"] == "c"


def test_ctrl_c_detected_via_vk():
    kh = KeyboardHandler()
    kh.handle_keypress(pynput_keyboard.Key.ctrl_l)
    action = kh.handle_keypress(FakeKey(vk=67))  # VK_C
    if isinstance(action, list):
        action = action[-1]
    assert action["operation"] == "c"


@pytest.mark.parametrize("vk,expected", [(88, "x"), (86, "v"), (65, "a"), (90, "z"), (89, "y")])
def test_ctrl_combos_via_vk(vk, expected):
    kh = KeyboardHandler()
    kh.handle_keypress(pynput_keyboard.Key.ctrl_l)
    action = kh.handle_keypress(FakeKey(vk=vk))
    if isinstance(action, list):
        action = action[-1]
    assert action["operation"] == expected


def test_control_char_key_creates_keystroke():
    kh = KeyboardHandler()
    action = kh.handle_keypress(FakeKey(char="\x7f"))  # DEL
    if isinstance(action, list):
        action = action[-1]
    assert action["type"] == "keystroke"


def test_non_printable_key_creates_keystroke():
    kh = KeyboardHandler()
    action = kh.handle_keypress(FakeKey())
    if isinstance(action, list):
        action = action[-1]
    assert action["type"] == "keystroke"
    assert "enter" in action["key"].lower() or action["key"] == "enter"


def test_handle_keypress_plain_object_becomes_keystroke():
    """Non-modifier, non-char keys are recorded as keystroke actions."""
    kh = KeyboardHandler()
    action = kh.handle_keypress(object())
    if isinstance(action, list):
        action = action[-1]
    assert action["type"] == "keystroke"


# ---------------------------------------------------------------------------
# MouseHandler
# ---------------------------------------------------------------------------


def test_click_creates_action():
    mh = MouseHandler()
    mh.handle_mouse_press(100, 100, pynput_mouse.Button.left, True)
    action = mh.handle_mouse_release(101, 101, pynput_mouse.Button.left)
    assert action["type"] == "click"
    assert action["coordinates"] == {"x": 101, "y": 101}
    assert action["needs_screenshot"] is True


def test_drag_creates_drag_drop():
    mh = MouseHandler()
    mh.handle_mouse_press(100, 100, pynput_mouse.Button.left, True)
    action = mh.handle_mouse_release(150, 120, pynput_mouse.Button.left)
    assert action["type"] == "drag_drop"
    assert action["from"] == {"x": 100, "y": 100}
    assert action["to"] == {"x": 150, "y": 120}


def test_release_without_press_returns_none():
    mh = MouseHandler()
    assert mh.handle_mouse_release(10, 10, pynput_mouse.Button.left) is None


def test_double_click_detection():
    mh = MouseHandler()
    mh.handle_mouse_press(10, 10, pynput_mouse.Button.left, True)
    first = mh.handle_mouse_release(11, 11, pynput_mouse.Button.left)
    assert first["type"] == "click"
    time.sleep(0.01)
    mh.handle_mouse_press(11, 11, pynput_mouse.Button.left, True)
    second = mh.handle_mouse_release(12, 12, pynput_mouse.Button.left)
    assert second["type"] == "double_click"


def test_right_click():
    mh = MouseHandler()
    mh.handle_mouse_press(10, 10, pynput_mouse.Button.right, True)
    action = mh.handle_mouse_release(10, 10, pynput_mouse.Button.right)
    assert action["type"] == "click"
    assert action["button"] == "right"


def test_button_name_string_input():
    mh = MouseHandler()
    assert mh._is_left_button("left") is True
    assert mh._is_left_button(pynput_mouse.Button.right) is False
    assert mh._get_button_name("RIGHT") == "right"


# ---------------------------------------------------------------------------
# ScrollManager
# ---------------------------------------------------------------------------


def test_first_scroll_starts_burst_returns_none():
    sm = ScrollManager()
    assert sm.process_scroll(0, 0, 0, 1) is None
    assert sm._current_scroll_burst is not None


def test_burst_accumulates_and_finalizes():
    sm = ScrollManager()
    sm.process_scroll(0, 0, 0, 1)
    sm.process_scroll(0, 0, 0, 1)
    action = sm.finalize_scroll_burst()
    assert action["type"] == "scroll"
    assert action["total_delta"] == 100  # 2 notches * 50px
    assert action["direction"] == "up"
    assert action["steps"] == 2


def test_direction_change_finalizes_previous():
    sm = ScrollManager()
    sm.process_scroll(0, 0, 0, 1)
    action = sm.process_scroll(0, 0, 0, -1)  # opposite direction
    assert action is not None
    assert action["direction"] == "up"


def test_finalize_without_burst_returns_none():
    sm = ScrollManager()
    assert sm.finalize_scroll_burst() is None


# ---------------------------------------------------------------------------
# SequenceManager
# ---------------------------------------------------------------------------


def test_add_action_single_and_list():
    sm = SequenceManager()
    sm.add_action({"type": "click"})
    sm.add_action([{"type": "wait"}, {"type": "scroll"}])
    sm.add_action(None)
    assert sm.get_action_count() == 3


def test_attach_app_context_skips_existing(monkeypatch):
    sm = SequenceManager()
    monkeypatch.setattr(sm, "_get_active_app_context", lambda: {"title": "Chrome"})
    action = sm._attach_app_context({"type": "click", "app_context": {"title": "Old"}})
    assert action["app_context"] == {"title": "Old"}


def test_attach_app_context_adds_new(monkeypatch):
    sm = SequenceManager()
    monkeypatch.setattr(sm, "_get_active_app_context", lambda: {"title": "Chrome"})
    action = sm._attach_app_context({"type": "click"})
    assert action["app_context"] == {"title": "Chrome"}


def test_save_sequence_writes_file(tmp_path, monkeypatch):
    sm = SequenceManager()
    sm.add_action({"type": "click", "coordinates": {"x": 1, "y": 1}})
    monkeypatch.setattr("LoOper.recorder.sequence_manager.clear_cache", lambda: None)
    out = tmp_path / "out.json"
    sm.save_sequence(str(out))
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["metadata"]["total_actions"] == 1
    assert data["actions"][0]["type"] == "click"


def test_save_sequence_dedups_screenshots(tmp_path, monkeypatch):
    sm = SequenceManager()
    sm.add_action({"type": "click", "screenshot_data": "AAAA"})
    sm.add_action({"type": "click", "screenshot_data": "AAAA"})  # duplicate content
    monkeypatch.setattr("LoOper.recorder.sequence_manager.clear_cache", lambda: None)
    out = tmp_path / "out.json"
    sm.save_sequence(str(out))
    data = json.loads(out.read_text(encoding="utf-8"))
    assert len(data["images"]) == 1
    assert all(a["screenshot_hash"] for a in data["actions"])
    assert all("screenshot_data" not in a for a in data["actions"])


def test_save_sequence_embeds_file_screenshots(tmp_path, monkeypatch):
    img = tmp_path / "shot.png"
    Image.new("RGB", (4, 4), color=(255, 0, 0)).save(img)
    sm = SequenceManager()
    sm.add_action({"type": "click", "screenshot": str(img)})
    monkeypatch.setattr("LoOper.recorder.sequence_manager.clear_cache", lambda: None)
    out = tmp_path / "out.json"
    sm.save_sequence(str(out))
    data = json.loads(out.read_text(encoding="utf-8"))
    action = data["actions"][0]
    # The inline base64 is replaced by a hash reference; the original path
    # key is kept by save_sequence for traceability.
    assert "screenshot_hash" in action
    assert "screenshot_data" not in action
    assert len(data["images"]) == 1


def test_clear_actions():
    sm = SequenceManager()
    sm.add_action({"type": "click"})
    sm.clear_actions()
    assert sm.get_action_count() == 0
