"""Unit tests for the OS-level web capture prototype (player/web/os_capture.py).

The desktop handlers (KeyboardHandler/MouseHandler/ScrollManager) and the
pynput listeners are environment-dependent; the translations under test are
pure logic driven by queued action dicts, so the capture is built with
``object.__new__`` (no pynput / pyautogui imports) and the driver-facing
helpers (_hit_test_locator / _focused_editable / _current_url) are
monkeypatched.  The chrome:// page drop is recorder._drop_chrome_internal_
actions, already covered by test_web_sequence_integration.py.
"""
import threading
import types

import pytest

from player.web import os_capture as oc
from player.web.events import Event


def _capture(monkeypatch, **overrides):
    """A light OsWebCapture with fake geometry and no OS listeners."""
    cap = object.__new__(oc.OsWebCapture)
    cap.driver = types.SimpleNamespace(current_url="https://example.com/feed")
    cap.scale = 1.0
    cap.content_offset_px = 100.0  # toolbar+tabstrip height, physical px
    cap.events = []
    cap._queue = []
    cap._lock = threading.Lock()
    cap._stop = None
    cap._geom_done = True
    cap._recorded_count = 0
    cap._last_input_time = 0.0
    cap._editable_snapshot = None
    cap._window_rect_cb = lambda: (0, 0, 1280, 800)
    cap._kh = types.SimpleNamespace(current_string="", modifiers={})
    monkeypatch.setattr(cap, "_hit_test_locator",
                        lambda cx, cy: {"tag": "button", "id": "go"})
    monkeypatch.setattr(cap, "_focused_editable", lambda: None)
    for key, value in overrides.items():
        setattr(cap, key, value)
    return cap


def test_web_key_mapping():
    assert oc._web_key("enter") == "Enter"
    assert oc._web_key("f5") == "F5"
    assert oc._web_key("page_down") == "PageDown"
    assert oc._web_key("a") == "a"
    assert oc._web_key("é") == "é"
    assert oc._web_key("vk_91") is None
    assert oc._web_key("") is None


def test_web_mods_mapping():
    assert oc._web_mods(["ctrl", "shift", "cmd", "alt_gr"]) == [
        "Ctrl", "Shift", "Meta", "AltGraph",
    ]
    assert oc._web_mods([]) == []


def test_translate_click_records_element_with_locator(monkeypatch):
    cap = _capture(monkeypatch)
    cap._translate_mouse({
        "type": "click", "button": "left",
        "coordinates": {"x": 300, "y": 250},
    })
    assert len(cap.events) == 1
    evt = cap.events[0]
    assert evt.type == "click" and evt.button == "0"
    assert evt.locator.id == "go" and evt.locator.tag == "button"
    assert evt.url == "https://example.com/feed"


def test_toolbar_click_back_is_chrome_action(monkeypatch):
    cap = _capture(monkeypatch)
    cap._translate_mouse({  # y=40 is inside the toolbar band (content at 100)
        "type": "click", "button": "left",
        "coordinates": {"x": 20, "y": 40},
    })
    assert len(cap.events) == 1
    assert cap.events[0].type == "chrome"
    assert cap.events[0].action == "back"


def test_toolbar_click_newtab_button_is_setup_not_recorded(monkeypatch):
    cap = _capture(monkeypatch)
    cap._translate_mouse({  # right-side toolbar click (tab strip / + button)
        "type": "click", "button": "left",
        "coordinates": {"x": 1200, "y": 40},
    })
    assert cap.events == []


def test_ctrl_click_carries_modifier_and_retype(monkeypatch):
    cap = _capture(monkeypatch)
    cap._kh = types.SimpleNamespace(
        current_string="", modifiers={"ctrl_l": True}
    )
    action = cap._retype_with_modifiers({
        "type": "click", "button": "left",
        "coordinates": {"x": 300, "y": 250},
    })
    assert action["type"] == "ctrl_click"
    cap._translate_mouse(action)
    assert cap.events[0].type == "click"
    assert cap.events[0].modifiers == ["Ctrl"]


def test_alt_click_relative_reference_is_dropped(monkeypatch):
    cap = _capture(monkeypatch)
    cap._kh = types.SimpleNamespace(
        current_string="", modifiers={"alt_l": True}
    )
    action = cap._retype_with_modifiers({
        "type": "click", "button": "left",
        "coordinates": {"x": 300, "y": 250},
    })
    assert action is None  # Alt+click needs a desktop reference - no web twin


