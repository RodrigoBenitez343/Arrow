"""Self-checks for the OS-cursor glide and the ONE element it aims at.

A web replay used to drive only the browser's virtual pointer, so the
automation read as a teleport while desktop sequences walked the real pointer.
The overlay box and the pointer walk must also describe the element the action
ACTUALLY hits: a recorded css that now also matches a sibling (a "Back" button
where "Next" used to sit) must not move the pointer onto that lookalike.
"""
import sys
import types

from player import human_cursor
from player.web import actions as web_actions
from player.web import engine as web_engine
from player.web import exec_overlay
from player.web.events import Event, Locator


class _FakePyAutoGUI(types.ModuleType):
    def __init__(self):
        super().__init__("pyautogui")
        self.moves = []
        self.easeOutQuad = object()
        self.easeInOutQuad = object()

    def size(self):
        return (1920, 1080)

    def position(self):
        return (100, 100)

    def moveTo(self, x, y, **kwargs):
        self.moves.append((round(x), round(y)))


def _driver(reply):
    class _Driver:
        def execute_script(self, *args):
            return reply

    return _Driver()


def test_glide_walks_a_midpoint_then_the_target(monkeypatch):
    fake = _FakePyAutoGUI()
    monkeypatch.setitem(sys.modules, "pyautogui", fake)
    monkeypatch.delenv("LOOPER_OS_CURSOR", raising=False)
    human_cursor.glide_os_cursor(900, 700)
    assert fake.moves[-1] == (900, 700)
    assert len(fake.moves) >= 2  # a mid path point, not a straight jump


def test_glide_is_parked_by_the_env_switch(monkeypatch):
    fake = _FakePyAutoGUI()
    monkeypatch.setitem(sys.modules, "pyautogui", fake)
    monkeypatch.setenv("LOOPER_OS_CURSOR", "off")
    human_cursor.glide_os_cursor(900, 700)
    assert fake.moves == []


def test_geometry_reply_maps_to_overlay_box_and_screen_point():
    reply = web_actions._geometry_reply([10.0, 20.0, 30.0, 40.0, 100.0, 200.0, 2.0])
    assert reply["rect"] == [10.0, 20.0, 30.0, 40.0]
    # center (25, 40) + client origin (100, 200), scaled by dpr 2
    assert reply["point"] == (250.0, 480.0)


def test_target_geometry_uses_recorded_screen_position_for_chrome():
    chrome = Event(type="chrome", ts=0, action="chrome_click",
                   context={"screen_x": 15, "screen_y": 25})
    assert web_actions.target_geometry(_driver(None), chrome) == {
        "rect": None, "point": (15.0, 25.0)}


def test_target_geometry_without_a_locator_is_none():
    assert web_actions.target_geometry(_driver(None), Event(type="navigate", ts=0)) is None


def test_engine_glides_only_when_it_may_move_the_pointer(monkeypatch):
    moved = []
    monkeypatch.setattr(web_engine, "glide_os_cursor",
                        lambda x, y: moved.append((x, y)))
    engine = web_engine.ReplayEngine(_driver(None), web_engine.ReplayConfig())
    chrome = Event(type="chrome", ts=0, action="chrome_click",
                   context={"screen_x": 15, "screen_y": 25})

    # conftest pins LOOPER_OS_CURSOR=off for the whole suite.
    assert engine._cursor_moves is False
    engine._glide_to_action(chrome)
    assert moved == []

    engine._cursor_moves = True
    engine._glide_to_action(Event(type="key", ts=0, key="a"))  # bursts never glide
    assert moved == []
    engine._glide_to_action(chrome)
    assert moved == [(15.0, 25.0)]


def test_mark_boxes_the_resolved_rect_not_a_lookalike():
    calls = []

    class _Driver:
        def execute_script(self, script, *args):
            calls.append((script, args))
            return True

    event = Event(type="click", ts=0, locator=Locator(tag="button", text="Next"))
    exec_overlay.mark(_Driver(), event, "3: click",
                      {"rect": [10.0, 20.0, 30.0, 40.0], "point": (1.0, 2.0)})
    script, args = calls[0]
    assert script is exec_overlay.JS_EXEC_MARK_RECT
    assert args[0] == [10.0, 20.0, 30.0, 40.0]
    assert "Next" in args[2]  # the recorded identity captions the box