def test_right_click_and_double_click_translate(monkeypatch):
    cap = _capture(monkeypatch)
    cap._translate_mouse({
        "type": "click", "button": "right",
        "coordinates": {"x": 300, "y": 250},
    })
    cap._translate_mouse({
        "type": "double_click", "button": "left",
        "coordinates": {"x": 310, "y": 260},
    })
    assert [e.type for e in cap.events] == ["contextmenu", "dblclick"]
    assert cap.events[0].button == "2"


def test_drag_translates_with_drop_context(monkeypatch):
    cap = _capture(monkeypatch)
    cap._translate_mouse({
        "type": "drag_drop",
        "from": {"x": 200, "y": 300},
        "to": {"x": 400, "y": 320},
    })
    assert len(cap.events) == 1
    evt = cap.events[0]
    assert evt.type == "drag"
    assert evt.context["drop_x"] == 400 and evt.context["drop_y"] == 320
    assert evt.coordinates == {"x": 200, "y": 300}


def test_typing_flush_only_on_editable_focus(monkeypatch):
    cap = _capture(monkeypatch)
    cap._kh = types.SimpleNamespace(
        current_string="hello",
        flush_current_string=lambda: "hello",
    )
    # Non-editable focus (address bar / body) -> dropped as browser setup.
    monkeypatch.setattr(
        cap, "_focused_editable",
        lambda: {"editable": False, "locator": {"tag": "body"}},
    )
    cap._flush_typing()
    assert cap.events == []

    # Editable focus -> a type event bound to the focused element.
    monkeypatch.setattr(
        cap, "_focused_editable",
        lambda: {"editable": True,
                 "locator": {"tag": "textarea", "id": "input"}},
    )
    cap._flush_typing()
    assert len(cap.events) == 1
    assert cap.events[0].type == "type"
    assert cap.events[0].value == "hello"
    assert cap.events[0].locator.id == "input"


def test_key_translation_maps_desktop_names(monkeypatch):
    cap = _capture(monkeypatch)
    cap._kh = types.SimpleNamespace(
        current_string="",
        flush_current_string=lambda: None,
    )
    monkeypatch.setattr(
        cap, "_focused_editable",
        lambda: {"editable": True,
                 "locator": {"tag": "input", "name": "q"}},
    )
    cap._translate_key([
        {"type": "type_string", "text": "news"},
        {"type": "key_event", "key": "enter", "state": "down",
         "modifiers": []},
        {"type": "key_event", "key": "enter", "state": "up",
         "modifiers": []},
    ])
    assert [e.type for e in cap.events] == ["type", "key", "key"]
    assert cap.events[1].key == "Enter" and cap.events[1].state == "down"
    assert cap.events[0].value == "news"


def test_clipboard_op_becomes_ctrl_chord(monkeypatch):
    cap = _capture(monkeypatch)
    cap._translate_key({"type": "clipboard", "operation": "copy"})
    assert len(cap.events) == 1
    evt = cap.events[0]
    assert evt.type == "key" and evt.key == "c"
    assert evt.modifiers == ["Ctrl"]


def test_scroll_translation_keeps_burst_delta(monkeypatch):
    cap = _capture(monkeypatch)
    cap._translate_scroll({
        "type": "scroll", "total_delta": 500, "steps": 5,
        "start": {"x": 300, "y": 250}, "end": {"x": 300, "y": 250},
        "timestamp": 1000.0,
    })
    assert len(cap.events) == 1
    evt = cap.events[0]
    assert evt.type == "scroll"
    # ScrollManager keeps the OS wheel sign (dy > 0 = wheel away = up on
    # Windows); the web contract (recorder.js deltaY / JS_SCROLL) treats a
    # positive delta as scrolling DOWN, so the translation negates it.
    assert evt.scroll["total_delta"] == -500
    assert evt.scroll["steps"] == 5
    assert evt.locator.id == "go"
    assert evt.ts == 1000.0 * 1000.0  # payload capture time, not translate time


def test_drop_chrome_internal_applies_to_os_events():
    """OS-captured actions on chrome:// pages are cleaned like DOM ones."""
    from player.web.recorder import _drop_chrome_internal_actions

    internal = Event(type="click", ts=1.0, url="chrome://new-tab-page/")
    real = Event(type="click", ts=2.0, url="https://example.com/feed")
    kept = _drop_chrome_internal_actions([internal, real])
    assert kept == [real]


def test_marker_key_is_never_recorded():
    """Insert is a pure recording marker - the OS hook must filter it out so a
    held marker never becomes a key action."""
    assert oc._is_marker_key(types.SimpleNamespace(name="insert")) is True
    assert oc._is_marker_key(types.SimpleNamespace(name="insert".upper())) is False
    assert oc._is_marker_key(types.SimpleNamespace(name="a")) is False
    assert oc._is_marker_key(types.SimpleNamespace(name=None)) is False
